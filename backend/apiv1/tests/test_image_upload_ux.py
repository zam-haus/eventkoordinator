"""Playwright coverage for proposal and speaker image uploads."""

from __future__ import annotations

import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skip

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from playwright.sync_api import Page, sync_playwright

from apiv1.models.basedata import ProposalArea, ProposalLanguage, SubmissionType
from apiv1.tests.util_imggen import write_large_noise_png
from project.test_utils import (
    ViteStaticLiveServerTestCase,
    playwright_launch_options,
    print_aria_on_timeout,
    wait_for_loading_indicators_to_disappear,
    SnapshotMixin,
)


class ImageUploadUxTest(ViteStaticLiveServerTestCase, SnapshotMixin):
    vite_force_rebuild = True

    def setUp(self) -> None:
        super().setUp()

        self.username = 'image-upload-ux-user'
        self.password = 'password123'

        user_model = get_user_model()
        user_model.objects.filter(username=self.username).delete()
        user, _ = user_model.objects.get_or_create(
            username=self.username,
            defaults={'email': f'{self.username}@example.com'},
        )
        user.set_password(self.password)
        user.save(update_fields=['password'])
        self.assertIsNotNone(authenticate(username=self.username, password=self.password))

        user.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    'add_proposal',
                    'change_proposal',
                    'view_proposal',
                    'browse_proposal',
                    'submit_proposal',
                ]
            )
        )

        SubmissionType.objects.get_or_create(
            code='workshop',
            defaults={'label': 'Workshop', 'description': 'Workshop format'},
        )
        ProposalArea.objects.get_or_create(
            code='woodworking',
            defaults={'label': 'Woodworking', 'description': 'Wood workshop'},
        )
        ProposalLanguage.objects.get_or_create(
            code='de',
            defaults={'label': 'German', 'description': 'German language'},
        )

    def tearDown(self) -> None:
        get_user_model().objects.filter(username=self.username).delete()
        super().tearDown()

    def _login_via_navbar(self, page: Page, base_url: str) -> None:
        page.goto(base_url + '/')
        page.get_by_role('button', name='User menu').click()
        page.get_by_role('form', name='Login form').wait_for(timeout=5000)
        page.get_by_label('Username').fill(self.username)
        page.get_by_label('Password').fill(self.password)
        with page.expect_response(
            lambda response: response.url.endswith('/api/v1/authenticate') and response.status == 200,
            timeout=5000,
        ):
            page.get_by_role('button', name='Login', exact=True).click()

        page.get_by_text(self.username, exact=True).wait_for(timeout=5000)

    def _create_saved_proposal_with_speaker(self, page: Page) -> None:
        page.get_by_role('button', name='Create a Proposal').click()
        page.get_by_role('main', name='Proposals content').wait_for(timeout=5000)
        page.get_by_role('button', name='Create New Proposal').click()
        page.get_by_role('form', name='Proposal editor').wait_for(timeout=5000)
        page.wait_for_load_state('networkidle')

        page.get_by_label('Title (max 30 characters)').fill('Image Upload Test')
        page.get_by_label('Submission Type').select_option('workshop')
        page.get_by_label('Area (optional)').select_option('woodworking')
        page.get_by_label('Language', exact=True).select_option('de')
        page.get_by_label('Abstract (50-250 characters)').fill(
            'A hands-on workshop introducing image uploads for proposal and speaker profiles.'
        )
        page.get_by_label('Description (50-1000 characters)').fill(
            'Participants will exercise proposal editing features while adding a speaker and uploading images through the new API endpoints.'
        )
        page.get_by_label('Number of Days').fill('1')
        page.get_by_label('Duration per Day (HH:MM or minutes)').fill('02:00')
        page.get_by_label('How often would you offer this event?').fill('1')

        page.locator('summary', has_text='Additional Information').click()
        page.get_by_label('Max. Number of Participants').fill('10')
        page.get_by_label('Preferred Date and Alternatives').fill('2026-08-10 to 2026-08-11')

        page.locator('summary', has_text='About Yourself').click()
        page.get_by_label('Email (required):').fill('speaker@example.com')
        page.get_by_label('Display Name:').fill('Sample Speaker')
        page.get_by_label('Biography:').fill(
            'Sample Speaker has many years of workshop facilitation and practical making experience.'
        )
        page.get_by_role('button', name='+ Add Speaker').click()
        page.get_by_text('Added Speakers (1)').wait_for(timeout=5000)

        page.get_by_role('button', name='Save Proposal').click()
        page.wait_for_load_state('networkidle')

    @skip("Broken, not adapted to new tabs yet.")
    def test_upload_fields_show_progress_and_preview_images(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / 'tiny.png'
            write_large_noise_png(image_path)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**playwright_launch_options())
                page = browser.new_page()
                try:
                    with print_aria_on_timeout(page):
                        base_url = self.live_server_url
                        if callable(base_url):
                            base_url = base_url()

                        self._login_via_navbar(page, base_url)
                        self._create_saved_proposal_with_speaker(page)

                        page.route(
                            re.compile(r'.*/api/v1/proposals/[^/]+/photo$'),
                            lambda route: (time.sleep(1), route.continue_()),
                        )
                        with page.expect_response(
                            lambda response: re.search(r'/api/v1/proposals/[^/]+/photo$', response.url) is not None
                            and response.status == 200,
                            timeout=5000,
                        ):
                            page.wait_for_load_state('networkidle')
                            wait_for_loading_indicators_to_disappear(page, timeout_ms=1000)
                            page.wait_for_timeout(500)
                            page.locator('#proposal-image-upload').set_input_files(str(image_path))
                            page.get_by_role('progressbar', name='Proposal image upload progress').wait_for(timeout=5000)
                            page.locator("body").screenshot(
                                path=self._snapshot_path().with_suffix(".upload.png")
                            )

                        page.get_by_role('img', name='Proposal image preview').wait_for(timeout=5000)
                        page.get_by_role('progressbar', name='Proposal image upload progress').wait_for(
                            state='hidden',
                            timeout=5000,
                        )

                        page.locator('summary', has_text='About Yourself').click()
                        page.get_by_text('Added Speakers (1)').wait_for(timeout=5000)
                        page.get_by_role('button', name='Edit').click()
                        page.route(
                            re.compile(r'.*/api/v1/proposals/[^/]+/speakers/[^/]+/profile-picture$'),
                            lambda route: (time.sleep(1), route.continue_()),
                        )
                        with page.expect_response(
                            lambda response: re.search(
                                r'/api/v1/proposals/[^/]+/speakers/[^/]+/profile-picture$',
                                response.url,
                            ) is not None and response.status == 200,
                            timeout=5000,
                        ):
                            page.locator('input[id^="speaker-image-"]').set_input_files(str(image_path))
                            page.get_by_role('progressbar', name='Speaker image upload progress').wait_for(timeout=5000)

                        page.get_by_role(
                            'img',
                            name='Speaker image preview for Sample Speaker',
                        ).wait_for(timeout=5000)
                        page.get_by_role('progressbar', name='Speaker image upload progress').wait_for(
                            state='hidden',
                            timeout=5000,
                        )
                finally:
                    browser.close()


