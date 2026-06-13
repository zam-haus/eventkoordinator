"""Playwright UX snapshots for the event / series coordinator flow.

Covers three flows:
1. Select a series and event pre-seeded in the DB, edit event data including a
   calendar drag-select, then save.
2. Create a new series via the UI, create a new event via the UI, edit it, and
   save.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Permission
from playwright.sync_api import Page, sync_playwright, expect

from apiv1.models.basedata import Call, Event, Series
from project import test_utils
from project.test_utils import (
    SnapshotMixin,
    ViteStaticLiveServerTestCase,
    playwright_launch_options,
    print_aria_on_timeout,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants that mirror WeekViewCombined.tsx – kept in sync manually.
# ---------------------------------------------------------------------------
HOUR_PX = 24  # pixels per hour
GUTTER_PX = 52  # width of the sticky time-label column


class EventUxPlaywrightTest(SnapshotMixin, ViteStaticLiveServerTestCase):
    """Coordinator: select / create → edit → calendar-drag → save."""

    vite_force_rebuild = True

    # ------------------------------------------------------------------
    # Test fixtures
    # ------------------------------------------------------------------

    def setUp(self) -> None:
        super().setUp()
        logger.info("Starting test: %s", self._testMethodName)

        self.username = "eventux-user"
        self.password = "eventux-pass-123"

        User = get_user_model()
        User.objects.filter(username=self.username).delete()
        user, _ = User.objects.get_or_create(
            username=self.username,
            defaults={"email": f"{self.username}@example.com"},
        )
        user.set_password(self.password)
        user.save(update_fields=["password"])
        logger.debug("Created user: %r", user)
        self.assertIsNotNone(
            authenticate(username=self.username, password=self.password)
        )

        # Permissions needed for the coordinator view and event editing.
        # - view_series  → list + fetch series via API; show "Coordinator" navbar link
        # - change_series → create events inside a series (create_event API gate)
        # - add_series   → create new series via the UI
        # - add_event    → create events via the UI
        # - view_event   → object-permission check in ContentRenderer
        # - change_event → edit event fields + save
        required_permission_codenames = [
            "view_series",
            "change_series",
            "add_series",
            "add_event",
            "view_event",
            "change_event",
        ]
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=required_permission_codenames)
        )

        # ------------------------------------------------------------------
        # DB-seeded series + event (used by test_select_series_and_event_flow)
        # ------------------------------------------------------------------
        self.series, _ = Series.objects.get_or_create(
            name="Ux Test Series",
            defaults={"description": "A series created for UX testing"},
        )

        # Event on Monday 2026-03-16 10:00–12:00 UTC so the calendar shows
        # a predictable week and the Monday column contains the seeded event.
        event_start = datetime(2026, 3, 16, 10, 0, tzinfo=timezone.utc)
        event_end = datetime(2026, 3, 16, 12, 0, tzinfo=timezone.utc)
        self.event, _ = Event.objects.get_or_create(
            name="Ux Test Event",
            series=self.series,
            defaults={
                "start_time": event_start,
                "end_time": event_end,
                "tag": "draft",
            },
        )

        # Create an active call so the homepage shows a "Go to Coordinator" button.
        self.call, _ = Call.objects.get_or_create(
            title="UX Test Call",
            defaults={
                "description": "A call created for UX testing",
                "execution_period_start": date(2026, 3, 1),
                "execution_period_end": date(2026, 4, 30),
                "submission_deadline": date(2026, 2, 15),
                "print_deadline": date(2026, 2, 20),
                "responsible_name": "Test Responsible",
                "responsible_email": "test@example.com",
                "is_active": True,
            },
        )

    def tearDown(self) -> None:
        Call.objects.filter(title="UX Test Call").delete()
        Event.objects.filter(series__name="Ux Test Series").delete()
        Series.objects.filter(name="Ux Test Series").delete()
        Event.objects.filter(series__name__startswith="New Series").delete()
        Series.objects.filter(name__startswith="New Series").delete()
        User = get_user_model()
        User.objects.filter(username=self.username).delete()
        super().tearDown()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _login_via_navbar(self, page: Page, base_url: str) -> None:
        page.goto(base_url + "/")
        logger.debug("Clicking User menu button")
        page.get_by_role("button", name="User menu").click()
        logger.debug("Waiting for Login form to appear")
        page.get_by_role("form", name="Login form").wait_for(timeout=5000)
        logger.debug("Filling Username input: %r", self.username)
        page.get_by_label("Username").fill(self.username)
        logger.debug("Filling Password input")
        page.get_by_label("Password").fill(self.password)
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/v1/authenticate") and response.status == 200
            ),
            timeout=5000,
        ):
            logger.debug("Clicking Login button")
            page.get_by_role("button", name="Login", exact=True).click()

        logger.debug("Waiting for username text to be visible: %r", self.username)
        page.get_by_text(self.username, exact=True).wait_for(timeout=500)

        user_menu_button = page.get_by_role("button", name="User menu")
        if user_menu_button.get_attribute("aria-expanded") != "true":
            logger.debug("Re-opening User menu to confirm Logout item is present")
            user_menu_button.click()
        logger.debug("Waiting for Logout menu item to be visible")
        page.get_by_role("menuitem", name="Logout").wait_for(timeout=500)
        if user_menu_button.get_attribute("aria-expanded") == "true":
            logger.debug("Closing User menu before navbar navigation")
            user_menu_button.click()
        page.wait_for_load_state("networkidle")
        logger.debug("Waiting for Coordinator link to be visible after login")
        page.get_by_role("link", name="Coordinator").wait_for(timeout=500)

    def _go_to_coordinator(self, page: Page, base_url: str) -> None:
        """Navigate to coordinator view from homepage by clicking the button under an active call."""
        logger.debug("Waiting for 'Go to Coordinator' button under the active call")
        goToCoordinatorButton = page.get_by_role(
            "button", name=re.compile(r"Go to Coordinator", re.IGNORECASE)
        )
        goToCoordinatorButton.wait_for(timeout=500)
        logger.debug("Clicking 'Go to Coordinator' button")
        goToCoordinatorButton.wait_for(state="visible", timeout=500)
        goToCoordinatorButton.click()
        logger.debug("Waiting for SPA pathname to start with /coordinator")
        page.wait_for_function(
            "() => window.location.pathname.startsWith('/coordinator')",
            timeout=500,
        )

        logger.debug("Waiting for Series listbox in coordinator view")
        page.get_by_role("listbox", name="Series").wait_for(timeout=500)

    def _drag_in_calendar(
        self, page: Page, start_hour: int = 10, end_hour: int = 13, col_index: int = 1
    ) -> None:
        """Drag within the calendar grid to pick a new time range.

        Drags from *start_hour* to *end_hour* in the day column at *col_index*
        (0 = Monday).  Default is Tuesday 10:00 → 13:00.

        The drag sets the event's start/end time via WeekViewCombined's
        ``onEventCreate`` callback which propagates into the form fields.
        """
        calendar_grid = page.get_by_label("Calendar grid")
        logger.debug("Scrolling calendar grid into view")
        calendar_grid.scroll_into_view_if_needed()
        page.wait_for_timeout(300)

        logger.debug("Resetting calendar scroll to top")
        # Reset scroll so pixel offsets are predictable.
        calendar_grid.evaluate("el => { el.scrollTop = 0; }")
        page.wait_for_timeout(200)

        box = calendar_grid.bounding_box()
        assert box is not None, "Calendar grid bounding box is None"

        # Measure the sticky header row height via JS (it stays at the top of
        # the scroll area regardless of scrollTop).
        header_h: float = calendar_grid.evaluate(
            "el => el.querySelector('[role=\"grid\"]')?.firstElementChild"
            "?.getBoundingClientRect()?.height || 26"
        )
        logger.debug("Measured calendar header height: %.1f px", header_h)

        # Horizontal layout: 7 equal columns after the gutter.
        col_width = (box["width"] - GUTTER_PX) / 7

        # Centre of the target column.
        x = box["x"] + GUTTER_PX + col_index * col_width + col_width * 0.5

        # Vertical: body starts immediately below the sticky header.
        body_top_y = box["y"] + header_h
        y_start = body_top_y + start_hour * HOUR_PX
        y_end = body_top_y + end_hour * HOUR_PX

        logger.debug(
            "Calendar drag: col=%d x=%.1f y_start=%.1f (hour %d) y_end=%.1f (hour %d)",
            col_index,
            x,
            y_start,
            start_hour,
            y_end,
            end_hour,
        )

        logger.debug("Mouse move to drag start (%.1f, %.1f)", x, y_start)
        page.mouse.move(x, y_start)
        logger.debug("Mouse down")
        page.mouse.down()
        logger.debug("Mouse move to drag end (%.1f, %.1f) in 15 steps", x, y_end)
        page.mouse.move(x, y_end, steps=15)
        logger.debug("Mouse up – drag complete")
        page.mouse.up()
        page.wait_for_timeout(400)

    # ------------------------------------------------------------------
    # Test 1: select a DB-seeded series + event, then edit with drag
    # ------------------------------------------------------------------

    def test_event_sidebar_shows_status_badge(self) -> None:
        """Coordinator sidebar shows the event lifecycle badge for listed events."""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            page.set_viewport_size({"width": 1600, "height": 900})
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)
                    self._go_to_coordinator(page, base_url)

                    series_listbox = page.get_by_role("listbox", name="Series")
                    series_listbox.get_by_text(self.series.name).click()
                    page.wait_for_load_state("networkidle")

                    events_listbox = page.get_by_role("listbox", name="Events")
                    events_listbox.wait_for(timeout=500)
                    events_listbox.get_by_text(self.event.name).wait_for(timeout=500)
                    events_listbox.locator(
                        f'[aria-label="Status of {self.event.name}: draft"]'
                    ).wait_for(timeout=500)
            finally:
                browser.close()

    def test_select_series_and_event_flow(self) -> None:
        """Select pre-seeded series/event, edit fields, drag-select time, save."""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            page.set_viewport_size({"width": 1600, "height": 900})
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)

                    self._go_to_coordinator(page, base_url)

                    with (
                        self.subTest(stage="select_series"),
                        print_aria_on_timeout(page),
                    ):
                        logger.debug("Waiting for Series listbox to appear")
                        series_listbox = page.get_by_role("listbox", name="Series")
                        series_listbox.wait_for(timeout=500)
                        logger.debug(
                            "Waiting for series name %r inside listbox",
                            self.series.name,
                        )
                        series_listbox.get_by_text(self.series.name).wait_for(
                            timeout=500
                        )
                        logger.debug("Clicking series: %r", self.series.name)
                        series_listbox.get_by_text(self.series.name).click()
                        page.wait_for_load_state("networkidle")
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".select_series.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with (
                        self.subTest(stage="select_event"),
                        print_aria_on_timeout(page),
                    ):
                        logger.debug("Waiting for Events listbox to appear")
                        events_listbox = page.get_by_role("listbox", name="Events")
                        events_listbox.wait_for(timeout=500)
                        logger.debug(
                            "Waiting for event name %r inside listbox", self.event.name
                        )
                        events_listbox.get_by_text(self.event.name).wait_for(
                            timeout=500
                        )
                        events_listbox.locator(
                            f'[aria-label="Status of {self.event.name}: draft"]'
                        ).wait_for(timeout=500)
                        logger.debug("Clicking event: %r", self.event.name)
                        events_listbox.get_by_text(self.event.name).click()
                        page.wait_for_load_state("networkidle")
                        logger.debug("Waiting for 'Edit event details' form to appear")
                        page.get_by_role("form", name="Edit event details").wait_for(
                            timeout=500
                        )
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".select_event.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="edit_fields"), print_aria_on_timeout(page):
                        logger.debug(
                            "Clearing and filling Name input: 'Updated Event Name'"
                        )
                        name_input = page.get_by_label("Name")
                        name_input.clear()
                        name_input.fill("Updated Event Name")

                        logger.debug("Clearing and filling Tag input: 'updated-tag'")
                        tag_input = page.get_by_label("Tag")
                        tag_input.clear()
                        tag_input.fill("updated-tag")

                        page.wait_for_timeout(300)
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".edit_fields.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with (
                        self.subTest(stage="drag_calendar"),
                        print_aria_on_timeout(page),
                    ):
                        logger.debug("Starting calendar drag: Tuesday col, 10:00→13:00")
                        self._drag_in_calendar(
                            page, start_hour=10, end_hour=13, col_index=1
                        )

                        logger.debug("Checking Start Time input value after drag")
                        start_value = page.get_by_label("Start Time").input_value()
                        logger.debug("Checking End Time input value after drag")
                        end_value = page.get_by_label("End Time").input_value()
                        logger.info(
                            "After calendar drag: start=%s  end=%s",
                            start_value,
                            end_value,
                        )
                        self.assertTrue(
                            start_value,
                            "Start Time input should be populated after drag",
                        )
                        self.assertTrue(
                            end_value,
                            "End Time input should be populated after drag",
                        )

                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".drag_calendar.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="save"), print_aria_on_timeout(page):
                        logger.debug("Clicking Save Changes button")
                        page.get_by_role("button", name="Save Changes").click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(500)
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".save.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

            finally:
                browser.close()

    def test_event_editor_uses_object_change_permission_instead_of_global_permission(self) -> None:
        """Users with change_series but without global change_event can still edit events."""
        user = get_user_model().objects.get(username=self.username)
        user.user_permissions.remove(Permission.objects.get(codename="change_event"))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            page.set_viewport_size({"width": 1600, "height": 900})
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)
                    self._go_to_coordinator(page, base_url)

                    page.get_by_role("listbox", name="Series").get_by_text(
                        self.series.name
                    ).click()
                    page.wait_for_load_state("networkidle")

                    page.get_by_role("listbox", name="Events").get_by_text(
                        self.event.name
                    ).click()
                    page.get_by_role("form", name="Edit event details").wait_for(
                        timeout=3000
                    )

                    name_input = page.get_by_label("Name")
                    self.assertTrue(name_input.is_enabled())

                    name_input.clear()
                    name_input.fill("Object Permission Edit")
                    save_button = page.get_by_role("button", name="Save Changes")
                    self.assertTrue(save_button.is_enabled())
                    save_button.click()
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(500)
                    self.assertEqual(name_input.input_value(), "Object Permission Edit")
                    page.get_by_role("listbox", name="Events").get_by_text(
                        "Object Permission Edit"
                    ).wait_for(timeout=1000)
            finally:
                browser.close()

    # ------------------------------------------------------------------
    # Test 2: create series + event via the UI, then edit with drag
    # ------------------------------------------------------------------

    def test_create_series_and_event_via_ui(self) -> None:
        """Create a series and an event via the UI, edit the event, drag, save."""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            page.set_viewport_size({"width": 1600, "height": 900})
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)

                    self._go_to_coordinator(page, base_url)

                    with (
                        self.subTest(stage="create_series"),
                        print_aria_on_timeout(page),
                    ):
                        logger.debug("Waiting for 'Create new series' button to appear")
                        create_series_btn = page.get_by_role(
                            "button", name="Create new series"
                        )
                        create_series_btn.wait_for(timeout=500)
                        logger.debug("Clicking 'Create new series' button")
                        create_series_btn.click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(500)

                        logger.debug("Waiting for Series listbox to reflect new series")
                        series_listbox = page.get_by_role("listbox", name="Series")
                        series_listbox.wait_for(timeout=500)

                        logger.debug("Waiting for Events listbox and General Information tab")
                        events_listbox = page.get_by_role("listbox", name="Events")
                        events_listbox.wait_for(timeout=500)
                        events_listbox.get_by_text("General Information").wait_for(
                            timeout=500
                        )
                        logger.debug("Waiting for series general editor to be visible")
                        page.get_by_role("form", name="Edit series").wait_for(
                            timeout=500
                        )

                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".create_series.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())
                    with (
                        self.subTest(stage="create_event"),
                        print_aria_on_timeout(page),
                    ):
                        logger.debug("Waiting for 'Create new event' button to appear")
                        create_event_btn = page.get_by_role(
                            "button", name="Create new event"
                        )
                        create_event_btn.wait_for(timeout=500)
                        logger.debug("Clicking 'Create new event' button")
                        create_event_btn.click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(500)

                        logger.debug("Waiting for 'Edit event details' form to appear")
                        page.get_by_role("form", name="Edit event details").wait_for(
                            timeout=500
                        )
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".create_event.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="edit_fields"), print_aria_on_timeout(page):
                        logger.debug(
                            "Clearing and filling Name input: 'My New Workshop'"
                        )
                        name_input = page.get_by_label("Name")
                        name_input.clear()
                        name_input.fill("My New Workshop")

                        logger.debug("Clearing and filling Tag input: 'workshop'")
                        tag_input = page.get_by_label("Tag")
                        tag_input.clear()
                        tag_input.fill("workshop")

                        page.wait_for_timeout(300)
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".edit_fields.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with (
                        self.subTest(stage="drag_calendar"),
                        print_aria_on_timeout(page),
                    ):
                        logger.debug("Starting calendar drag: Tuesday col, 09:00→11:00")
                        self._drag_in_calendar(
                            page, start_hour=9, end_hour=11, col_index=1
                        )

                        logger.debug("Checking Start Time input value after drag")
                        start_value = page.get_by_label("Start Time").input_value()
                        logger.debug("Checking End Time input value after drag")
                        end_value = page.get_by_label("End Time").input_value()
                        logger.info(
                            "After calendar drag: start=%s  end=%s",
                            start_value,
                            end_value,
                        )
                        self.assertTrue(
                            start_value,
                            "Start Time input should be populated after drag",
                        )
                        self.assertTrue(
                            end_value,
                            "End Time input should be populated after drag",
                        )

                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".drag_calendar.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="save"):
                        logger.debug("Clicking Save Changes button")
                        page.get_by_role("button", name="Save Changes").click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(500)
                        page.locator("body").screenshot(
                            path=self._snapshot_path().with_suffix(".save.png")
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

            finally:
                browser.close()

    def test_delete_event_and_series_via_ui(self) -> None:
        """Delete an event and then its series after accepting browser confirms."""
        user = get_user_model().objects.get(username=self.username)
        user.user_permissions.add(
            *Permission.objects.filter(codename__in=["delete_event", "delete_series"])
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**playwright_launch_options())
            page = browser.new_page()
            page.set_viewport_size({"width": 1600, "height": 900})
            try:
                with print_aria_on_timeout(page):
                    base_url = self.live_server_url
                    if callable(base_url):
                        base_url = base_url()

                    self._login_via_navbar(page, base_url)
                    self._go_to_coordinator(page, base_url)

                    series_listbox = page.get_by_role("listbox", name="Series")
                    series_listbox.get_by_text(self.series.name).click()
                    events_listbox = page.get_by_role("listbox", name="Events")
                    events_listbox.get_by_text(self.event.name).click()
                    page.get_by_role("form", name="Edit event details").wait_for(timeout=1000)

                    with self.subTest(stage="before_delete_event"):
                        test_utils.wait_for_loading_indicators_to_disappear(page)
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="after_delete_event"):
                        # Register the handler BEFORE the click so it is in place
                        # when window.confirm() fires and blocks JS execution.
                        # Using expect_event() and calling dialog.accept() after
                        # the with-block causes a deadlock because the click never
                        # returns while the native confirm dialog is open.
                        delete_event_msgs: list[str] = []

                        def _handle_delete_event_dialog(d) -> None:
                            delete_event_msgs.append(d.message)
                            d.accept()

                        page.once("dialog", _handle_delete_event_dialog)
                        page.get_by_role("button", name="Delete Event").click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(300)
                        self.assertTrue(
                            delete_event_msgs, "No dialog was shown for delete event"
                        )
                        self.assertIn("Delete the event", delete_event_msgs[0])
                        self.assertEqual(
                            events_listbox.get_by_text(self.event.name).count(),
                            0,
                            "Deleted event is still visible in the events list",
                        )
                        self.assert_snapshot(page.locator("body").aria_snapshot())

                    with self.subTest(stage="after_delete_series"):
                        delete_series_msgs: list[str] = []

                        def _handle_delete_series_dialog(d) -> None:
                            delete_series_msgs.append(d.message)
                            d.accept()

                        page.once("dialog", _handle_delete_series_dialog)
                        page.get_by_role("button", name="Delete Series").click()
                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(300)
                        page.get_by_role("main", name="Series content").get_by_text(
                            "No series yet"
                        ).wait_for(timeout=1000)
                        self.assertTrue(
                            delete_series_msgs, "No dialog was shown for delete series"
                        )
                        self.assertIn("Delete the series", delete_series_msgs[0])
                        self.assert_snapshot(page.locator("body").aria_snapshot())
            finally:
                browser.close()

