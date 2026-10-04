"""Tests for the informational mail to reviewers when a proposal is accepted."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail

from apiv1.flows import ProposalFlow
from apiv1.models import Proposal
from apiv1.models.basedata import ProposalReview
from apiv1.tests.test_optional_reviews import OptionalReviewTestBase


class AcceptReviewerMailTests(OptionalReviewTestBase):
    """Accepting a proposal informs all requested and actual reviewers once each."""

    def setUp(self) -> None:
        super().setUp()
        user_model = get_user_model()
        self.group_member = user_model.objects.create_user(
            username="acc-member", email="acc-member@example.com", password="pw"
        )
        self.pending_reviewer = user_model.objects.create_user(
            username="acc-pending", email="acc-pending@example.com", password="pw"
        )
        self.group = Group.objects.create(name="Accept advisors")
        # The reviewer is also a group member and must only be mailed once.
        self.group.user_set.add(self.group_member, self.reviewer)

        self.proposal = self._create_proposal()
        # An actual vote ...
        ProposalReview.objects.create(
            proposal=self.proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.reviewer,
            status=ProposalReview.STATUS_APPROVED,
            requested_directly=True,
            requested_via_groups=[str(self.group.pk)],
        )
        # ... a direct request that was never answered ...
        ProposalReview.objects.create(
            proposal=self.proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.pending_reviewer,
            status=ProposalReview.STATUS_PENDING,
            requested_directly=True,
            is_blocking=False,
        )
        # ... and a group request.
        ProposalReview.objects.create(
            proposal=self.proposal,
            kind=ProposalReview.KIND_GROUP,
            group_code=str(self.group.pk),
            is_blocking=False,
        )

    def _reviewer_mails(self) -> list:
        return [m for m in mail.outbox if self.owner.email not in m.to]

    def test_all_reviewers_are_informed_once(self) -> None:
        mail.outbox.clear()
        ProposalFlow(self.proposal).accept()

        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, Proposal.Status.ACCEPTED)
        recipients = [addr for m in self._reviewer_mails() for addr in m.to]
        self.assertCountEqual(
            recipients,
            [self.reviewer.email, self.pending_reviewer.email, self.group_member.email],
        )
        for m in self._reviewer_mails():
            self.assertIn("Submission accepted", m.subject)
            self.assertIn(self.proposal.title, m.body)
            self.assertIn("accepted by a moderator", m.body)

    def test_owner_still_gets_the_acceptance_mail(self) -> None:
        mail.outbox.clear()
        ProposalFlow(self.proposal).accept()
        self.assertEqual(
            sum(1 for m in mail.outbox if self.owner.email in m.to), 1
        )

    def test_reviewers_without_email_are_skipped(self) -> None:
        self.group_member.email = ""
        self.group_member.save(update_fields=["email"])
        mail.outbox.clear()
        ProposalFlow(self.proposal).accept()
        recipients = {addr for m in self._reviewer_mails() for addr in m.to}
        self.assertEqual(recipients, {self.reviewer.email, self.pending_reviewer.email})
