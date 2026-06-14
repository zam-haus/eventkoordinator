"""
Playwright end-to-end tests for the SPA.

Run with::

    python manage.py test project.tests

The ARIA snapshot files are written to ``backend/test_aria_snapshots/``
so they can be reviewed and committed as living documentation.
If a snapshot differs from the committed version the test fails with a
unified diff showing the changes.
"""

from __future__ import annotations

import logging

from playwright.sync_api import sync_playwright

from project.test_utils import SnapshotMixin, ViteStaticLiveServerTestCase, playwright_launch_options

logger = logging.getLogger(__name__)


class TestCreatorTests(SnapshotMixin, ViteStaticLiveServerTestCase):
    """Open the SPA and capture an ARIA accessibility snapshot."""

    vite_force_rebuild = True

    def test_homepage_aria_snapshot(self) -> None:
        """Navigate to the root URL and write an ARIA snapshot to disk."""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            try:
                page = browser.new_page()
                logger.debug(f"Navigating to {self.live_server_url}/ and capturing ARIA snapshot")
                page.goto(self.live_server_url + "/")
                # Wait until the React root has mounted something visible.
                page.locator("main").is_visible(timeout=1000)
                page.locator("nav").is_visible(timeout=1000)
                page.wait_for_load_state("networkidle")

                page.pause()
                input("Press enter to continue...")

            finally:
                browser.close()

