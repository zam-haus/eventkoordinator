"""Review-request helpers shared by the flow engine and the reviews router."""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

from apiv1.flows import call_from_email
from apiv1.models import Proposal, ProposalReview
from apiv1.models.basedata import ProposalAreaReviewGroup

logger = logging.getLogger(__name__)


def send_review_requested_mail(proposal: Proposal, reviewer) -> None:
    """Notify a reviewer that they have been asked to review a proposal."""
    proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{proposal.pk}"
    ctx = dict(object=proposal, proposal_url=proposal_url, reviewer=reviewer)
    try:
        send_mail(
            subject=f"Bitte um Gutachten / Review requested: {proposal.title}",
            message=render_to_string("apiv1/mails/review_requested.txt.j2", ctx),
            html_message=render_to_string("apiv1/mails/review_requested.html.j2", ctx),
            from_email=call_from_email(proposal.call),
            recipient_list=[reviewer.email],
            fail_silently=False,
        )
    except BaseException as e:
        logger.error("Failed to send review-requested notification: " + str(e), exc_info=e)


def auto_request_area_reviews(proposal: Proposal, requested_by=None) -> list[ProposalReview]:
    """Create the group-review requests configured for the proposal's area.

    Mappings are maintained in the Django admin (ProposalAreaReviewGroup). Each
    mapping creates one group request, blocking or optional as configured.
    Existing requests for the same group are left untouched (their blocking flag
    may have been adjusted by hand), so this is safe to call on every submission.
    """
    if not proposal.area_id:
        return []

    mappings = list(
        ProposalAreaReviewGroup.objects.filter(area_id=proposal.area_id).select_related("group")
    )
    if not mappings:
        return []

    existing_codes = set(
        ProposalReview.objects.filter(
            proposal=proposal, kind=ProposalReview.KIND_GROUP
        ).values_list("group_code", flat=True)
    )

    created: list[ProposalReview] = []
    for mapping in mappings:
        group_code = str(mapping.group_id)
        if group_code in existing_codes:
            continue
        review = ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_GROUP,
            group_code=group_code,
            is_blocking=mapping.is_blocking,
            requested_by=requested_by,
            requested_at=timezone.now(),
        )
        created.append(review)
        logger.info(
            "Auto-requested %s review from group %r for proposal %s",
            "blocking" if mapping.is_blocking else "optional",
            mapping.group.name,
            proposal.pk,
        )
        # Link votes group members may already have cast to this new request.
        member_pks = list(mapping.group.user_set.values_list("pk", flat=True))
        to_update = []
        for member_review in ProposalReview.objects.filter(
            proposal=proposal, kind=ProposalReview.KIND_USER, reviewer__in=member_pks
        ):
            if group_code not in (member_review.requested_via_groups or []):
                member_review.requested_via_groups = (
                    member_review.requested_via_groups or []
                ) + [group_code]
                to_update.append(member_review)
        if to_update:
            ProposalReview.objects.bulk_update(to_update, ["requested_via_groups"])

        for member in mapping.group.user_set.filter(email__gt=""):
            send_review_requested_mail(proposal, member)

    return created
