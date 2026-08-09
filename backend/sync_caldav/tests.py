"""Tests for sync_caldav (events-and-sync.md §3, Step 11): VEVENT payload
built from `synced_payload`, remote_uid handling, and status transitions via
the sync_core worker. Mirrors sync_webhook/tests.py."""
from unittest.mock import Mock, patch

from django.test import TestCase

from sync_caldav.models import CalDAVSyncItem, CalDAVSyncTarget
from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING, DERIVED_STATE_SYNCED
from sync_core.tasks import push_pending_sync_items
from userdefinedmodel.tests.factories import make_entity_with_type


class CalDAVSyncTargetTests(TestCase):
    def test_secret_field_names(self):
        target = CalDAVSyncTarget.objects.create(
            key="caldav:main", name="Main calendar", url="https://caldav.example.com",
            username="bot", password="s3cr3t", calendar_display_name="Main",
        )
        self.assertEqual(set(target.secret_field_names), {"password"})


class CalDAVSyncItemPushTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = CalDAVSyncTarget.objects.create(
            key="caldav:main", name="Main calendar", url="https://caldav.example.com",
            username="bot", password="s3cr3t", calendar_display_name="Main",
        )
        self.effective = {
            "title": "Some event",
            "start": "2026-09-01T10:00:00+00:00",
            "end": "2026-09-01T12:00:00+00:00",
            "location": "Room 1",
        }

    def _item(self, **kwargs):
        kwargs.setdefault("status", DERIVED_STATE_PENDING)
        kwargs.setdefault("synced_payload", self.effective)
        return CalDAVSyncItem.objects.create(
            related_entity=self.entity, sync_target=self.target, **kwargs,
        )

    def _mock_calendar(self):
        calendar = Mock()
        calendar.get_event_by_uid.side_effect = Exception("not found")
        return calendar

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_push_builds_vevent_from_synced_payload(self, mock_get_calendar):
        calendar = self._mock_calendar()
        mock_get_calendar.return_value = calendar
        item = self._item()

        item.push()

        calendar.add_event.assert_called_once()
        _args, kwargs = calendar.add_event.call_args
        self.assertEqual(kwargs["summary"], "Some event")
        self.assertEqual(kwargs["location"], "Room 1")
        self.assertEqual(kwargs["dtstart"].isoformat(), "2026-09-01T10:00:00+00:00")
        self.assertEqual(kwargs["dtend"].isoformat(), "2026-09-01T12:00:00+00:00")
        self.assertEqual(kwargs["x-eventkoordinator-entity"], str(self.entity.id))

        item.refresh_from_db()
        self.assertTrue(item.remote_uid)

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_push_defaults_missing_fields(self, mock_get_calendar):
        calendar = self._mock_calendar()
        mock_get_calendar.return_value = calendar
        item = self._item(synced_payload={})

        item.push()

        _args, kwargs = calendar.add_event.call_args
        self.assertEqual(kwargs["summary"], "(untitled event)")
        self.assertEqual(kwargs["location"], "")
        self.assertIsNotNone(kwargs["dtstart"])
        self.assertEqual(kwargs["dtstart"], kwargs["dtend"])

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_push_deletes_existing_remote_event_first(self, mock_get_calendar):
        calendar = self._mock_calendar()
        mock_get_calendar.return_value = calendar
        item = self._item(remote_uid="old-uid-123")

        item.push()

        calendar.get_event_by_uid.assert_called_with("old-uid-123")
        calendar.add_event.assert_called_once()
        item.refresh_from_db()
        self.assertNotEqual(item.remote_uid, "old-uid-123")

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_push_tolerates_missing_remote_event_on_delete(self, mock_get_calendar):
        # get_event_by_uid(...).delete() raising (e.g. NotFoundError) must not
        # abort the push — a fresh VEVENT should still be created.
        calendar = self._mock_calendar()
        mock_get_calendar.return_value = calendar
        item = self._item(remote_uid="stale-uid")

        item.push()

        calendar.add_event.assert_called_once()

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_push_failure_raises_and_still_checkpoints_uid(self, mock_get_calendar):
        calendar = self._mock_calendar()
        calendar.add_event.side_effect = Exception("network error")
        mock_get_calendar.return_value = calendar
        item = self._item()

        with self.assertRaises(RuntimeError):
            item.push()

        # The new UID is checkpointed before the remote call is attempted, so
        # a retry can clean it up (delete-then-recreate) even after a failure.
        item.refresh_from_db()
        self.assertTrue(item.remote_uid)

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_push_calendar_lookup_failure_raises(self, mock_get_calendar):
        mock_get_calendar.side_effect = ValueError("calendar not found on server")
        item = self._item()

        with self.assertRaises(RuntimeError):
            item.push()


class CalDAVSyncWorkerTests(TestCase):
    """End-to-end through push_pending_sync_items (sync_core/tasks.py), which
    owns status/synced_at/last_error transitions polymorphically."""

    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = CalDAVSyncTarget.objects.create(
            key="caldav:main", name="Main calendar", url="https://caldav.example.com",
            username="bot", password="s3cr3t", calendar_display_name="Main",
        )

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_success_marks_synced(self, mock_get_calendar):
        calendar = Mock()
        calendar.get_event_by_uid.side_effect = Exception("not found")
        mock_get_calendar.return_value = calendar
        item = CalDAVSyncItem.objects.create(
            related_entity=self.entity, sync_target=self.target,
            status=DERIVED_STATE_PENDING, synced_payload={"title": "Talk"},
        )

        result = push_pending_sync_items()

        item.refresh_from_db()
        self.assertEqual(result, {"pushed": 1, "failed": 0})
        self.assertEqual(item.status, DERIVED_STATE_SYNCED)
        self.assertIsNotNone(item.synced_at)
        self.assertEqual(item.last_error, "")
        self.assertTrue(item.remote_uid)

    @patch.object(CalDAVSyncTarget, "_get_calendar")
    def test_failure_marks_error(self, mock_get_calendar):
        mock_get_calendar.side_effect = ValueError("calendar not found on server")
        item = CalDAVSyncItem.objects.create(
            related_entity=self.entity, sync_target=self.target,
            status=DERIVED_STATE_PENDING, synced_payload={"title": "Talk"},
        )

        result = push_pending_sync_items()

        item.refresh_from_db()
        self.assertEqual(result, {"pushed": 0, "failed": 1})
        self.assertEqual(item.status, DERIVED_STATE_ERROR)
        self.assertIn("calendar not found", item.last_error)
