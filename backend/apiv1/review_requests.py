"""Review-request helpers shared by the flow engine and the reviews router."""

import logging
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
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


def proposal_reviewers(proposal: Proposal) -> list:
    """All users requested to review the proposal or who did review it, deduplicated.

    Covers direct requests and votes (user reviews) as well as the current
    members of every requested group. The system review has no reviewer.
    """
    reviews = list(ProposalReview.objects.filter(proposal=proposal).select_related("reviewer"))
    users: dict[Any, Any] = {}
    for review in reviews:
        if review.kind == ProposalReview.KIND_USER and review.reviewer is not None:
            users.setdefault(review.reviewer.pk, review.reviewer)
    group_pks = [
        int(r.group_code)
        for r in reviews
        if r.kind == ProposalReview.KIND_GROUP and r.group_code.isdigit()
    ]
    for group in Group.objects.filter(pk__in=group_pks):
        for member in group.user_set.all():
            users.setdefault(member.pk, member)
    return list(users.values())


def send_proposal_accepted_reviewer_mails(proposal: Proposal) -> None:
    """Inform every requested and actual reviewer that the proposal was accepted."""
    proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{proposal.pk}"
    for reviewer in proposal_reviewers(proposal):
        if not reviewer.email:
            continue
        ctx = dict(object=proposal, proposal_url=proposal_url, reviewer=reviewer)
        try:
            send_mail(
                subject=f"Einreichung angenommen / Submission accepted: {proposal.title}",
                message=render_to_string("apiv1/mails/accept_reviewer.txt.j2", ctx),
                html_message=render_to_string("apiv1/mails/accept_reviewer.html.j2", ctx),
                from_email=call_from_email(proposal.call),
                recipient_list=[reviewer.email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error("Failed to send acceptance notification to reviewer: " + str(e), exc_info=e)


def sync_group_review_links(proposal: Proposal, reviews: list[ProposalReview] | None = None) -> bool:
    """Link every user review to the requested groups its reviewer belongs to.

    ``ProposalReview.requested_via_groups`` is what ties an individual vote to a
    group request, and it is stored on the vote. Membership can change after the
    vote was cast (or after a group request was added), so the stored links are
    recomputed here from the current memberships. Links are only added, never
    removed: withdrawing a group request clears its links explicitly.

    Returns True if anything changed.
    """
    if reviews is None:
        reviews = list(ProposalReview.objects.filter(proposal=proposal))

    # Map the pk of every requested group to the code the reviews store it under.
    code_by_pk = {
        int(r.group_code): r.group_code
        for r in reviews
        if r.kind == ProposalReview.KIND_GROUP and r.group_code.isdigit()
    }
    if not code_by_pk:
        return False

    memberships: dict[Any, set[str]] = {}
    for group_pk, user_pk in Group.objects.filter(pk__in=code_by_pk).values_list(
        "pk", "user__pk"
    ):
        if user_pk is not None:
            memberships.setdefault(user_pk, set()).add(code_by_pk[group_pk])

    to_update = []
    for review in reviews:
        if review.kind != ProposalReview.KIND_USER or review.reviewer_id is None:
            continue
        current = list(review.requested_via_groups or [])
        missing = sorted(memberships.get(review.reviewer_id, set()) - set(current))
        if missing:
            review.requested_via_groups = current + missing
            to_update.append(review)
            logger.info(
                "Linked review %s by %s to group request(s) %s on proposal %s",
                review.pk,
                review.reviewer_id,
                missing,
                proposal.pk,
            )

    if to_update:
        ProposalReview.objects.bulk_update(to_update, ["requested_via_groups"])
    return bool(to_update)


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
        for member in mapping.group.user_set.filter(email__gt=""):
            send_review_requested_mail(proposal, member)

    if created:
        # Link votes group members may already have cast to the new requests.
        sync_group_review_links(proposal)

    return created
