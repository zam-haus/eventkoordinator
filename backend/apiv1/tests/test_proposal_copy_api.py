"""API tests for copying a proposal."""

from __future__ import annotations

from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from apiv1.models import (
    Proposal,
    ProposalArea,
    ProposalLanguage,
    Speaker,
    SubmissionType,
)
from apiv1.models.basedata import ProposalReview


class ProposalCopyApiTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="copy-owner", email="copy-owner@example.com", password="pw"
        )
        self.other_user = user_model.objects.create_user(
            username="copy-other", email="copy-other@example.com", password="pw"
        )
        perms = Permission.objects.filter(
            codename__in=["add_proposal", "change_proposal", "view_proposal"]
        )
        self.owner.user_permissions.add(*perms)
        self.other_user.user_permissions.add(*perms)

        self.submission_type, _ = SubmissionType.objects.get_or_create(
            code="workshop", defaults={"label": "Workshop"}
        )
        self.language, _ = ProposalLanguage.objects.get_or_create(
            code="en", defaults={"label": "English"}
        )
        self.area, _ = ProposalArea.objects.get_or_create(
            code="wood", defaults={"label": "Wood"}
        )

    def _create_source(self, status: str = Proposal.Status.ACCEPTED) -> Proposal:
        proposal = Proposal.objects.create(
            title="Source Proposal",
            status=status,
            submission_type=self.submission_type,
            language=self.language,
            area=self.area,
            abstract="A" * 60,
            description="B" * 120,
            internal_notes="secret notes",
            occurrence_count=3,
            duration_days=2,
            duration_time_per_day="03:30",
            is_basic_course=True,
            max_participants=12,
            material_cost_eur="12.50",
            preferred_dates="2026-07-10",
            has_building_access=True,
            moderation_comment="moderator said something",
            owner=self.owner,
        )
        proposal.editors.add(self.other_user)
        proposal.photo.save("poster.png", ContentFile(b"photo-bytes"), save=True)

        primary = Speaker.objects.create(
            proposal=proposal,
            email="primary@example.com",
            display_name="Primary Speaker",
            biography="Primary biography that is long enough for validation.",
            role=Speaker.Role.PRIMARY,
            sort_order=0,
        )
        primary.profile_picture.save("avatar.png", ContentFile(b"avatar-bytes"), save=True)
        Speaker.objects.create(
            proposal=proposal,
            email="co@example.com",
            display_name="Co Speaker",
            biography="Co-speaker biography that is long enough for validation.",
            role=Speaker.Role.CO_SPEAKER,
            sort_order=1,
        )
        ProposalReview.objects.create(
            proposal=proposal,
            kind=ProposalReview.KIND_USER,
            reviewer=self.other_user,
            status=ProposalReview.STATUS_APPROVED,
        )
        return proposal

    def test_copy_creates_draft_with_speakers_and_without_reviews(self) -> None:
        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            source = self._create_source()
            self.client.force_login(self.owner)
            response = self.client.post(f"/api/v1/proposals/{source.id}/copy")
            self.assertEqual(response.status_code, 201, response.content)
            payload = response.json()
            self.assertNotEqual(payload["id"], str(source.id))
            self.assertEqual(payload["title"], "Source Proposal")

            source.refresh_from_db()
            copy = Proposal.objects.get(pk=payload["id"])
            self.assertEqual(copy.status, Proposal.Status.DRAFT)
            self.assertEqual(copy.owner, self.owner)
            self.assertEqual(copy.reviews.count(), 0)
            self.assertEqual(copy.moderation_comment, "")

            for field in (
                "title", "submission_type", "area", "language", "abstract",
                "description", "internal_notes", "occurrence_count", "duration_days",
                "duration_time_per_day", "is_basic_course", "max_participants",
                "material_cost_eur", "preferred_dates", "has_building_access", "call",
            ):
                self.assertEqual(getattr(copy, field), getattr(source, field), field)
            self.assertEqual(set(copy.editors.all()), set(source.editors.all()))

            # Photo duplicated, not shared
            self.assertTrue(copy.photo)
            self.assertNotEqual(copy.photo.name, source.photo.name)
            with copy.photo.open("rb") as fh:
                self.assertEqual(fh.read(), b"photo-bytes")

            copied_speakers = list(copy.speakers.order_by("sort_order"))
            source_speakers = list(source.speakers.order_by("sort_order"))
            self.assertEqual(len(copied_speakers), 2)
            for src, dst in zip(source_speakers, copied_speakers):
                self.assertNotEqual(src.pk, dst.pk)
                for field in ("email", "display_name", "biography", "role", "sort_order"):
                    self.assertEqual(getattr(dst, field), getattr(src, field), field)
            self.assertTrue(copied_speakers[0].profile_picture)
            self.assertNotEqual(
                copied_speakers[0].profile_picture.name,
                source_speakers[0].profile_picture.name,
            )
            self.assertFalse(copied_speakers[1].profile_picture)

            # Source is untouched
            source.refresh_from_db()
            self.assertEqual(source.status, Proposal.Status.ACCEPTED)
            self.assertEqual(source.reviews.count(), 1)
            self.assertEqual(source.speakers.count(), 2)

    def test_copy_sets_owner_to_current_user(self) -> None:
        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            source = self._create_source()
            self.client.force_login(self.other_user)  # editor of the source
            response = self.client.post(f"/api/v1/proposals/{source.id}/copy")
            self.assertEqual(response.status_code, 201, response.content)
            copy = Proposal.objects.get(pk=response.json()["id"])
            self.assertEqual(copy.owner, self.other_user)

    def test_copy_requires_view_permission_on_source(self) -> None:
        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            source = self._create_source(status=Proposal.Status.DRAFT)
            source.editors.clear()
            stranger = get_user_model().objects.create_user(
                username="copy-stranger", email="s@example.com", password="pw"
            )
            stranger.user_permissions.add(
                *Permission.objects.filter(codename__in=["add_proposal", "view_proposal"])
            )
            self.client.force_login(stranger)
            response = self.client.post(f"/api/v1/proposals/{source.id}/copy")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(Proposal.objects.count(), 1)

    def test_copy_requires_add_permission(self) -> None:
        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            source = self._create_source()
            # Every user is in the default group that grants add_proposal; strip it.
            self.owner.groups.clear()
            self.owner.user_permissions.remove(
                Permission.objects.get(codename="add_proposal")
            )
            self.client.force_login(self.owner)
            response = self.client.post(f"/api/v1/proposals/{source.id}/copy")
            self.assertIn(response.status_code, (401, 403))
            self.assertEqual(Proposal.objects.count(), 1)

    def test_copy_unknown_proposal_returns_404(self) -> None:
        self.client.force_login(self.owner)
        response = self.client.post(
            "/api/v1/proposals/00000000-0000-0000-0000-000000000000/copy"
        )
        self.assertEqual(response.status_code, 404)
