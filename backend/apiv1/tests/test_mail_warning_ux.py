"""Playwright UX tests for the mail warning confirmation dialogs.

Moderator actions that mail the proposal owner, and review requests that mail a
reviewer, must ask for confirmation first and describe the mail that is about to
be sent.  Dismissing the dialog must leave both the proposal and the mail queue
untouched.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from django.contrib.auth.models import Permission
from django.contrib.auth import authenticate
from django.core import mail
from playwright.sync_api import Page, sync_playwright

from apiv1.models.basedata import (
    Call,
    Proposal,
    ProposalArea,
    ProposalLanguage,
    SubmissionType,
)
from openid_user_management.models import OpenIDUser
from project.test_utils import (
    ViteStaticLiveServerTestCase,
    playwright_launch_options,
    print_aria_on_timeout,
    wait_for_loading_indicators_to_disappear,
)

logger = logging.getLogger(__name__)


class MailWarningUxPlaywrightTest(ViteStaticLiveServerTestCase):
    """Rejecting a proposal and requesting a review both warn about the mail."""

    def setUp(self) -> None:
        super().setUp()

        self.username = "mailwarning-moderator"
        self.password = "password123"
        OpenIDUser.objects.filter(username=self.username).delete()
        # Moderators see other people's proposals through the superuser short-circuit
        # in PermissionsMixin.has_perm; object-level view_proposal is owner/reviewer only.
        self.moderator = OpenIDUser.objects.create(
            username=self.username,
            email="mailwarning-moderator@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.moderator.set_password(self.password)
        self.moderator.save()
        self.assertIsNotNone(authenticate(username=self.username, password=self.password))
        self.moderator.user_permissions.add(
            *Permission.objects.filter(
                codename__in=[
                    "view_proposal",
                    "change_proposal",
                    "browse_proposal",
                    "reject_proposal",
                    "revise_proposal",
                    "accept_proposal",
                    "moderate_proposal",
                ]
            )
        )

        self.reviewer = OpenIDUser.objects.create(
            username="mailwarning-reviewer", email="mailwarning-reviewer@example.com"
        )
        self.owner = OpenIDUser.objects.create(
            username="mailwarning-owner", email="mailwarning-owner@example.com"
        )

        submission_type, _ = SubmissionType.objects.get_or_create(
            code="workshop", defaults={"label": "Workshop"}
        )
        area, _ = ProposalArea.objects.get_or_create(
            code="woodworking", defaults={"label": "Woodworking"}
        )
        language, _ = ProposalLanguage.objects.get_or_create(
            code="de", defaults={"label": "German"}
        )
        self.call, _ = Call.objects.get_or_create(
            title="Mail Warning Call",
            defaults={
                "description": "A call created for mail warning testing",
                "execution_period_start": date(2026, 3, 1),
                "execution_period_end": date(2026, 4, 30),
                "submission_deadline": date(2026, 9, 15),
                "print_deadline": date(2026, 9, 20),
                "responsible_name": "Test Responsible",
                "responsible_email": "responsible@example.com",
                "is_active": True,
            },
        )
        self.proposal = Proposal.objects.create(
            title="Mail Warning Proposal",
            status=Proposal.Status.SUBMITTED,
            call=self.call,
            submission_type=submission_type,
            area=area,
            language=language,
            abstract="This submitted proposal exists only to exercise the mail warning dialogs.",
            description="It carries enough detail to satisfy model validation during the mail warning UX test.",
            occurrence_count=1,
            duration_days=1,
            duration_time_per_day="02:00",
            max_participants=8,
            material_cost_eur="0.00",
            preferred_dates="2026-09-10",
            owner=self.owner,
        )

    def _login_via_navbar(self, page: Page, base_url: str) -> None:
        page.goto(base_url + "/")
        page.get_by_role("button", name="User menu").click()
        page.get_by_role("form", name="Login form").wait_for(timeout=2000)
        page.get_by_label("Username").fill(self.username)
        page.get_by_label("Password").fill(self.password)
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/v1/authenticate") and response.status == 200
            ),
            timeout=5000,
        ):
            page.get_by_role("button", name="Login", exact=True).click()
        page.get_by_text(self.username, exact=True).wait_for(timeout=2000)

    def _open_submission_tab(self, page: Page, base_url: str) -> None:
        page.goto(f"{base_url}/proposal-editor/{self.proposal.pk}?tab=4")
        page.get_by_role("form", name="Proposal editor").wait_for(timeout=10000)
        wait_for_loading_indicators_to_disappear(page)

    def test_reject_warns_about_the_owner_mail(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()
                    self._login_via_navbar(page, base_url)
                    self._open_submission_tab(page, base_url)

                    reject_button = page.get_by_role("button", name="Reject Proposal")
                    reject_button.wait_for(timeout=10000)

                    # Dismissing the dialog must not change anything.
                    messages: list[str] = []

                    def _dismiss(dialog) -> None:
                        messages.append(dialog.message)
                        dialog.dismiss()

                    page.once("dialog", _dismiss)
                    reject_button.click()
                    page.wait_for_timeout(500)

                    self.assertTrue(messages, "No confirmation dialog was shown for Reject Proposal")
                    self.assertRegex(messages[0], r"e-mail will be sent to the proposal owner")
                    # Still submitted: the dismissed dialog cancelled the transition.
                    page.get_by_role(
                        "heading", name="Submitted – under review"
                    ).wait_for(timeout=2000)

                    # Accepting it performs the transition and queues the mail.
                    page.once("dialog", lambda dialog: dialog.accept())
                    with page.expect_response(
                        lambda response: (
                            re.search(r"/api/v1/proposals/.*/reject$", response.url) is not None
                        ),
                        timeout=10000,
                    ):
                        reject_button.click()
                    page.wait_for_timeout(500)
            finally:
                browser.close()

        # ORM access must happen outside the Playwright (async) context.
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, Proposal.Status.REJECTED)
        owner_mails = [m for m in mail.outbox if self.owner.email in m.to]
        self.assertEqual(
            len(owner_mails),
            1,
            "Exactly one rejection mail should be sent — the dismissed dialog must send none",
        )
        self.assertIn("abgelehnt", owner_mails[0].subject)

    def test_submit_button_is_greyed_out_for_a_non_author(self) -> None:
        """A superuser moderator sees Submit Proposal, but disabled."""
        self.proposal.status = Proposal.Status.DRAFT
        self.proposal.save(update_fields=["status"])

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()
                    self._login_via_navbar(page, base_url)
                    self._open_submission_tab(page, base_url)

                    submit_button = page.get_by_role("button", name="Submit Proposal")
                    submit_button.wait_for(timeout=10000)
                    self.assertFalse(submit_button.is_enabled())
                    self.assertIn("on behalf", submit_button.get_attribute("title") or "")
            finally:
                browser.close()

    def test_review_request_warns_about_the_reviewer_mail(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()
                    self._login_via_navbar(page, base_url)
                    self._open_submission_tab(page, base_url)

                    picker = page.get_by_role(
                        "combobox", name=re.compile("Search a person or area", re.IGNORECASE)
                    )
                    picker.wait_for(timeout=10000)
                    picker.fill("mailwarning-reviewer")
                    page.get_by_text("mailwarning-reviewer", exact=True).first.click()

                    messages: list[str] = []

                    def _dismiss(dialog) -> None:
                        messages.append(dialog.message)
                        dialog.dismiss()

                    page.once("dialog", _dismiss)
                    page.get_by_role("button", name=re.compile("Request review")).click()
                    page.wait_for_timeout(500)

                    self.assertTrue(messages, "No confirmation dialog was shown for the review request")
                    self.assertIn("mailwarning-reviewer", messages[0])
                    self.assertRegex(messages[0], r"e-mail asking them to review")
            finally:
                browser.close()

        self.assertEqual(
            mail.outbox, [], "Dismissing the review request dialog must not send any mail"
        )
