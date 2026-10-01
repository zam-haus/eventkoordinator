import logging
import textwrap
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from viewflow import fsm
from viewflow.fsm.base import Transition

import apiv1
from apiv1.models import Event, Proposal, ProposalReview, check_proposal_required_fields
from openid_user_management.models import OpenIDUser
from prometheus_client import Gauge

logger = logging.getLogger(__name__)


g_proposal_submit = Gauge('eventkoordinator_flow_proposal_submit_count', 'Submitted proposals')
g_proposal_reject = Gauge('eventkoordinator_flow_proposal_reject_count', 'Rejected proposals')
g_proposal_revise = Gauge('eventkoordinator_flow_proposal_revise_count', 'Revised proposals')
g_proposal_accept = Gauge('eventkoordinator_flow_proposal_accept_count', 'Accepted proposals')


def call_from_email(call) -> str:
    """From address for flow mails: the call's contact address, if any."""
    return call.responsible_email if call and call.responsible_email else settings.DEFAULT_FROM_EMAIL


# Warning ids for actions that mail somebody outside the moderator team (the proposal
# owner, or a reviewer). The frontend localizes these ids and asks for confirmation
# before executing the action. Actions whose mails only reach the call's own
# responsible address (event approve/reject) are intentionally absent.
# submit/resubmit are owner actions whose mail only confirms the owner's own
# submission, so they are not annotated; submit_on_behalf is a moderator action.
PROPOSAL_MAIL_WARNINGS = {
    "submit_on_behalf": "proposal.submit_on_behalf",
    "resubmit_on_behalf": "proposal.resubmit_on_behalf",
    "revise": "proposal.revise",
    "revise_after_rejection": "proposal.revise",
    "accept": "proposal.accept",
    "reject": "proposal.reject",
}

# Submitting in one's own name is reserved for the author and their editors, even
# for users whose global permissions (e.g. superusers) would otherwise allow it.
# Moderators use the "on behalf" transitions instead.
AUTHOR_ONLY_PROPOSAL_LABELS = frozenset({"submit", "resubmit"})

EVENT_MAIL_WARNINGS = {
    "submit": "event.submit",
    "publish": "event.publish",
    "confirm": "event.confirm",
    "cancel": "event.cancel",
}






class ProposalTransition:
    """Data class representing an available or unavailable transition."""

    def __init__(
        self,
        action: str,
        label_id: str,
        target_status: str,
        enabled: bool,
        disable_reason: str | None = None,
        mail_warning_id: str | None = None,
    ):
        self.action = action
        self.label_id = label_id
        self.target_status = target_status
        self.enabled = enabled
        self.disable_reason = disable_reason
        self.mail_warning_id = mail_warning_id

    def to_dict(self):
        return {
            "action": self.action,
            "label_id": self.label_id,
            "target_status": self.target_status,
            "enabled": self.enabled,
            "disable_reason": self.disable_reason,
            "mail_warning_id": self.mail_warning_id,
        }


