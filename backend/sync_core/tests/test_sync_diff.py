"""Tests for PropertyDiff/SyncDiffData (events-and-sync.md §3, Step 11:
moved from apiv1.models.sync.syncbasedata, diffing synced_payload vs the
current effective values instead of a live remote pull)."""
from django.test import TestCase

from sync_core.models import SyncBaseItem, SyncBaseTarget, compute_sync_diff
from userdefinedmodel.tests.factories import make_entity_with_type


class SyncDiffTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.entity, *_ = make_entity_with_type()
        self.target = SyncBaseTarget.objects.create(key="webhook:main", name="Webhook")

    def _item(self, synced_payload):
        return SyncBaseItem.objects.create(
            related_entity=self.entity, sync_target=self.target,
            status="synced", synced_payload=synced_payload,
        )

    def test_no_diff_when_equal(self):
        item = self._item({"title": "same"})
        diff = compute_sync_diff(item, {"title": "same"})
        self.assertEqual(diff.properties, [])
        self.assertEqual(diff.entity_id, str(self.entity.id))
        self.assertEqual(diff.target_key, "webhook:main")

    def test_changed_property_reported(self):
        item = self._item({"title": "old"})
        diff = compute_sync_diff(item, {"title": "new"})
        self.assertEqual(len(diff.properties), 1)
        prop = diff.properties[0]
        self.assertEqual(prop.property_name, "title")
        self.assertEqual(prop.old_value, "old")
        self.assertEqual(prop.new_value, "new")

    def test_added_and_removed_properties_reported(self):
        item = self._item({"title": "old", "gone": "x"})
        diff = compute_sync_diff(item, {"title": "old", "added": "y"})
        names = {p.property_name for p in diff.properties}
        self.assertEqual(names, {"gone", "added"})

    def test_none_synced_payload_treated_as_empty(self):
        item = self._item(None)
        diff = compute_sync_diff(item, {"title": "new"})
        self.assertEqual(len(diff.properties), 1)
        self.assertIsNone(diff.properties[0].old_value)

    def test_target_key_none_when_target_missing(self):
        item = self._item({"title": "old"})
        item.sync_target = None
        diff = compute_sync_diff(item, {"title": "old"})
        self.assertIsNone(diff.target_key)
