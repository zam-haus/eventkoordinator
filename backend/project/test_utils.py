"""
Test utilities for Django Playwright end-to-end tests.

Usage in a test file::

    from project.test_utils import ViteStaticLiveServerTestCase

    class MyPlaywrightTest(ViteStaticLiveServerTestCase):
        def test_homepage(self):
            with sync_playwright() as p:
                browser = p.chromium.launch(**playwright_launch_options())
                page = browser.new_page()
                page.goto(self.live_server_url)
                ...

``ViteStaticLiveServerTestCase`` guarantees that:

1. ``npm run build`` has been run (or re-used from a cached build) before any
   test in the class executes.
2. The Vite ``dist/`` output is copied into Django's ``STATIC_ROOT`` via
   ``collectstatic``.
3. Django's ``StaticLiveServerTestCase`` serves both the API and all static
   assets on a single port, so Playwright doesn't need a separate dev server.
4. Any URL that doesn't match a Django urlconf entry is answered with the
   built ``index.html`` (SPA fallback), allowing React-Router to handle
   client-side routing.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import shutil
import subprocess
import textwrap
from contextlib import contextmanager
from pathlib import Path

import playwright.sync_api
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings, TestCase
from icecream import ic
from playwright._impl._errors import TargetClosedError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

# Two levels above backend/ → repo root, then into backend/test_aria_snapshots
_BACKEND_DIR: Path = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR: Path = _BACKEND_DIR / "test_aria_snapshots"


def playwright_launch_options() -> dict:
    """Return Playwright launch kwargs derived from Django settings.

    Reads ``settings.PLAYWRIGHT_HEADLESS`` so the same test code runs
    headless in CI and with a visible browser window in development.
    """
    return {
        "headless": getattr(settings, "PLAYWRIGHT_HEADLESS", True),
    }


def wait_for_loading_indicators_to_disappear(
    page: playwright.sync_api.Page, *, timeout_ms: int = 10000
) -> None:
    """Wait until no visible UI element starts with ``Loading``.

    Proposal-related Playwright tests use visible loading text while async
    lookup options are being fetched. Waiting for that text to disappear keeps
    snapshots and interactions stable across slower environments.
    """
    page.wait_for_function(
        """
        () => {
          const hasVisibleLoading = Array.from(document.querySelectorAll('body *')).some((el) => {
            if (!(el instanceof HTMLElement)) return false;
            const text = (el.innerText || '').trim();
            if (!/^(Loading|Processing)/i.test(text)) return false;
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          });
          return !hasVisibleLoading;
        }
        """,
        timeout=timeout_ms,
    )


class SnapshotMixin:
    """Mixin that names screenshot files after the running test.

    The file name is derived from ``self.id()`` and, when called from
    inside ``subTest(...)``, includes a sanitized subTest suffix so each case
    writes to a distinct file.

    Usage::

        class MyTest(SnapshotMixin, SomeTestCase):
            def test_something(self):
                page.screenshot(path=self._snapshot_path().with_suffix(".png"))
    """

    @staticmethod
    def _sanitize_snapshot_fragment(fragment: str) -> str:
        """Convert a free-form subTest description into a safe filename part."""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", fragment).strip("_")

    def _snapshot_id(self) -> str:
        """Return a stable id for snapshots, including active subTest context."""
        base_id = self.id()
        subtest = getattr(self, "_subtest", None)
        if subtest is None:
            return base_id

        # unittest._SubTest.id() starts with the parent test id and then a
        # human-readable subTest description, e.g. "...test_x (role='staff')".
        subtest_id = subtest.id()
        if not subtest_id.startswith(base_id):
            return base_id

        suffix = subtest_id[len(base_id) :].strip()
        safe_suffix = self._sanitize_snapshot_fragment(suffix)
        if not safe_suffix:
            return base_id
        return f"{base_id}__{safe_suffix}"

    def _snapshot_path(self) -> Path:
        """Return the snapshot file path derived from the test id.

        Files are placed in ``backend/test_aria_snapshots/`` and named after
        ``self.id()``. When called inside ``subTest(...)`` the name also
        includes a sanitized subTest suffix to keep each case unique.
        """
        filename = f"{self._snapshot_id()}.aria.txt"
        return SNAPSHOT_DIR / filename

    @contextmanager
    def snapshotted_stage(self, page: playwright.async_api.Page, stagename: str):
        with self.subTest(stagename):
            with print_aria_on_timeout(page):
                try:
                    yield
                finally:
                    try:
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(f".{stagename}.png")
                        )
                    except TargetClosedError:
                        logger.warning(
                            "Failed to take snapshot for stage because page was closed: %s",
                            stagename,
                        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Root of the repository (two levels above backend/)
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# Where ``npm run build`` writes its output
VITE_DIST_DIR: Path = _REPO_ROOT / "dist"

# Django STATIC_ROOT – where collectstatic writes its output
_STATIC_ROOT: Path = Path(settings.STATIC_ROOT)


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------


def build_vite(*, force: bool = False) -> None:
    """Run ``npm run build`` in the repository root.

    The build is skipped when *force* is ``False`` and ``dist/index.html``
    already exists, so repeated test runs don't rebuild from scratch unless
    the caller explicitly requests it.

    Raises ``subprocess.CalledProcessError`` if the build fails.
    """
    index_html = VITE_DIST_DIR / "index.html"
    if not force and index_html.exists():
        logger.debug("Vite dist/ already present – skipping build.")
        return

    logger.info("Running npm run build in %s …", _REPO_ROOT)
    subprocess.run(
        ["npm", "run", "build"],
        cwd=_REPO_ROOT,
        check=True,
        # VITE_DJANGO_BASE tells vite.config.ts to set base='/static/spa/'
        # so built asset paths match Django's staticfiles URL prefix.
        env={**os.environ, "VITE_DJANGO_BASE": "1"},
        # Capture output so it doesn't clutter test output; on failure
        # CalledProcessError will include stdout/stderr.
        capture_output=True,
        text=True,
    )
    logger.info("Vite build finished.")


def populate_static_root(*, vite_dist: Path = VITE_DIST_DIR) -> None:
    """Copy the Vite build output into Django's STATIC_ROOT.

    The files are placed under ``STATIC_ROOT/spa/`` so they live alongside
    any other collected static files without collision.  The destination
    directory is wiped before copying so stale assets are never served.
    """
    dest = _STATIC_ROOT / "spa"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src=vite_dist, dst=dest, dirs_exist_ok=True)
    logger.info("Copied Vite dist → %s", dest)


# ---------------------------------------------------------------------------
# Test case
# ---------------------------------------------------------------------------


class ViteStaticLiveServerTestCase(StaticLiveServerTestCase):
    """``StaticLiveServerTestCase`` that builds the Vite SPA before tests run.

    The build + copy step runs once per *class* (``setUpClass``), not once per
    test, to keep the suite fast.

    ``StaticLiveServerTestCase`` serves static files through Django's
    *finders* (not from ``STATIC_ROOT``), so the Vite ``dist/`` directory
    is added to ``STATICFILES_DIRS`` with the ``spa`` prefix via
    ``override_settings``.  This ensures the ``FileSystemFinder`` can
    resolve URLs like ``/static/spa/assets/…`` and that the finder cache
    is properly cleared and restored.

    Override ``vite_force_rebuild = True`` on a subclass to always rebuild::

        class MyTest(ViteStaticLiveServerTestCase):
            vite_force_rebuild = True
    """

    #: Set to True to force a fresh ``npm run build`` even if dist/ exists.
    vite_force_rebuild: bool = False

    @classmethod
    def setUpClass(cls) -> None:
        build_vite(force=cls.vite_force_rebuild)
        populate_static_root()

        # StaticLiveServerTestCase serves files via staticfiles *finders*,
        # not from STATIC_ROOT.  Use override_settings so Django's
        # setting_changed signal clears the finder cache and the
        # FileSystemFinder picks up the Vite dist directory.
        # enterClassContext applies the override before super().setUpClass()
        # starts the live-server thread and reverses it in tearDownClass.
        cls.enterClassContext(
            override_settings(
                STATICFILES_DIRS=list(getattr(settings, "STATICFILES_DIRS", []))
                + [
                    ("spa", str(VITE_DIST_DIR)),
                ],
            )
        )

        super().setUpClass()


@contextlib.contextmanager
def print_aria_on_timeout(page: playwright.sync_api.Page):
    """Context manager to print ARIA snapshots on timeout exceptions.

    Use this to wrap any block of code where a Playwright timeout might occur
    and you want to capture the current state of the page for debugging::

        with print_aria_on_timeout():
            page.click("button#submit")
            page.wait_for_selector("#result")

    If a TimeoutError is raised inside the block, the context manager will
    catch it, print the current ARIA snapshot (if available), and re-raise
    the exception so the test still fails as expected.
    """
    try:
        yield
    except playwright.sync_api.TimeoutError as e:
        logger.error(
            f"Unexpected page after error {e!r} looks like this:\n{textwrap.indent(page.locator('body').aria_snapshot(), prefix='    ')}"
        )
        raise
