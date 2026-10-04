"""
Playwright end-to-end tests for the SPA.

Run with::

    python manage.py test project.tests

A screenshot of the homepage is written to ``backend/test_aria_snapshots/``.
"""

from __future__ import annotations

import logging

from playwright.sync_api import sync_playwright

from project.test_utils import SnapshotMixin, ViteStaticLiveServerTestCase, playwright_launch_options

logger = logging.getLogger(__name__)


class SpaAriaSnapshotTests(SnapshotMixin, ViteStaticLiveServerTestCase):
    """Open the SPA and capture a screenshot."""

    vite_force_rebuild = True

    def test_homepage_aria_snapshot(self) -> None:
        """Navigate to the root URL and write a screenshot to disk."""
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
                page.locator("body").screenshot(path=self._snapshot_path().with_suffix(".homepage.png"))
            finally:
                browser.close()

