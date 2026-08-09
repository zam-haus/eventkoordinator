"""Smoke test for sync_core/admin.py (events-and-sync.md Step 11 loose end:
Django admin for SyncBaseTarget/CalendarSource previously did not exist)."""
import time
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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


class NoDuplicateAdminEntriesTest(TestCase):
    """Concrete target models (sync_ical/sync_caldav/sync_pretix/sync_webhook)
    must not appear as their own top-level admin entry alongside the unified
    SyncBaseTargetAdmin polymorphic list (events-and-sync.md Step 11 follow-up:
    found via live testing, "targets appear twice")."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin3", email="admin3@example.com", password="password",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()

    def test_concrete_target_admins_are_hidden_from_module_permission(self):
        from sync_caldav.models import CalDAVSyncTarget
        from sync_ical.models import IcalCalendarSyncTarget
        from sync_pretix.models import PretixSyncTarget
        from sync_webhook.models import SyncWebhookTarget

        request = type("Req", (), {"user": self.user})()
        for model in (IcalCalendarSyncTarget, CalDAVSyncTarget, PretixSyncTarget, SyncWebhookTarget):
            instance = admin.site._registry[model]
            self.assertFalse(instance.has_module_permission(request))

    def test_app_index_lists_target_only_once_per_app(self):
        resp = self.client.get(reverse("admin:app_list", args=["sync_webhook"]))
        content = resp.content.decode()
        # Only SyncWebhookItem should be listed; SyncWebhookTarget is hidden.
        self.assertNotIn("Sync webhook targets", content)
        self.assertIn("Sync webhook items", content)


class CalendarSourceFetchButtonTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin2", email="admin2@example.com", password="password",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["oidc_id_token_expiration"] = time.time() + 3600
        session.save()
        self.source = models.CalendarSource.objects.create(
            key="s2", name="Source 2", kind=models.CalendarSource.KIND_ICAL, url="https://example.com/cal.ics",
        )

    def test_change_page_has_fetch_button(self):
        url = reverse("admin:sync_core_calendarsource_change", args=[self.source.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(reverse("admin:sync_core_calendarsource_fetch", args=[self.source.pk]), resp.content.decode())

    def test_fetch_view_triggers_fetch_and_redirects(self):
        fetch_url = reverse("admin:sync_core_calendarsource_fetch", args=[self.source.pk])
        with patch("sync_core.models.fetch_calendar_source", return_value={"fetched": 3}) as mock_fetch:
            resp = self.client.get(fetch_url)
        mock_fetch.assert_called_once_with(str(self.source.pk))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.url, reverse("admin:sync_core_calendarsource_change", args=[self.source.pk]),
        )

    def test_fetch_view_reports_failure(self):
        fetch_url = reverse("admin:sync_core_calendarsource_fetch", args=[self.source.pk])
        with patch("sync_core.models.fetch_calendar_source", side_effect=RuntimeError("boom")):
            resp = self.client.get(fetch_url, follow=True)
        messages = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("Fetch failed" in m for m in messages))
