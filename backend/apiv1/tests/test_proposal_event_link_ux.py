"""Playwright UX tests for proposal-event linking flow."""

from __future__ import annotations

import logging
import re
from datetime import date

from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.models import Permission
from playwright.sync_api import Page, sync_playwright, expect

from apiv1.models.basedata import (
    ProposalArea,
    ProposalLanguage,
    Series,
    SubmissionType,
    Call,
)
from apiv1.tests.test_proposal_ux import ProposalNavigationMixin
from project.test_utils import (
    SnapshotMixin,
    ViteStaticLiveServerTestCase,
    playwright_launch_options,
    print_aria_on_timeout,
    wait_for_loading_indicators_to_disappear,
)

logger = logging.getLogger(__name__)


class ProposalEventLinkUxTest(ProposalNavigationMixin, SnapshotMixin, ViteStaticLiveServerTestCase):
    """Covers the flow: accept a proposal -> create event from proposal -> verify linked events."""

    vite_force_rebuild = True

    def setUp(self) -> None:
        super().setUp()

        self.username = "proposaleventux-user"
        self.password = "password123"

        User = get_user_model()
        User.objects.filter(username=self.username).delete()
        user, _ = User.objects.get_or_create(
            username=self.username,
            defaults={"email": f"{self.username}@example.com"},
        )
        user.set_password(self.password)
        user.save(update_fields=["password"])
        self.assertIsNotNone(
            authenticate(username=self.username, password=self.password)
        )

        required_permission_codenames = [
            "add_proposal",
            "change_proposal",
            "view_proposal",
            "browse_proposal",
            "submit_proposal",
            "accept_proposal",
            "reject_proposal",
            "revise_proposal",
            "add_event",
            "change_event",
            "view_event",
            "add_series",
            "change_series",
            "view_series",
            "browse_series",
        ]
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=required_permission_codenames)
        )

        SubmissionType.objects.get_or_create(
            code="workshop",
            defaults={"label": "Workshop", "description": "Workshop format"},
        )
        ProposalArea.objects.get_or_create(
            code="woodworking",
            defaults={"label": "Woodworking", "description": "Wood workshop"},
        )
        ProposalLanguage.objects.get_or_create(
            code="de",
            defaults={"label": "German", "description": "German language"},
        )

        # Create a series for linking events to
        self.test_series = Series.objects.create(
            name="Test Workshop Series",
            description="A series for testing event linking",
        )

        # Create an active call so the homepage shows a "Submit proposal" button.
        self.call, _ = Call.objects.get_or_create(
            title="UX Test Call",
            defaults={
                "description": "A call created for UX testing",
                "execution_period_start": date(2026, 3, 1),
                "execution_period_end": date(2026, 4, 30),
                "submission_deadline": date(2026, 9, 15),
                "print_deadline": date(2026, 9, 20),
                "responsible_name": "Test Responsible",
                "responsible_email": "test@example.com",
                "is_active": True,
            },
        )
        logger.debug(f"Created call: {self.call.title}")

    def tearDown(self) -> None:
        User = get_user_model()
        User.objects.filter(username=self.username).delete()
        super().tearDown()

    def _login_via_navbar(self, page: Page, base_url: str) -> None:
        page.goto(base_url + "/")
        page.get_by_role("button", name="User menu").click()
        page.get_by_role("form", name="Login form").wait_for(timeout=5000)
        page.get_by_label("Username").fill(self.username)
        page.get_by_label("Password").fill(self.password)
        with page.expect_response(
            lambda response: response.url.endswith("/api/v1/authenticate")
            and response.status == 200,
            timeout=5000,
        ):
            page.get_by_role("button", name="Login", exact=True).click()

        page.get_by_text(self.username, exact=True).wait_for(timeout=5000)

    def _create_and_accept_proposal(self, page: Page, base_url: str) -> None:
        """Create a proposal, fill required fields, save, submit, and accept it."""

        with self.snapshotted_stage(page, "create"):
            self.navigate_from_home_to_proposaleditor(page)
            self.navigate_create_new_proposal(page)
            self.navigate_fill_a_proposal(page)

        with self.snapshotted_stage(page, "save"):
            self.navigate_save_a_proposal(page)

        with self.snapshotted_stage(page, "submit"):
            self.navigate_from_proposaleditor_last_tab_to_submit_proposal(page)

        with self.snapshotted_stage(page, "accept"):
            accept_button = page.get_by_role(
                "button",
                name=re.compile(r"^Accept proposal$", re.IGNORECASE),
            )
            accept_button.wait_for(timeout=5000)
            accept_button.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            wait_for_loading_indicators_to_disappear(page)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            wait_for_loading_indicators_to_disappear(page)
            page.get_by_role("button", name="Next →").click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            wait_for_loading_indicators_to_disappear(page)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(500)
            wait_for_loading_indicators_to_disappear(page)

    def test_proposal_event_link_flow(self) -> None:
        """Test the complete flow: accept proposal, create event, verify linked events list."""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)
                    self._create_and_accept_proposal(page, base_url)

                    with self.snapshotted_stage(page, "accepted_with_linked_events_section"):
                        # After acceptance, linked events section should appear
                        wait_for_loading_indicators_to_disappear(page)
                        page.get_by_text("Linked Events (0)").wait_for(timeout=5000)
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".png")
                        )

                    with self.snapshotted_stage(page,"create_event_from_proposal"):
                        # Select the series from the dropdown
                        wait_for_loading_indicators_to_disappear(page)
                        page.get_by_role("combobox", name="Series").select_option(
                            label=self.test_series.name
                        )
                        page.wait_for_timeout(500)

                        # Click "Create New Event" button
                        page.get_by_role("button", name="Create New Event").click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(500)

                        # Should navigate to the event editor page
                        page.get_by_text("Edit Event").wait_for(timeout=5000)
                        # Should show proposal info on the left
                        page.get_by_role(
                            "heading", name="My Title", exact=True
                        ).wait_for(timeout=5000)
                        page.get_by_text("Number of Days").wait_for(timeout=5000)
                        page.get_by_label("Status of My Title: draft").first.wait_for(timeout=5000)
                        page.wait_for_load_state("networkidle")
                        wait_for_loading_indicators_to_disappear(page)
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".png")
                        )

                    with self.snapshotted_stage(page, "verify_linked_event_in_proposal"):
                        # Go back to proposal
                        page.get_by_role("link", name="← Back").click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(500)

                        # Should show 1 linked event
                        wait_for_loading_indicators_to_disappear(page)
                        page.get_by_role("tab", name="Date Arrangement").click()
                        page.get_by_text("Linked Events (1)").wait_for(
                            timeout=5000
                        )
                        page.wait_for_load_state("networkidle")
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".png")
                        )

            finally:
                browser.close()


