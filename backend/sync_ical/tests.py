"""Tests for sync_ical (events-and-sync.md §3, Step 11): the .ics feed is
built/updated from synced_payload (the effective-values snapshot), and
status transitions run through the shared sync_core worker."""
import tempfile

import icalendar
from django.test import TestCase, override_settings

from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING, DERIVED_STATE_SYNCED
from sync_core.tasks import push_pending_sync_items
from sync_ical.models import IcalCalendarSyncItem, IcalCalendarSyncTarget
from sync_ical.tasks import push_ical_target
from userdefinedmodel.tests.factories import make_entity_with_type


class SyncIcalTestCase(TestCase):
    databases = ["default"]

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._settings_override = override_settings(SYNC_ICAL_FEED_DIR=self._tmpdir.name)
        self._settings_override.enable()
        self.addCleanup(self._settings_override.disable)

        self.entity, *_ = make_entity_with_type()
        self.target = IcalCalendarSyncTarget.objects.create(key="ical:main", name="Main calendar")
        self.effective = {
            "title": "Some event",
            "start": "2026-03-01T10:00:00+00:00",
            "end": "2026-03-01T11:00:00+00:00",
            "location": "Room 1",
            "description": "A description",
        }

    def _item(self, **kwargs):
        kwargs.setdefault("status", DERIVED_STATE_PENDING)
        kwargs.setdefault("synced_payload", self.effective)
        return IcalCalendarSyncItem.objects.create(
            related_entity=self.entity, sync_target=self.target, **kwargs,
        )

    def _read_feed(self):
        with open(self.target.feed_path(), "rb") as fh:
            return icalendar.Calendar.from_ical(fh.read())


class IcalCalendarSyncTargetTests(SyncIcalTestCase):
    def test_no_secret_fields(self):
        self.assertEqual(self.target.secret_field_names, [])


class IcalCalendarSyncItemPushTests(SyncIcalTestCase):
    def test_push_writes_vevent_from_synced_payload(self):
        item = self._item()

        item.push()

        item.refresh_from_db()
        self.assertTrue(item.remote_uid)

        calendar = self._read_feed()
        vevents = [c for c in calendar.subcomponents if c.name == "VEVENT"]
        self.assertEqual(len(vevents), 1)
        vevent = vevents[0]
        self.assertEqual(str(vevent.get("SUMMARY")), "Some event")
        self.assertEqual(str(vevent.get("LOCATION")), "Room 1")
        self.assertEqual(str(vevent.get("DESCRIPTION")), "A description")
        self.assertEqual(str(vevent.get("UID")), item.remote_uid)

    def test_push_missing_fields_defaults_sanely(self):
        item = self._item(synced_payload={"title": "Bare event"})

        item.push()

        calendar = self._read_feed()
        vevent = next(c for c in calendar.subcomponents if c.name == "VEVENT")
        self.assertEqual(str(vevent.get("SUMMARY")), "Bare event")
        self.assertIsNone(vevent.get("LOCATION"))
        self.assertIsNone(vevent.get("DTSTART"))

    def test_push_twice_updates_single_vevent_not_duplicate(self):
        item = self._item()
        item.push()

        item.synced_payload = {**self.effective, "title": "Updated title"}
        item.save(update_fields=["synced_payload"])
        item.push()

        calendar = self._read_feed()
        vevents = [c for c in calendar.subcomponents if c.name == "VEVENT"]
        self.assertEqual(len(vevents), 1)
        self.assertEqual(str(vevents[0].get("SUMMARY")), "Updated title")

    def test_push_second_item_appends_second_vevent(self):
        entity2, *_ = make_entity_with_type()
        item1 = self._item()
        item2 = IcalCalendarSyncItem.objects.create(
            related_entity=entity2, sync_target=self.target,
            status=DERIVED_STATE_PENDING, synced_payload={"title": "Second event"},
        )

        item1.push()
        item2.push()

        calendar = self._read_feed()
        vevents = [c for c in calendar.subcomponents if c.name == "VEVENT"]
        self.assertEqual(len(vevents), 2)
        summaries = {str(v.get("SUMMARY")) for v in vevents}
        self.assertEqual(summaries, {"Some event", "Second event"})


