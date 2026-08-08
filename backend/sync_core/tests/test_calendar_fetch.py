"""Tests for CalendarSource fetch (events-and-sync.md §6, Step 9 read-side)."""
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from django.test import TestCase

from sync_core.models import CalendarSource, RemoteCalendarEntry, fetch_calendar_source

ICAL_CONTENT = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:event-1@example.com
SUMMARY:Board Meeting
DTSTART:20260301T090000Z
DTEND:20260301T100000Z
DESCRIPTION:Quarterly board meeting
END:VEVENT
BEGIN:VEVENT
UID:event-2@example.com
SUMMARY:All Day Workshop
DTSTART;VALUE=DATE:20260305
DTEND;VALUE=DATE:20260306
END:VEVENT
END:VCALENDAR
"""


class IcalFetchTests(TestCase):
    def _make_source(self):
        return CalendarSource.objects.create(
            key="test-ical", name="Test iCal", kind=CalendarSource.KIND_ICAL,
            url="https://cal.example.com/feed.ics",
        )

    def _patch_fetch(self, ics_content):
        mock_response = Mock()
        mock_response.text = ics_content
        mock_response.raise_for_status = Mock()
        return patch("sync_core.calendar_fetch.requests.get", return_value=mock_response)

    def _patch_now(self, fixed_now):
        return patch("sync_core.calendar_fetch.django_timezone.now", return_value=fixed_now)

    def test_fetch_creates_entries(self):
        fixed_now = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        source = self._make_source()
        with self._patch_now(fixed_now), self._patch_fetch(ICAL_CONTENT):
            result = fetch_calendar_source(source.pk)
        self.assertEqual(result, {"fetched": 2})
        self.assertEqual(RemoteCalendarEntry.objects.filter(source=source).count(), 2)
        entry = RemoteCalendarEntry.objects.get(source=source, title="Board Meeting")
        self.assertEqual(entry.start, datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc))
        self.assertFalse(entry.all_day)
        allday = RemoteCalendarEntry.objects.get(source=source, title="All Day Workshop")
        self.assertTrue(allday.all_day)
        source.refresh_from_db()
        self.assertIsNotNone(source.last_fetched_at)
        self.assertEqual(source.last_error, "")

    def test_reimport_upserts_not_duplicates(self):
        fixed_now = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        source = self._make_source()
        with self._patch_now(fixed_now), self._patch_fetch(ICAL_CONTENT):
            fetch_calendar_source(source.pk)
            fetch_calendar_source(source.pk)
        self.assertEqual(RemoteCalendarEntry.objects.filter(source=source).count(), 2)

    def test_stale_entry_removed_when_no_longer_on_remote(self):
        fixed_now = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        source = self._make_source()
        with self._patch_now(fixed_now), self._patch_fetch(ICAL_CONTENT):
            fetch_calendar_source(source.pk)

        shrunk = ICAL_CONTENT.split("BEGIN:VEVENT\nUID:event-2")[0] + "END:VCALENDAR\n"
        with self._patch_now(fixed_now), self._patch_fetch(shrunk):
            fetch_calendar_source(source.pk)
        self.assertEqual(RemoteCalendarEntry.objects.filter(source=source).count(), 1)

    def test_fetch_error_records_last_error_and_keeps_entries(self):
        fixed_now = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        source = self._make_source()
        with self._patch_now(fixed_now), self._patch_fetch(ICAL_CONTENT):
            fetch_calendar_source(source.pk)

        with patch("sync_core.calendar_fetch.requests.get", side_effect=Exception("boom")):
            result = fetch_calendar_source(source.pk)
        self.assertIn("error", result)
        source.refresh_from_db()
        self.assertEqual(source.last_error, "boom")
        self.assertEqual(RemoteCalendarEntry.objects.filter(source=source).count(), 2)

    def test_unknown_source_raises(self):
        import uuid
        with self.assertRaises(ValueError):
            fetch_calendar_source(uuid.uuid4())


class CaldavFetchTests(TestCase):
    def _make_source(self):
        return CalendarSource.objects.create(
            key="test-caldav", name="Test CalDAV", kind=CalendarSource.KIND_CALDAV,
            url="https://caldav.example.com/", username="user", password="secret",
            calendar_display_name="Team Calendar",
        )

    def test_fetch_parses_remote_vevents(self):
        vevent_ical = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
            "UID:caldav-event-1@example.com\nSUMMARY:Standup\n"
            "DTSTART:20260301T090000Z\nDTEND:20260301T093000Z\n"
            "END:VEVENT\nEND:VCALENDAR\n"
        )
        mock_cal_event = Mock()
        mock_cal_event.data = vevent_ical
        mock_calendar = Mock()
        mock_calendar.get_display_name.return_value = "Team Calendar"
        mock_calendar.events.return_value = [mock_cal_event]
        mock_principal = Mock()
        mock_principal.get_calendars.return_value = [mock_calendar]
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal

        source = self._make_source()
        with patch("caldav.davclient.DAVClient", return_value=mock_client):
            result = fetch_calendar_source(source.pk)
        self.assertEqual(result, {"fetched": 1})
        entry = RemoteCalendarEntry.objects.get(source=source)
        self.assertEqual(entry.title, "Standup")

    def test_calendar_not_found_records_error(self):
        mock_principal = Mock()
        mock_principal.get_calendars.return_value = []
        mock_client = Mock()
        mock_client.principal.return_value = mock_principal

        source = self._make_source()
        with patch("caldav.davclient.DAVClient", return_value=mock_client):
            result = fetch_calendar_source(source.pk)
        self.assertIn("error", result)
        source.refresh_from_db()
        self.assertIn("not found", source.last_error)


class CalendarSourceModelTests(TestCase):
    def test_secret_field_names_excludes_password(self):
        self.assertEqual(CalendarSource.secret_field_names, ["password"])