class ProposalFlow:
    status = fsm.State(Proposal.status, default=Proposal.Status.DRAFT)

    def has_permission(*perms):
        def permission_handler(self: object, user: Any) -> bool | None:
            if not isinstance(user, OpenIDUser):
                logger.warning(
                    f"Permission check failed: user {user!r} is not an OpenIDUser"
                )
                return False
            if not isinstance(self, ProposalFlow):
                logger.warning(
                    f"Permission check failed: object {self!r} is not a ProposalFlow instance"
                )
                return False
            proposal = self.object
            for perm in perms:
                if not user.has_perm(perm, proposal):
                    logger.debug(
                        f"Permission check failed: user {user.username!r} does not have all of the required permissions {perms} for proposal {proposal.pk}"
                    )
                    return False
            logger.debug(
                f"Permission check passed: user {user.username!r} has all of the required permissions {perms} for proposal {proposal.pk}"
            )
            return True

        return permission_handler

    @staticmethod
    def _review_gate_message(proposal: Proposal) -> str | None:
        """Return a disable reason if any reviews block acceptance, or None if clear."""
        # A vote only counts towards its group request through the stored links, so
        # make sure they reflect the current group memberships before deciding.
        # Imported lazily: review_requests imports call_from_email from this module.
        from apiv1.review_requests import sync_group_review_links

        sync_group_review_links(proposal)
        reviews = list(
            ProposalReview.objects.filter(proposal=proposal).values(
                "kind", "status", "reviewer_is_system", "is_blocking",
                "group_code", "requested_directly", "requested_via_groups",
            )
        )

        def _derive_group(code: str) -> str:
            member_statuses = [
                r["status"] for r in reviews
                if r["kind"] == "user" and code in (r["requested_via_groups"] or [])
            ]
            if "rejected" in member_statuses:
                return "rejected"
            if "revise" in member_statuses:
                return "revise"
            if "approved" in member_statuses:
                return "approved"
            return "pending"

        pending_count = rejected_count = revise_count = 0
        for r in reviews:
            if r["kind"] == "user" and (r["status"] == "note" or r["reviewer_is_system"]):
                continue
            if not r["is_blocking"]:
                # Optional review: requested and shown, but never blocks acceptance.
                continue
            if (
                r["kind"] == "user"
                and not r["requested_directly"]
                and (r["requested_via_groups"] or [])
            ):
                continue
            effective = _derive_group(r["group_code"]) if r["kind"] == "group" else r["status"]
            if effective == "pending":
                pending_count += 1
            elif effective == "rejected":
                rejected_count += 1
            elif effective == "revise":
                revise_count += 1

        if rejected_count:
            return f"{rejected_count} reviewer{'s' if rejected_count > 1 else ''} rejected this proposal."
        if revise_count:
            return f"{revise_count} reviewer{'s' if revise_count > 1 else ''} requested changes."
        if pending_count:
            return f"Waiting on {pending_count} pending review{'s' if pending_count > 1 else ''}."
        return None

    def reviews_allow_accept(self: object) -> bool:
        """Condition: no pending/rejected/revise reviews are blocking acceptance."""
        if not isinstance(self, ProposalFlow):
            return False
        return ProposalFlow._review_gate_message(self.object) is None

    def has_required_information(self: object) -> bool:
        if not isinstance(self, ProposalFlow):
            logger.warning(
                f"Condition check failed: object {self!r} is not a ProposalFlow instance"
            )
            return False
        proposal = self.object
        return all(
            (
                status["status"] == "ok"
                for status in check_proposal_required_fields(proposal).values()
            )
        )

    def __init__(self, object: Proposal):
        self.object = object

    @status.setter()
    def _set_object_status(self, value):
        logging.debug(f"Setting object status: {value}")
        self.object.status = value

    @status.getter()
    def _get_object_status(self):
        logging.debug(f"Getting object status: {self}")
        return self.object.status


    @status.transition(
        source=Proposal.Status.DRAFT,
        target=Proposal.Status.SUBMITTED,
        label="submit_on_behalf",
        conditions=[],
        permission=has_permission((apiv1, "revise", Proposal)),
    )
    @status.transition(
        source=Proposal.Status.REVISE,
        target=Proposal.Status.SUBMITTED,
        label="resubmit_on_behalf",
        conditions=[],
        permission=has_permission((apiv1, "revise", Proposal)),
    )
    def submit_on_behalf(self):
        self._do_submit()

    @status.transition(
        source=Proposal.Status.ACCEPTED,
        target=Proposal.Status.SUBMITTED,
        label="undo_accept",
        conditions=[],
        permission=has_permission((apiv1, "accept", Proposal)),
    )
    def undo_accept(self):
        logger.info(f"Undoing acceptance of proposal: {self.object!r}")
        self.object.save()

    @status.transition(
        source=Proposal.Status.DRAFT,
        target=Proposal.Status.SUBMITTED,
        label="submit",
        conditions=[has_required_information],
        permission=has_permission((apiv1, "submit", Proposal)),
    )
    @status.transition(
        source=Proposal.Status.REVISE,
        target=Proposal.Status.SUBMITTED,
        label="resubmit",
        conditions=[has_required_information],
        permission=has_permission((apiv1, "submit", Proposal)),
    )
    def submit(self):
        self._do_submit()

    def _do_submit(self):
        logger.info(f"Submitting proposal: {self.object!r}")
        g_proposal_submit.inc()
        self.object.save()
        # Request the reviews configured for this proposal's submission area.
        # Imported lazily: review_requests imports call_from_email from this module.
        from apiv1.review_requests import auto_request_area_reviews

        try:
            auto_request_area_reviews(self.object)
        except BaseException as e:
            logger.error("Failed to auto-request area reviews: " + str(e), exc_info=e)
        proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{self.object.pk}"
        try:
            send_mail(
                subject=f"Einreichung eingegangen / Submission received: {self.object.title}",
                message=render_to_string(
                    "apiv1/mails/submit.txt.j2",
                    dict(object=self.object, proposal_url=proposal_url),
                ),
                html_message=render_to_string(
                    "apiv1/mails/submit.html.j2",
                    dict(object=self.object, proposal_url=proposal_url),
                ),
                from_email=call_from_email(self.object.call),
                recipient_list=[self.object.owner.email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error("Failed to send submission confirmation: " + str(e), exc_info=e)
            raise
        if self.object.call and self.object.call.responsible_email:
            try:
                send_mail(
                    subject=f"Neue Einreichung / New submission: {self.object.title}",
                    message=render_to_string(
                        "apiv1/mails/submit_contact.txt.j2",
                        dict(object=self.object, proposal_url=proposal_url),
                    ),
                    html_message=render_to_string(
                        "apiv1/mails/submit_contact.html.j2",
                        dict(object=self.object, proposal_url=proposal_url),
                    ),
                    from_email=call_from_email(self.object.call),
                    recipient_list=[self.object.call.responsible_email],
                    fail_silently=False,
                )
            except BaseException as e:
                logger.error("Failed to send submission notification to call contact: " + str(e), exc_info=e)

    @status.transition(
        source=Proposal.Status.SUBMITTED,
        target=Proposal.Status.REVISE,
        label="revise",
        permission=has_permission((apiv1, "revise", Proposal)),
    )
    @status.transition(
        source=Proposal.Status.REJECTED,
        target=Proposal.Status.REVISE,
        label="revise_after_rejection",
        permission=has_permission((apiv1, "revise", Proposal)),
    )
    def revise(self):
        logger.info(f"Revising proposal: {self.object}")
        g_proposal_revise.inc()
        proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{self.object.pk}"
        reviews_with_comments = list(
            ProposalReview.objects.filter(
                proposal=self.object,
                kind=ProposalReview.KIND_USER,
            ).exclude(comment="").select_related("reviewer")
        )
        try:
            send_mail(
                subject=f"Überarbeitung angefordert / Revision requested: {self.object.title}",
                message=render_to_string(
                    "apiv1/mails/revise.txt.j2",
                    dict(object=self.object, proposal_url=proposal_url, reviews=reviews_with_comments),
                ),
                html_message=render_to_string(
                    "apiv1/mails/revise.html.j2",
                    dict(object=self.object, proposal_url=proposal_url, reviews=reviews_with_comments),
                ),
                from_email=call_from_email(self.object.call),
                recipient_list=[self.object.owner.email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error("Failed to send message: " + str(e), exc_info=e)
            raise
        self.object.save()

    @status.transition(
        source=Proposal.Status.SUBMITTED,
        target=Proposal.Status.ACCEPTED,
        label="accept",
        conditions=[reviews_allow_accept],
        permission=has_permission((apiv1, "accept", Proposal)),
    )
    def accept(self):
        logger.info(f"Accepting proposal: {self.object}")
        g_proposal_accept.inc()
        self.object.save()
        proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{self.object.pk}"
        try:
            send_mail(
                subject=f"Einreichung angenommen / Submission accepted: {self.object.title}",
                message=render_to_string(
                    "apiv1/mails/accept.txt.j2",
                    dict(object=self.object, proposal_url=proposal_url),
                ),
                html_message=render_to_string(
                    "apiv1/mails/accept.html.j2",
                    dict(object=self.object, proposal_url=proposal_url),
                ),
                from_email=call_from_email(self.object.call),
                recipient_list=[self.object.owner.email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error("Failed to send acceptance mail: " + str(e), exc_info=e)
            raise

    @status.transition(
        source=Proposal.Status.SUBMITTED,
        target=Proposal.Status.REJECTED,
        label="reject",
        permission=has_permission((apiv1, "reject", Proposal)),
    )
    def reject(self):
        logger.info(f"Rejecting proposal: {self.object}")
        g_proposal_reject.inc()
        self.object.save()
        proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{self.object.pk}"
        try:
            send_mail(
                subject=f"Einreichung abgelehnt / Submission rejected: {self.object.title}",
                message=render_to_string(
                    "apiv1/mails/reject.txt.j2",
                    dict(object=self.object, proposal_url=proposal_url),
                ),
                html_message=render_to_string(
                    "apiv1/mails/reject.html.j2",
                    dict(object=self.object, proposal_url=proposal_url),
                ),
                from_email=call_from_email(self.object.call),
                recipient_list=[self.object.owner.email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error("Failed to send rejection mail: " + str(e), exc_info=e)
            raise

    def get_available_transitions(self, user: OpenIDUser) -> list[ProposalTransition]:
        """
        Get all available transitions for this proposal and user.
        Returns a list of ProposalTransition objects with enable/disable reasons.
        Uses the FSM's transition metadata from the state machine definition.
        """
        transitions_list = []

        # Get all transition methods from the class
        transition_methods = [
            ("submit", self.submit),
            ("submit_on_behalf", self.submit_on_behalf),
            ("undo_accept", self.undo_accept),
            ("revise", self.revise),
            ("accept", self.accept),
            ("reject", self.reject),
        ]

        for action, method in transition_methods:
            # Get the actual transition objects from the method descriptor
            try:
                all_transitions = method.get_transitions()
                # Filter to transitions that apply from the current status
                for transition in all_transitions:
                    if transition.source == self.object.status:
                        result = self._evaluate_transition(transition, action, user)
                        transitions_list.append(result)
                        break  # Found the transition for this action from current state
            except Exception as e:
                logger.error(f"Error getting transitions for {action}: {str(e)}")

        return transitions_list

    def _user_is_author(self, user: OpenIDUser) -> bool:
        """Whether the user submits in their own name, i.e. owns or edits the proposal."""
        return user == self.object.owner or self.object.editors.filter(pk=user.pk).exists()

    def _mail_warning_id(self, label_id: str) -> str | None:
        """Warning id if executing this transition mails the proposal owner."""
        owner = self.object.owner
        if not owner or not owner.email:
            return None
        return PROPOSAL_MAIL_WARNINGS.get(label_id)

    def _evaluate_transition(
        self, transition: Transition, action: str, user: OpenIDUser
    ) -> ProposalTransition:
        """
        Evaluate a single transition to determine if it's allowed.
        Uses the FSM transition's conditions and permissions.
        """
        label_id = transition.label

        # Submitting in one's own name is the author's prerogative, regardless of
        # any global permissions the user may hold.
        if label_id in AUTHOR_ONLY_PROPOSAL_LABELS and not self._user_is_author(user):
            return ProposalTransition(
                action=action,
                label_id=label_id,
                target_status=transition.target,
                enabled=False,
                disable_reason=(
                    "Only the proposal owner or one of its editors can submit it in "
                    "their own name — use the \"on behalf\" action instead"
                ),
            )

        # Check conditions
        conditions_met = transition.conditions_met(self)
        if not conditions_met:
            if action == "submit":
                checklist = check_proposal_required_fields(self.object)
                missing_items = [
                    name for name, item in checklist.items() if item["status"] != "ok"
                ]
                missing_str = (
                    ", ".join(missing_items)
                    if missing_items
                    else "incomplete information"
                )
                disable_reason = f"Incomplete proposal: {missing_str}"
            elif action == "accept":
                disable_reason = (
                    ProposalFlow._review_gate_message(self.object)
                    or "Conditions not met for this transition"
                )
            else:
                disable_reason = "Conditions not met for this transition"
            return ProposalTransition(
                action=action,
                label_id=label_id,
                target_status=transition.target,
                enabled=False,
                disable_reason=disable_reason,
            )

        # Check permissions
        has_perm = transition.has_perm(self, user)
        if not has_perm:
            return ProposalTransition(
                action=action,
                label_id=label_id,
                target_status=transition.target,
                enabled=False,
                disable_reason="Insufficient permissions for this action",
            )

        return ProposalTransition(
            action=action,
            label_id=label_id,
            target_status=transition.target,
            enabled=True,
            disable_reason=None,
            mail_warning_id=self._mail_warning_id(label_id),
        )

    def execute_transition(self, action: str) -> bool:
        """
        Execute a transition action (submit, accept, reject, revise).
        Returns True if successful, False otherwise.
        """
        action_to_method = {
            "submit": self.submit,
            "accept": self.accept,
            "reject": self.reject,
            "revise": self.revise,
            "undo_accept": self.undo_accept,
            "submit_on_behalf": self.submit_on_behalf,
        }

        method = action_to_method.get(action)
        if not method:
            logger.error(f"Unknown transition action: {action}")
            return False

        try:
            method()
            return True
        except Exception as e:
            logger.error(f"Failed to execute transition {action}: {str(e)}")
            return False


class EventTransition:
    """Data class representing an available or unavailable event transition."""

    def __init__(
        self,
        action: str,
        label_id: str,
        target_status: str,
        enabled: bool,
        disable_reason: str | None = None,
        mail_warning_id: str | None = None,
    ):
        self.action = action
        self.label_id = label_id
        self.target_status = target_status
        self.enabled = enabled
        self.disable_reason = disable_reason
        self.mail_warning_id = mail_warning_id

    def to_dict(self):
        return {
            "action": self.action,
            "label_id": self.label_id,
            "target_status": self.target_status,
            "enabled": self.enabled,
            "disable_reason": self.disable_reason,
            "mail_warning_id": self.mail_warning_id,
        }


# Window within which a Published event may be confirmed (days before start_time).
CONFIRM_WINDOW_DAYS = 14


class EventFlow:
    """FSM flow for the Event lifecycle.

    State diagram:
        Draft → Proposed   : submit
        Proposed → Planned  : approve
        Proposed → Rejected : reject
        Planned → Published : publish
        Published → Confirmed : confirm  (≈1 week before event)
        Published → Canceled  : cancel
        Confirmed → Completed : complete (event date passed)
        Completed → Archived  : archive
        Canceled  → Archived  : archive
        Rejected  → Archived  : archive
    """

    status = fsm.State(Event.status, default=Event.Status.DRAFT)

    def has_permission(*perms):
        def permission_handler(self: object, user: Any) -> bool | None:
            if not isinstance(user, OpenIDUser):
                logger.warning(
                    f"Permission check failed: user {user!r} is not an OpenIDUser"
                )
                return False
            if not isinstance(self, EventFlow):
                logger.warning(
                    f"Permission check failed: object {self!r} is not an EventFlow instance"
                )
                return False
            event = self.object
            for perm in perms:
                if not user.has_perm(perm, event):
                    logger.debug(
                        f"Permission check failed: user {user.username!r} does not have all of "
                        f"the required permissions {perms} for event {event.pk}"
                    )
                    return False
            logger.debug(
                f"Permission check passed: user {user.username!r} has all of the required "
                f"permissions {perms} for event {event.pk}"
            )
            return True

        return permission_handler

    def _within_confirmation_window(self: object) -> bool:
        """Condition: event start_time is within CONFIRM_WINDOW_DAYS days from now."""
        if not isinstance(self, EventFlow):
            return False
        event = self.object
        now = timezone.now()
        return now <= event.start_time <= now + timedelta(days=CONFIRM_WINDOW_DAYS)

    def _event_has_passed(self: object) -> bool:
        """Condition: event end_time is in the past."""
        if not isinstance(self, EventFlow):
            return False
        event = self.object
        return timezone.now() >= event.end_time

    def __init__(self, object: Event):
        self.object = object

    @status.setter()
    def _set_object_status(self, value):
        logging.debug(f"Setting event status: {value}")
        self.object.status = value

    @status.getter()
    def _get_object_status(self):
        logging.debug(f"Getting event status: {self}")
        return self.object.status

    # ------------------------------------------------------------------ #
    #  Transitions                                                          #
    # ------------------------------------------------------------------ #

    @status.transition(
        source=Event.Status.DRAFT,
        target=Event.Status.PROPOSED,
        label="Submit event",
        permission=has_permission((apiv1, "submit", Event)),
    )
    def submit(self):
        logger.info(f"Submitting event: {self.object!r}")
        self.object.save()
        self._notify_proposal_owner_event("submit")

    @status.transition(
        source=Event.Status.PROPOSED,
        target=Event.Status.PLANNED,
        label="Approve event",
        permission=has_permission((apiv1, "approve", Event)),
    )
    def approve(self):
        logger.info(f"Approving event: {self.object!r}")
        self.object.save()
        self._notify_call_contact_event("approve")
        if False:
            # if there are no overlapping events for the block (or any blocks, if a multiday block event that does not span full days),
            # publish automatically. This allows events that are not in conflict with others to skip the "planned" state and be published immediately.
            conflicts = self.object.find_active_conflicts()
            if conflicts:
                for conflict in conflicts:
                    logger.debug(
                        "Auto-publish blocked for %r: block %s conflicts with %r (status=%s) block %s",
                        self.object,
                        conflict.my_block,
                        conflict.conflicting_event,
                        conflict.conflicting_event.status,
                        conflict.conflicting_block,
                    )
                logger.info(
                    "Not auto-publishing %r: %d conflict(s) found",
                    self.object,
                    len(conflicts),
                )
            else:
                logger.info(
                    "Auto-publishing %r: no overlapping active events found",
                    self.object,
                )
                self.publish()

    def _has_overlapping_events(self) -> bool:
        """Thin wrapper kept for backwards compatibility. Prefer find_active_conflicts() directly."""
        return bool(self.object.find_active_conflicts())


    @status.transition(
        source=Event.Status.PROPOSED,
        target=Event.Status.REJECTED,
        label="Reject event",
        permission=has_permission((apiv1, "reject", Event)),
    )
    def reject(self):
        logger.info(f"Rejecting event: {self.object!r}")
        self.object.save()
        self._notify_call_contact_event("reject")

    @status.transition(
        source=Event.Status.PLANNED,
        target=Event.Status.PUBLISHED,
        label="Publish event",
        permission=has_permission((apiv1, "publish", Event)),
    )
    def publish(self):
        logger.info(f"Publishing event: {self.object!r}")
        self.object.save()
        self._notify_proposal_owner_event("publish")

    @status.transition(
        source=Event.Status.PUBLISHED,
        target=Event.Status.CONFIRMED,
        label="Confirm event",
        conditions=[_within_confirmation_window],
        permission=has_permission((apiv1, "confirm", Event)),
    )
    def confirm(self):
        logger.info(f"Confirming event: {self.object!r}")
        self.object.save()
        self._notify_proposal_owner_event("confirm")
        self._notify_call_contact_event("confirm")

    @status.transition(
        source=Event.Status.PLANNED,
        target=Event.Status.CANCELED,
        label="Cancel event",
        permission=has_permission((apiv1, "cancel", Event)),
    )
    @status.transition(
        source=Event.Status.PUBLISHED,
        target=Event.Status.CANCELED,
        label="Cancel event",
        permission=has_permission((apiv1, "cancel", Event)),
    )
    def cancel(self):
        logger.info(f"Canceling event: {self.object!r}")
        self.object.save()
        self._notify_proposal_owner_event("cancel")
        self._notify_call_contact_event("cancel")

    @status.transition(
        source=Event.Status.CONFIRMED,
        target=Event.Status.COMPLETED,
        label="Complete event",
        conditions=[_event_has_passed],
        permission=has_permission((apiv1, "complete", Event)),
    )
    def complete(self):
        logger.info(f"Completing event: {self.object!r}")
        self.object.save()

    @status.transition(
        source=[Event.Status.COMPLETED, Event.Status.CANCELED, Event.Status.REJECTED],
        target=Event.Status.ARCHIVED,
        label="Archive event",
        permission=has_permission((apiv1, "archive", Event)),
    )
    def archive(self):
        logger.info(f"Archiving event: {self.object!r}")
        self.object.save()

    # ------------------------------------------------------------------ #
    #  Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _notify_proposal_owner_event(self, action: str) -> None:
        """Send a notification to the proposal owner for event lifecycle transitions."""
        proposal = self.object.proposal
        if not proposal or not proposal.owner or not proposal.owner.email:
            return
        proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{proposal.pk}"
        event_url = f"{settings.FRONTEND_BASE_URL}/proposal/{proposal.pk}/event/{self.object.pk}"
        _subjects = {
            "submit": ("Neuer Terminvorschlag", "New date proposal"),
            "publish": ("Termin wird veröffentlicht", "Date will be published"),
            "confirm": ("Termin bestätigt", "Date confirmed"),
            "cancel": ("Termin abgesagt", "Date canceled"),
        }
        subject_de, subject_en = _subjects[action]
        ctx = dict(object=self.object, proposal_url=proposal_url, event_url=event_url)
        try:
            send_mail(
                subject=f"{subject_de} / {subject_en}: {self.object.name}",
                message=render_to_string(f"apiv1/mails/event_{action}_owner.txt.j2", ctx),
                html_message=render_to_string(f"apiv1/mails/event_{action}_owner.html.j2", ctx),
                from_email=call_from_email(proposal.call),
                recipient_list=[proposal.owner.email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error(
                f"Failed to send event {action} notification to proposal owner: " + str(e),
                exc_info=e,
            )

    def _notify_call_contact_event(self, action: str) -> None:
        """Send a notification to the call's responsible for event lifecycle transitions."""
        proposal = self.object.proposal
        if not proposal:
            return
        call = proposal.call
        if not call or not call.responsible_email:
            return
        proposal_url = f"{settings.FRONTEND_BASE_URL}/proposal-editor/{proposal.pk}"
        event_url = f"{settings.FRONTEND_BASE_URL}/proposal/{proposal.pk}/event/{self.object.pk}"
        _template_names = {
            "approve": "event_approve_contact",
            "reject": "event_reject_contact",
            "confirm": "event_confirm_contact",
            "cancel": "event_cancel_contact",
        }
        _subjects = {
            "approve": ("Terminvorschlag bestätigt", "Date proposal confirmed"),
            "reject": ("Terminvorschlag abgelehnt", "Date proposal rejected"),
            "confirm": ("Termin bestätigt", "Date confirmed"),
            "cancel": ("Termin abgesagt", "Date canceled"),
        }
        template_name = _template_names[action]
        subject_de, subject_en = _subjects[action]
        ctx = dict(object=self.object, proposal_url=proposal_url, event_url=event_url)
        try:
            send_mail(
                subject=f"{subject_de} / {subject_en}: {self.object.name}",
                message=render_to_string(f"apiv1/mails/{template_name}.txt.j2", ctx),
                html_message=render_to_string(f"apiv1/mails/{template_name}.html.j2", ctx),
                from_email=call_from_email(call),
                recipient_list=[call.responsible_email],
                fail_silently=False,
            )
        except BaseException as e:
            logger.error(
                f"Failed to send event {action} notification to call contact: " + str(e),
                exc_info=e,
            )

    def get_available_transitions(self, user: OpenIDUser) -> list[EventTransition]:
        """Return all transitions (enabled or not) applicable to the current status."""
        transitions_list = []
        transition_methods = [
            ("submit", self.submit),
            ("approve", self.approve),
            ("reject", self.reject),
            ("publish", self.publish),
            ("confirm", self.confirm),
            ("cancel", self.cancel),
            ("complete", self.complete),
            ("archive", self.archive),
        ]

        for action, method in transition_methods:
            try:
                all_transitions = method.get_transitions()
                for transition in all_transitions:
                    sources = (
                        transition.source
                        if isinstance(transition.source, list)
                        else [transition.source]
                    )
                    if self.object.status in sources:
                        result = self._evaluate_transition(transition, action, user)
                        transitions_list.append(result)
                        break
            except Exception as e:
                logger.error(f"Error getting transitions for {action}: {str(e)}")

        return transitions_list

    def _mail_warning_id(self, action: str) -> str | None:
        """Warning id if executing this transition mails the proposal owner."""
        proposal = self.object.proposal
        if not proposal or not proposal.owner or not proposal.owner.email:
            return None
        return EVENT_MAIL_WARNINGS.get(action)

    def _evaluate_transition(
        self, transition: Transition, action: str, user: OpenIDUser
    ) -> EventTransition:
        conditions_met = transition.conditions_met(self)
        if not conditions_met:
            if action == "confirm":
                disable_reason = (
                    f"Event must start within {CONFIRM_WINDOW_DAYS} days to be confirmed"
                )
            elif action == "complete":
                disable_reason = "Event end time has not passed yet"
            else:
                disable_reason = "Conditions not met for this transition"
            return EventTransition(
                action=action,
                label_id=action,
                target_status=transition.target,
                enabled=False,
                disable_reason=disable_reason,
            )

        has_perm = transition.has_perm(self, user)
        if not has_perm:
            return EventTransition(
                action=action,
                label_id=action,
                target_status=transition.target,
                enabled=False,
                disable_reason="Insufficient permissions for this action",
            )

        return EventTransition(
            action=action,
            label_id=action,
            target_status=transition.target,
            enabled=True,
            disable_reason=None,
            mail_warning_id=self._mail_warning_id(action),
        )

    def execute_transition(self, action: str) -> bool:
        """Execute a named transition. Returns True on success."""
        action_to_method = {
            "submit": self.submit,
            "approve": self.approve,
            "reject": self.reject,
            "publish": self.publish,
            "confirm": self.confirm,
            "cancel": self.cancel,
            "complete": self.complete,
            "archive": self.archive,
        }

        method = action_to_method.get(action)
        if not method:
            logger.error(f"Unknown event transition action: {action}")
            return False

        try:
            method()
            return True
        except Exception as e:
            logger.error(f"Failed to execute event transition {action}: {str(e)}")
            return False

