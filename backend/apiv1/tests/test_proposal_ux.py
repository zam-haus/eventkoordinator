"""Playwright UX snapshots for the proposal flow."""

from __future__ import annotations

import logging
import os
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.models import Permission
from django.test import tag
from dotenv.cli import unset
from playwright.sync_api import Page, sync_playwright, expect

from datetime import date

from apiv1.models.basedata import (
    Call,
    Proposal,
    ProposalArea,
    ProposalLanguage,
    SubmissionType,
)
from apiv1.tests.util_imggen import write_large_noise_png
from openid_user_management.models import OpenIDUser
from project.test_utils import (
    SnapshotMixin,
    ViteStaticLiveServerTestCase,
    playwright_launch_options,
    print_aria_on_timeout,
    wait_for_loading_indicators_to_disappear,
)


logger = logging.getLogger(__name__)

class ProposalNavigationMixin:

    def wait_some_more(self, page: Page, delay=100):
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(delay)

    def wait_for_proposal_dropdowns_enabled(self, page: Page):
        expect(page.get_by_label("Submission Type")).to_be_enabled()
        expect(page.get_by_label("Area (optional)")).to_be_enabled()
        expect(page.get_by_label("Language", exact=True)).to_be_enabled()

    def navigate_from_proposaleditor_last_tab_to_submit_proposal(self, page: Page):
        logger.debug("Navigating from proposal editor last tab to submit proposal")
        page.get_by_role("button", name="Submit proposal").click()
        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_some_more(page)
        logger.debug("Proposal submitted successfully")

    def navigate_save_a_proposal(self, page: Page):
        logger.debug("Saving proposal")
        page.get_by_role("button", name="Save Proposal").click()
        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_some_more(page)
        logger.debug("Proposal saved successfully")

    def navigate_fill_a_proposal(self, page: Page):
        logger.info("Starting to fill out proposal form")
        with self.snapshotted_stage(page, "modify-page1"):
            logger.debug("Filling in title")
            page.get_by_role(
                "textbox", name="Title (max 30 characters)"
            ).fill("My Title")

            logger.debug("Filling in call")
            page.get_by_label("Call").select_option("UX Test Call")

            logger.debug("Filling in workshop")
            page.get_by_label("Submission Type").select_option("workshop")

            logger.debug("Filling in area")
            page.get_by_label("Area (optional)").select_option(
                "woodworking"
            )

            logger.debug("Confirming copyright")
            page.get_by_role("checkbox",name="I will only upload images that I hold the copyright of and permit the ZAM to use publicly").click()

            with TemporaryDirectory() as tmp_dir:
                image_path = Path(tmp_dir) / "tiny.png"
                write_large_noise_png(image_path)
                page.get_by_role("button", name="Proposal Image").set_input_files(str(image_path))

            logger.debug("Filling in lang")
            page.get_by_label("Language", exact=True).select_option("de")

            logger.debug("Filling in abstract")
            page.get_by_role(
                "textbox", name="Abstract (50-250 characters)"
            ).fill("Some Abstract " * 4)

            logger.debug("Filling in desc")
            page.get_by_role("textbox", name="Description (50-1000").fill(
                "Some Description " * 8
            )

        with self.snapshotted_stage(page, "modify-page2"):
            logger.debug("go to page 2")
            page.get_by_role("button", name="Next →").click()

            logger.debug("click basic course")
            page.get_by_text("This workshop is a basic").click()

            logger.debug("fill in participant number")
            page.get_by_role(
                "spinbutton", name="Max. number of participants"
            ).fill("20")

            logger.debug("fill in cost")
            page.get_by_role(
                "spinbutton", name="Material cost per participant"
            ).fill("24")

        with self.snapshotted_stage(page, "modify-page3"):
            logger.debug("go to page 3")
            page.get_by_role("button", name="Next →").click()

            logger.debug("fill in count of days")
            page.get_by_role("spinbutton", name="Number of Days").fill("1")

            logger.debug("fill in duration of days")
            page.get_by_role("textbox", name="Duration per day (HH:MM or minutes)").fill(
                "1:30"
            )

            logger.debug("fill in occurrence count")
            page.get_by_role(
                "spinbutton", name="How often would you offer this event?"
            ).fill("1")

            logger.debug("fill in preferred dates")
            page.get_by_role("textbox", name="Preferred Dates, Time of Day and Alternatives").fill(
                "Always"
            )
        with self.snapshotted_stage(page, "modify-page4"):
            logger.debug("go to page 4")
            page.get_by_role("button", name="Next →").click()

            logger.debug("click edit speaker")
            page.get_by_role("button", name="Edit").click()

            logger.debug("fill in speaker name")
            page.get_by_role("textbox", name="Display Name:").fill(
                "proposalux-user Name"
            )
            logger.debug("fill in speaker bio")
            page.get_by_role("textbox", name="Biography:").fill("Bio for the speaker")

        with self.snapshotted_stage(page, "modify-page4b"):
            logger.debug("save speaker")
            page.get_by_role("button", name="Save", exact=True).click()
            logger.debug(page.locator("body").aria_snapshot())

        with self.snapshotted_stage(page, "modify-page5"):
            logger.debug("click next")
            page.get_by_role("button", name="Next →").click()

            logger.debug("fill internal notes")
            page.get_by_role(
                "textbox", name="Additional Information for the ZAM (optional)"
            ).fill("Internal Notes")

        logger.info("Proposal form filled out successfully")

    def navigate_create_new_proposal(self, page: Page):
        logger.debug("Creating new proposal")
        page.get_by_role("button", name="Create New Proposal").click()

        self.wait_some_more(page)
        wait_for_loading_indicators_to_disappear(page)
        self.wait_for_proposal_dropdowns_enabled(page)
        self.wait_some_more(page)
        logger.debug("New proposal form is ready")

    def navigate_from_home_to_proposaleditor(self, page: Page):
        logger.debug("Navigating from home to proposal editor")
        page.get_by_role(
            "button", name=re.compile(r"Submit proposal", re.IGNORECASE)
        ).click()
        # Wait until the proposal editor page loads.
        page.get_by_role("heading", name="Proposal Editor").wait_for(timeout=5000)
        logger.debug("Proposal editor page loaded")

