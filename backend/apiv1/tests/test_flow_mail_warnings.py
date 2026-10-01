"""Unit tests for the mail_warning_id annotation on flow transitions.

Transitions that mail the proposal owner carry a stable warning id so the
frontend can ask for confirmation before the mail goes out.  Transitions that
send no mail, or whose mail only reaches the call's own responsible address,
must not carry one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase

from apiv1.flows import EventFlow, ProposalFlow
from apiv1.models import Event, Proposal, Series


def _warnings(transitions) -> dict[str, str | None]:
    return {t.action: t.mail_warning_id for t in transitions if t.enabled}


class ProposalMailWarningTest(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="warn-owner", email="warn-owner@example.com", password="pw"
        )
        self.moderator = user_model.objects.create_superuser(
            username="warn-moderator", email="warn-moderator@example.com", password="pw"
        )

    def _proposal(self, status: str, *, owner_email: str = "warn-owner@example.com") -> Proposal:
        self.owner.email = owner_email
        self.owner.save(update_fields=["email"])
        return Proposal.objects.create(
            title="Warned Proposal",
            status=status,
            owner=self.owner,
            material_cost_eur="0",
        )

    def test_submitted_owner_facing_transitions_are_annotated(self) -> None:
        proposal = self._proposal(Proposal.Status.SUBMITTED)
        warnings = _warnings(ProposalFlow(proposal).get_available_transitions(self.moderator))
        self.assertEqual(warnings.get("reject"), "proposal.reject")
        self.assertEqual(warnings.get("revise"), "proposal.revise")

    def test_on_behalf_submissions_are_split_by_source_status(self) -> None:
        """A draft is submitted on behalf, a revision is resubmitted on behalf."""
        draft = self._proposal(Proposal.Status.DRAFT)
        draft_transitions = {
            t.action: t
            for t in ProposalFlow(draft).get_available_transitions(self.moderator)
        }
        self.assertEqual(draft_transitions["submit_on_behalf"].label_id, "submit_on_behalf")
        self.assertEqual(
            draft_transitions["submit_on_behalf"].mail_warning_id,
            "proposal.submit_on_behalf",
        )

        revise = Proposal.objects.create(
            title="Revised Proposal",
            status=Proposal.Status.REVISE,
            owner=self.owner,
            material_cost_eur="0",
        )
        revise_transitions = {
            t.action: t
            for t in ProposalFlow(revise).get_available_transitions(self.moderator)
        }
        self.assertEqual(
            revise_transitions["submit_on_behalf"].label_id, "resubmit_on_behalf"
        )
        self.assertEqual(
            revise_transitions["submit_on_behalf"].mail_warning_id,
            "proposal.resubmit_on_behalf",
        )

    def test_undo_accept_has_no_warning(self) -> None:
        proposal = self._proposal(Proposal.Status.ACCEPTED)
        warnings = _warnings(ProposalFlow(proposal).get_available_transitions(self.moderator))
        self.assertIn("undo_accept", warnings)
        self.assertIsNone(warnings["undo_accept"])

    def test_no_warning_without_an_owner_address(self) -> None:
        proposal = self._proposal(Proposal.Status.SUBMITTED, owner_email="")
        warnings = _warnings(ProposalFlow(proposal).get_available_transitions(self.moderator))
        self.assertIsNone(warnings.get("reject"))
        self.assertIsNone(warnings.get("revise"))


class EventMailWarningTest(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="warn-event-owner", email="warn-event-owner@example.com", password="pw"
        )
        self.moderator = user_model.objects.create_superuser(
            username="warn-event-moderator", email="mod@example.com", password="pw"
        )
        self.series = Series.objects.create(name="Warning Series")
        self.proposal = Proposal.objects.create(
            title="Event Warning Proposal",
            status=Proposal.Status.ACCEPTED,
            owner=self.owner,
            material_cost_eur="0",
        )

    def _event(self, status: str) -> Event:
        return Event.objects.create(
            series=self.series,
            proposal=self.proposal,
            name="Warned Event",
            start_time=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
            end_time=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            status=status,
        )

    def test_submit_notifies_the_owner(self) -> None:
        warnings = _warnings(
            EventFlow(self._event(Event.Status.DRAFT)).get_available_transitions(self.moderator)
        )
        self.assertEqual(warnings.get("submit"), "event.submit")

    def test_contact_only_transitions_have_no_warning(self) -> None:
        """approve/reject only mail the call's responsible address — no warning."""
        warnings = _warnings(
            EventFlow(self._event(Event.Status.PROPOSED)).get_available_transitions(self.moderator)
        )
        for action in ("approve", "reject"):
            self.assertIn(action, warnings)
            self.assertIsNone(warnings[action])

    def test_no_warning_without_an_owner_address(self) -> None:
        self.owner.email = ""
        self.owner.save(update_fields=["email"])
        warnings = _warnings(
            EventFlow(self._event(Event.Status.DRAFT)).get_available_transitions(self.moderator)
        )
        self.assertIsNone(warnings.get("submit"))
