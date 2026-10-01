"""Tests for optional (non-blocking) reviews and automatic area review requests."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.test import TestCase

from apiv1.flows import ProposalFlow
from apiv1.models import Proposal, ProposalArea, ProposalLanguage, SubmissionType
from apiv1.models.basedata import ProposalAreaReviewGroup, ProposalReview
from apiv1.review_requests import auto_request_area_reviews


class OptionalReviewTestBase(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="opt-owner", email="opt-owner@example.com", password="pw"
        )
        # Moderators reach other people's proposals through the superuser
        # short-circuit in PermissionsMixin.has_perm; object-level view_proposal
        # is restricted to owners, editors and reviewers.
        self.moderator = user_model.objects.create_user(
            username="opt-mod",
            email="opt-mod@example.com",
            password="pw",
            is_staff=True,
            is_superuser=True,
        )
        self.moderator.user_permissions.add(
            *Permission.objects.filter(
                codename__in=["view_proposal", "moderate_proposal", "accept_proposal"]
            )
        )
        self.reviewer = user_model.objects.create_user(
            username="opt-reviewer", email="opt-reviewer@example.com", password="pw"
        )

        self.submission_type, _ = SubmissionType.objects.get_or_create(
            code="workshop", defaults={"label": "Workshop"}
        )
        self.language, _ = ProposalLanguage.objects.get_or_create(
            code="en", defaults={"label": "English"}
        )
        self.area, _ = ProposalArea.objects.get_or_create(
            code="metal", defaults={"label": "Metal"}
        )

    def _create_proposal(self, status: str = Proposal.Status.SUBMITTED) -> Proposal:
        return Proposal.objects.create(
            title="Optional review test",
            status=status,
            submission_type=self.submission_type,
            language=self.language,
            area=self.area,
            abstract="A" * 60,
            description="B" * 120,
            occurrence_count=1,
            duration_days=1,
            duration_time_per_day="02:00",
            max_participants=10,
            material_cost_eur="0.00",
            preferred_dates="2026-07-10",
            owner=self.owner,
        )


class ReviewGateTests(OptionalReviewTestBase):
    """A non-blocking review must never prevent acceptance."""

    def test_blocking_pending_review_blocks_acceptance(self) -> None:
        proposal = self._create_proposal()
        ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.reviewer,
            status=ProposalReview.STATUS_PENDING,
            requested_directly=True,
        )
        self.assertIsNotNone(ProposalFlow._review_gate_message(proposal))

    def test_optional_pending_review_does_not_block(self) -> None:
        proposal = self._create_proposal()
        ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.reviewer,
            status=ProposalReview.STATUS_PENDING,
            requested_directly=True,
            is_blocking=False,
        )
        self.assertIsNone(ProposalFlow._review_gate_message(proposal))

    def test_optional_rejection_does_not_block(self) -> None:
        proposal = self._create_proposal()
        ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.reviewer,
            status=ProposalReview.STATUS_REJECTED,
            comment="not convinced",
            requested_directly=True,
            is_blocking=False,
        )
        self.assertIsNone(ProposalFlow._review_gate_message(proposal))

    def test_optional_group_request_does_not_block(self) -> None:
        proposal = self._create_proposal()
        group = Group.objects.create(name="Optional reviewers")
        ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_GROUP,
            group_code=str(group.pk),
            is_blocking=False,
        )
        self.assertIsNone(ProposalFlow._review_gate_message(proposal))

    def test_blocking_group_request_blocks(self) -> None:
        proposal = self._create_proposal()
        group = Group.objects.create(name="Required reviewers")
        ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_GROUP,
            group_code=str(group.pk),
        )
        self.assertIsNotNone(ProposalFlow._review_gate_message(proposal))


class AutoAreaRequestTests(OptionalReviewTestBase):
    """Submission-area mappings create the configured group requests."""

    def setUp(self) -> None:
        super().setUp()
        self.blocking_group = Group.objects.create(name="Area board")
        self.blocking_group.user_set.add(self.reviewer)
        self.optional_group = Group.objects.create(name="Area advisors")
        self.optional_group.user_set.add(self.moderator)
        ProposalAreaReviewGroup.objects.create(area=self.area, group=self.blocking_group)
        ProposalAreaReviewGroup.objects.create(
            area=self.area, group=self.optional_group, is_blocking=False
        )

    def test_requests_are_created_with_the_mapped_blocking_flag(self) -> None:
        proposal = self._create_proposal()
        auto_request_area_reviews(proposal, requested_by=self.moderator)

        requests = {
            r.group_code: r
            for r in ProposalReview.objects.filter(
                proposal=proposal, kind=ProposalReview.KIND_GROUP
            )
        }
        self.assertEqual(set(requests), {str(self.blocking_group.pk), str(self.optional_group.pk)})
        self.assertTrue(requests[str(self.blocking_group.pk)].is_blocking)
        self.assertFalse(requests[str(self.optional_group.pk)].is_blocking)
        self.assertEqual(
            requests[str(self.blocking_group.pk)].requested_by, self.moderator
        )

    def test_group_members_are_notified(self) -> None:
        proposal = self._create_proposal()
        mail.outbox.clear()
        auto_request_area_reviews(proposal)
        recipients = {addr for m in mail.outbox for addr in m.to}
        self.assertEqual(recipients, {self.reviewer.email, self.moderator.email})

    def test_calling_twice_does_not_duplicate_requests(self) -> None:
        proposal = self._create_proposal()
        auto_request_area_reviews(proposal)
        auto_request_area_reviews(proposal)
        self.assertEqual(
            ProposalReview.objects.filter(
                proposal=proposal, kind=ProposalReview.KIND_GROUP
            ).count(),
            2,
        )

    def test_a_hand_adjusted_flag_survives_a_resubmission(self) -> None:
        proposal = self._create_proposal()
        auto_request_area_reviews(proposal)
        request = ProposalReview.objects.get(
            proposal=proposal, group_code=str(self.blocking_group.pk)
        )
        request.is_blocking = False
        request.save(update_fields=["is_blocking"])

        auto_request_area_reviews(proposal)
        request.refresh_from_db()
        self.assertFalse(request.is_blocking)

    def test_area_without_mapping_creates_nothing(self) -> None:
        other_area = ProposalArea.objects.create(code="textile", label="Textile")
        proposal = self._create_proposal()
        proposal.area = other_area
        proposal.save(update_fields=["area"])
        self.assertEqual(auto_request_area_reviews(proposal), [])

    def test_submitting_a_proposal_requests_the_area_reviews(self) -> None:
        # submit_on_behalf shares _do_submit with submit but carries no checklist
        # conditions, so the proposal does not need photos, speakers or a call.
        proposal = self._create_proposal(status=Proposal.Status.DRAFT)
        flow = ProposalFlow(proposal)
        flow.submit_on_behalf()
        self.assertEqual(
            ProposalReview.objects.filter(
                proposal=proposal, kind=ProposalReview.KIND_GROUP
            ).count(),
            2,
        )

    def test_existing_member_vote_is_linked_to_the_new_request(self) -> None:
        proposal = self._create_proposal()
        vote = ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.reviewer,
            status=ProposalReview.STATUS_APPROVED,
            comment="looks good",
        )
        auto_request_area_reviews(proposal)
        vote.refresh_from_db()
        self.assertIn(str(self.blocking_group.pk), vote.requested_via_groups)


class BlockingEndpointTests(OptionalReviewTestBase):
    """PATCH …/reviews/{id}/blocking is restricted to moderators."""

    def setUp(self) -> None:
        super().setUp()
        self.proposal = self._create_proposal()
        self.review = ProposalReview.objects.create(
            proposal=self.proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.reviewer,
            status=ProposalReview.STATUS_PENDING,
            requested_directly=True,
        )

    def _url(self) -> str:
        return f"/api/v1/proposals/{self.proposal.pk}/reviews/{self.review.pk}/blocking"

    def test_moderator_can_make_a_review_optional(self) -> None:
        self.client.force_login(self.moderator)
        response = self.client.patch(
            self._url(), data={"is_blocking": False}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_blocking"])
        self.review.refresh_from_db()
        self.assertFalse(self.review.is_blocking)

    def test_reviewer_cannot_change_their_own_blocking_flag(self) -> None:
        self.client.force_login(self.reviewer)
        response = self.client.patch(
            self._url(), data={"is_blocking": False}, content_type="application/json"
        )
        self.assertIn(response.status_code, (401, 403))
        self.review.refresh_from_db()
        self.assertTrue(self.review.is_blocking)