class ProposalUxPlaywrightTest(ProposalNavigationMixin, SnapshotMixin, ViteStaticLiveServerTestCase):
    """Covers create -> save -> submit proposal flow with stage snapshots."""

    vite_force_rebuild = True

    def setUp(self) -> None:
        logger.info("=== Starting setUp for ProposalUx tests ===")
        super().setUp()

        self.username = "proposalux-user"
        logger.debug(f"Test username: {self.username}")
        self.password = "password123"

        User = OpenIDUser
        User.objects.filter(username=self.username).delete()
        user, _ = User.objects.get_or_create(
            username=self.username,
            defaults={"email": f"{self.username}@example.com"},
        )
        user.set_password(self.password)
        user.save(update_fields=["password"])
        logger.debug(f"Created user: {user}, {user.__dict__!r}")
        self.assertIsNotNone(
            authenticate(username=self.username, password=self.password)
        )
        logger.debug(f"Authentication succeeds with pass {self.password}")
        required_permission_codenames = [
            "add_proposal",
            "change_proposal",
            "view_proposal",
            "browse_proposal",
            "submit_proposal",
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
        logger.info("=== setUp completed successfully ===")

    def tearDown(self) -> None:
        Call.objects.filter(title="UX Test Call").delete()
        User = get_user_model()
        User.objects.filter(username=self.username).delete()
        logger.debug(f"Deleted user {self.username}")
        super().tearDown()

    def _login_via_navbar(self, page: Page, base_url: str) -> None:
        page.goto(base_url + "/")
        page.get_by_role("button", name="User menu").click()
        page.get_by_role("form", name="Login form").wait_for(timeout=500)
        page.get_by_label("Username").fill(self.username)
        page.get_by_label("Password").fill(self.password)
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/v1/authenticate") and response.status == 200
            ),
            timeout=1000,
        ):
            page.get_by_role("button", name="Login", exact=True).click()

        # Wait for UI state to reflect authenticated user.
        user_menu_button = page.get_by_role("button", name="User menu")
        page.get_by_text(self.username, exact=True).wait_for(timeout=500)

        # Open the menu only when needed to avoid toggle races.
        if user_menu_button.get_attribute("aria-expanded") != "true":
            user_menu_button.click()
        page.get_by_role("menuitem", name="Logout").wait_for(timeout=500)

    def _log_field_step(self, label: str) -> None:
        logger.info("Proposal form step: %s", label)

    @unittest.skipUnless("--tag=manual_record" in sys.argv, "Test generation utility")
    @tag("manual_record")
    def test_manual_record(self):
        pwdebug_prev = os.environ.get("PWDEBUG")
        os.environ["PWDEBUG"] = "1"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**playwright_launch_options())
                page = browser.new_page()
                self._login_via_navbar(page, self.live_server_url)
                try:
                    with print_aria_on_timeout(page):
                        self.wait_some_more(page)
                        page.pause()
                finally:
                    browser.close()
        finally:
            if pwdebug_prev is not None:
                os.environ["PWDEBUG"] = pwdebug_prev
            else:
                os.environ.pop("PWDEBUG")

    def test_create_save_submit_proposal(self) -> None:
        logger.info("=== Starting test_create_save_submit_proposal ===")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            logger.debug("Browser launched")
            page = browser.new_page()
            page.set_viewport_size({"width": 1080, "height": 1920})
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)

                    with self.snapshotted_stage(page, "create"):
                        self.navigate_from_home_to_proposaleditor(page)
                        self.navigate_create_new_proposal(page)

                    self.navigate_fill_a_proposal(page)

                    with self.snapshotted_stage(page, "save"):
                        self.navigate_save_a_proposal(page)

                    with self.snapshotted_stage(page, "submit"):
                        self.navigate_from_proposaleditor_last_tab_to_submit_proposal(page)

            finally:
                logger.debug("Closing browser")
                browser.close()
        logger.info("=== Completed test_create_save_submit_proposal ===")

    def test_delete_proposal_via_ui(self) -> None:
        logger.info("=== Starting test_delete_proposal_via_ui ===")
        user = OpenIDUser.objects.get(username=self.username)
        logger.debug(f"Retrieved user: {user.username}")
        draft_proposal = Proposal.objects.create(
            title="Delete Me Proposal",
            submission_type=SubmissionType.objects.get(code="workshop"),
            area=ProposalArea.objects.get(code="woodworking"),
            language=ProposalLanguage.objects.get(code="de"),
            abstract="This draft proposal exists only to exercise delete confirmation in the UI.",
            description="This draft proposal contains enough detail to satisfy model validation during the delete UX test.",
            internal_notes="",
            occurrence_count=1,
            duration_days=1,
            duration_time_per_day="02:00",
            is_basic_course=False,
            max_participants=8,
            material_cost_eur="0.00",
            preferred_dates="2026-09-10",
            has_building_access=False,
            owner=user,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)
                    page.get_by_role("list").get_by_text("Proposal Editor").click()
                    page.get_by_role("heading", name="Proposal Editor").wait_for(timeout=5000)
                    page.get_by_role("listbox", name="Proposals").get_by_role(
                        "option", name=re.compile(r"^Delete Me Proposal\b")
                    ).click()
                    page.get_by_role("form", name="Proposal editor").wait_for(
                        timeout=5000
                    )

                    with self.subTest(stage="before_delete"):
                        wait_for_loading_indicators_to_disappear(page)
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="after_delete"):
                        delete_proposal_msgs: list[str] = []

                        def _handle_delete_proposal_dialog(d) -> None:
                            delete_proposal_msgs.append(d.message)
                            d.accept()

                        page.once("dialog", _handle_delete_proposal_dialog)
                        page.get_by_role("button", name="Delete Proposal").click()
                        self.wait_some_more(page)
                        page.get_by_role("main", name="Proposals content").get_by_text(
                            "No proposals yet"
                        ).wait_for(timeout=1000)
                        self.assertTrue(
                            delete_proposal_msgs,
                            "No dialog was shown for delete proposal",
                        )
                        self.assertIn("Delete proposal", delete_proposal_msgs[0])
                        self.assert_snapshot(page.locator("body").aria_snapshot())
            finally:
                browser.close()

        self.assertFalse(
            Proposal.objects.filter(pk=draft_proposal.pk).exists(),
            "Proposal should be deleted after accepting the confirmation dialog",
        )
        logger.info("=== Completed test_delete_proposal_via_ui ===")
