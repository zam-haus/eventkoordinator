"""Smoke test for sync_core/admin.py (events-and-sync.md Step 11 loose end:
Django admin for SyncBaseTarget/CalendarSource previously did not exist)."""
from django.contrib import admin
from django.test import TestCase

from sync_core import models


class SyncCoreAdminSmokeTest(TestCase):
    def test_syncbasetarget_and_calendarsource_are_registered(self):
        self.assertIn(models.SyncBaseTarget, admin.site._registry)
        self.assertIn(models.CalendarSource, admin.site._registry)

    def test_changelist_and_actions_render(self):
        target_admin = admin.site._registry[models.SyncBaseTarget]
        source_admin = admin.site._registry[models.CalendarSource]
        self.assertIn("sync_now", target_admin.actions)
        self.assertIn("fetch_now", source_admin.actions)

        target = models.SyncBaseTarget.objects.create(key="t1", name="Target 1")
        source = models.CalendarSource.objects.create(
            key="s1", name="Source 1", kind=models.CalendarSource.KIND_ICAL, url="https://example.com/cal.ics",
        )
        # list_display callables must not blow up on a real instance.
        self.assertIn("item(s)", target_admin.status_summary(target))
        self.assertEqual("never fetched", source_admin.status_summary(source))
