from __future__ import annotations

from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apiv1.models import (
    Proposal,
    ProposalArea,
    ProposalLanguage,
    Speaker,
    SubmissionType,
)
from apiv1.tests import util_imggen


class ImageUploadApiTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username='image-upload-owner',
            email='image-upload-owner@example.com',
            password='test-password',
        )
        self.other_user = user_model.objects.create_user(
            username='image-upload-other',
            email='image-upload-other@example.com',
            password='test-password',
        )

        required_permissions = Permission.objects.filter(
            codename__in=['change_proposal', 'view_proposal']
        )
        self.owner.user_permissions.add(*required_permissions)
        self.other_user.user_permissions.add(*required_permissions)

        self.submission_type, _ = SubmissionType.objects.get_or_create(
            code='workshop',
            defaults={'label': 'Workshop'},
        )
        self.language, _ = ProposalLanguage.objects.get_or_create(
            code='en',
            defaults={'label': 'English'},
        )
        self.area, _ = ProposalArea.objects.get_or_create(
            code='wood',
            defaults={'label': 'Wood'},
        )

    def _create_proposal(self) -> Proposal:
        return Proposal.objects.create(
            title='Proposal With Images',
            submission_type=self.submission_type,
            language=self.language,
            area=self.area,
            abstract='A' * 50,
            description='B' * 120,
            internal_notes='',
            occurrence_count=1,
            duration_days=1,
            duration_time_per_day='02:00',
            is_basic_course=False,
            max_participants=10,
            material_cost_eur='0.00',
            preferred_dates='2026-07-10',
            has_building_access=False,
            owner=self.owner,
        )

    def test_owner_can_upload_proposal_image(self) -> None:
        proposal = self._create_proposal()

        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            self.client.force_login(self.owner)
            response = self.client.post(
                f'/api/v1/proposals/{proposal.id}/photo',
                data={
                    'file': SimpleUploadedFile(
                        'poster.png',
                        util_imggen.encode_large_noise_png(),
                        content_type='image/png',
                    )
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['photo'].startswith('/media/proposal_photos/'))

        proposal.refresh_from_db()
        self.assertTrue(proposal.photo.name.startswith('proposal_photos/'))
        self.assertTrue(proposal.photo.name.endswith('.png'))

    def test_owner_can_upload_speaker_image(self) -> None:
        proposal = self._create_proposal()
        speaker = Speaker.objects.create(
            proposal=proposal,
            email='speaker@example.com',
            display_name='Speaker Example',
            biography='Biography text long enough for validation.' * 2,
        )

        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            self.client.force_login(self.owner)
            response = self.client.post(
                f'/api/v1/proposals/{proposal.id}/speakers/{speaker.id}/profile-picture',
                data={
                    'file': SimpleUploadedFile(
                        'avatar.jpeg',
                        b'speaker-image',
                        content_type='image/jpeg',
                    )
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['speaker']['profile_picture'].startswith('/media/speaker_profiles/'))

        speaker.refresh_from_db()
        self.assertTrue(speaker.profile_picture.name.startswith('speaker_profiles/'))
        self.assertTrue(speaker.profile_picture.name.endswith('.jpeg'))

    def test_invalid_image_extension_is_rejected(self) -> None:
        proposal = self._create_proposal()

        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            self.client.force_login(self.owner)
            response = self.client.post(
                f'/api/v1/proposals/{proposal.id}/photo',
                data={
                    'file': SimpleUploadedFile(
                        'poster.svg',
                        b'<svg></svg>',
                        content_type='image/svg+xml',
                    )
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'proposals.invalidImage')

    def test_non_owner_cannot_upload_proposal_image(self) -> None:
        proposal = self._create_proposal()

        with TemporaryDirectory() as tmp_media, override_settings(MEDIA_ROOT=tmp_media):
            self.client.force_login(self.other_user)
            response = self.client.post(
                f'/api/v1/proposals/{proposal.id}/photo',
                data={
                    'file': SimpleUploadedFile(
                        'poster.png',
                        b'proposal-image',
                        content_type='image/png',
                    )
                },
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'auth.permissionDenied')