class IcalCalendarSyncItemFanOutPushTests(SyncIcalTestCase):
    """events-and-sync.md §13.3: one VEVENT per `payload["submodel"]` slot."""

    def _item_with_slots(self, slots):
        payload = {**self.effective, "submodel": slots}
        return self._item(synced_payload=payload)

    def test_push_creates_one_vevent_per_slot(self):
        slots = [
            {"child_id": "slot-1", "start": "2026-03-01T09:00:00+00:00", "end": "2026-03-01T10:00:00+00:00"},
            {"child_id": "slot-2", "start": "2026-03-02T09:00:00+00:00", "end": "2026-03-02T10:00:00+00:00"},
        ]
        item = self._item_with_slots(slots)

        item.push()

        calendar = self._read_feed()
        vevents = {str(v.get("UID")): v for v in calendar.subcomponents if v.name == "VEVENT"}
        self.assertEqual(set(vevents), {f"{item.related_entity_id}-slot-1", f"{item.related_entity_id}-slot-2"})
        for vevent in vevents.values():
            self.assertEqual(str(vevent.get("SUMMARY")), "Some event")

    def test_removed_slot_deletes_its_vevent(self):
        slots = [
            {"child_id": "slot-1", "start": "2026-03-01T09:00:00+00:00", "end": "2026-03-01T10:00:00+00:00"},
            {"child_id": "slot-2", "start": "2026-03-02T09:00:00+00:00", "end": "2026-03-02T10:00:00+00:00"},
        ]
        item = self._item_with_slots(slots)
        item.push()

        item.synced_payload = {**self.effective, "submodel": slots[:1]}
        item.save(update_fields=["synced_payload"])
        item.push()

        calendar = self._read_feed()
        uids = {str(v.get("UID")) for v in calendar.subcomponents if v.name == "VEVENT"}
        self.assertEqual(uids, {f"{item.related_entity_id}-slot-1"})

    def test_moved_slot_updates_same_vevent_not_duplicate(self):
        slots = [{"child_id": "slot-1", "start": "2026-03-01T09:00:00+00:00", "end": "2026-03-01T10:00:00+00:00"}]
        item = self._item_with_slots(slots)
        item.push()

        moved = [{"child_id": "slot-1", "start": "2026-03-05T09:00:00+00:00", "end": "2026-03-05T10:00:00+00:00"}]
        item.synced_payload = {**self.effective, "submodel": moved}
        item.save(update_fields=["synced_payload"])
        item.push()

        calendar = self._read_feed()
        vevents = [v for v in calendar.subcomponents if v.name == "VEVENT"]
        self.assertEqual(len(vevents), 1)
        self.assertEqual(str(vevents[0].get("UID")), f"{item.related_entity_id}-slot-1")
        self.assertEqual(vevents[0].get("DTSTART").dt.isoformat(), "2026-03-05T09:00:00+00:00")

    def test_fan_out_does_not_touch_other_items_vevents(self):
        entity2, *_ = make_entity_with_type()
        item1 = self._item_with_slots(
            [{"child_id": "slot-1", "start": "2026-03-01T09:00:00+00:00", "end": "2026-03-01T10:00:00+00:00"}],
        )
        item2 = IcalCalendarSyncItem.objects.create(
            related_entity=entity2, sync_target=self.target,
            status=DERIVED_STATE_PENDING, synced_payload={"title": "Second event"},
        )
        item1.push()
        item2.push()

        calendar = self._read_feed()
        vevents = [v for v in calendar.subcomponents if v.name == "VEVENT"]
        self.assertEqual(len(vevents), 2)


class IcalCalendarWorkerTests(SyncIcalTestCase):
    """End-to-end through push_pending_sync_items (sync_core/tasks.py),
    which owns status/synced_at/last_error transitions polymorphically."""

    def test_success_marks_synced(self):
        item = self._item()

        result = push_pending_sync_items()

        item.refresh_from_db()
        self.assertEqual(result, {"pushed": 1, "failed": 0})
        self.assertEqual(item.status, DERIVED_STATE_SYNCED)
        self.assertIsNotNone(item.synced_at)
        self.assertEqual(item.last_error, "")

    def test_failure_marks_error(self):
        import os

        item = self._item()
        # A plain file where the feed directory should be: os.makedirs()
        # raises FileExistsError even with exist_ok=True since the last path
        # component exists but isn't a directory — a deterministic push failure.
        blocker_path = os.path.join(self._tmpdir.name, "blocker")
        with open(blocker_path, "w"):
            pass

        with self.settings(SYNC_ICAL_FEED_DIR=blocker_path):
            result = push_pending_sync_items()

        item.refresh_from_db()
        self.assertEqual(result, {"pushed": 0, "failed": 1})
        self.assertEqual(item.status, DERIVED_STATE_ERROR)
        self.assertTrue(item.last_error)

    def test_push_ical_target_scopes_to_single_target(self):
        other_target = IcalCalendarSyncTarget.objects.create(key="ical:other", name="Other calendar")
        entity2, *_ = make_entity_with_type()
        item_main = self._item()
        item_other = IcalCalendarSyncItem.objects.create(
            related_entity=entity2, sync_target=other_target,
            status=DERIVED_STATE_PENDING, synced_payload={"title": "Other"},
        )

        result = push_ical_target(self.target.pk)

        item_main.refresh_from_db()
        item_other.refresh_from_db()
        self.assertEqual(result, {"pushed": 1, "failed": 0})
        self.assertEqual(item_main.status, DERIVED_STATE_SYNCED)
        self.assertEqual(item_other.status, DERIVED_STATE_PENDING)

    def test_push_ical_target_unknown_target_is_noop(self):
        result = push_ical_target(99999)
        self.assertEqual(result, {"pushed": 0, "failed": 0})
