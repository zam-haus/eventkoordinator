"""Tests for sync_core's derived_state matrix and target soft-delete
(events-and-sync.md §3, Step 6)."""
from django.db import models
from django.test import TestCase

from sync_core.models import SyncBaseItem, SyncBaseTarget, sync_item_summary, sync_map_for_entity
from userdefinedmodel.tests.factories import make_entity_with_type


class DerivedStateTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = SyncBaseTarget.objects.create(key="caldav:main", name="Main calendar")

    def _item(self, **kwargs):
        return SyncBaseItem.objects.create(related_entity=self.entity, sync_target=self.target, **kwargs)

    def test_pending(self):
        item = self._item(status="pending")
        self.assertEqual(item.derived_state(), "pending")

    def test_error(self):
        item = self._item(status="error", last_error="boom")
        self.assertEqual(item.derived_state(), "error")

    def test_synced(self):
        item = self._item(status="synced")
        self.assertEqual(item.derived_state(), "synced")

    def test_stale(self):
        item = self._item(status="synced", is_stale=True)
        self.assertEqual(item.derived_state(), "stale")

    def test_target_unavailable_when_disabled(self):
        self.target.enabled = False
        self.target.save(update_fields=["enabled"])
        item = self._item(status="synced")
        self.assertEqual(item.derived_state(), "target_unavailable")

    def test_subclass_status_passthrough(self):
        item = self._item(status="cancelled")
        self.assertEqual(item.derived_state(), "cancelled")

    def test_hard_delete_blocked_while_items_exist(self):
        self._item(status="pending")
        with self.assertRaises(models.ProtectedError):
            self.target.delete()

    def test_hard_delete_allowed_without_items(self):
        empty_target = SyncBaseTarget.objects.create(key="caldav:empty", name="Empty")
        empty_target.delete()
        self.assertFalse(SyncBaseTarget.objects.filter(key="caldav:empty").exists())

    def test_sync_item_summary_shape(self):
        item = self._item(status="error", last_error="oops", remote_uid="uid-1")
        summary = sync_item_summary(item)
        self.assertEqual(summary["target"], "caldav:main")
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["derived_state"], "error")
        self.assertEqual(summary["last_error"], "oops")
        self.assertEqual(summary["remote_uid"], "uid-1")

    def test_sync_map_for_entity(self):
        other_target = SyncBaseTarget.objects.create(key="pretix:main", name="Pretix")
        self._item(status="pending")
        SyncBaseItem.objects.create(related_entity=self.entity, sync_target=other_target, status="synced")

        sync_map = sync_map_for_entity(self.entity.id)
        self.assertEqual(set(sync_map.keys()), {"caldav:main", "pretix:main"})
        self.assertEqual(sync_map["caldav:main"]["derived_state"], "pending")
        self.assertEqual(sync_map["pretix:main"]["derived_state"], "synced")

    def test_sync_map_empty_for_entity_without_items(self):
        other_entity, *_ = make_entity_with_type()
        self.assertEqual(sync_map_for_entity(other_entity.id), {})
