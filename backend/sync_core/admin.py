"""Django admin for sync_core (events-and-sync.md §3, Step 11 loose end).

Registers the polymorphic ``SyncBaseTarget``/``SyncBaseItem`` base models and
the (non-polymorphic) ``CalendarSource`` pull-side model, plus manual
"sync now" / "fetch now" actions that enqueue the existing Celery tasks
(previously only shell/management-command triggerable, see sync_core/tasks.py).

``child_models`` lists every concrete SyncBaseTarget subclass across the
sync_webhook/sync_ical/sync_caldav/sync_pretix plugins so the polymorphic
"add" flow here has somewhere to go — each plugin's own admin.py (plain
ModelAdmin or PolymorphicChildModelAdmin, plugins are inconsistent on this
but either works for `add`/`change`) is still the actual registration for
that model. Add a new plugin's target model here when it lands.
"""
from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from polymorphic.admin import PolymorphicParentModelAdmin
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import MaskedSecretFormMixin
from sync_caldav.models import CalDAVSyncTarget
from sync_core import models
from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING
from sync_ical.models import IcalCalendarSyncTarget
from sync_pretix.models import PretixSyncTarget
from sync_webhook.models import SyncWebhookTarget

# admin.py loads after every app's models are ready (admin autodiscovery
# runs post-registry), so importing the plugins' target models here — unlike
# in sync_core/models.py, which must stay plugin-agnostic — is safe and
# gives the polymorphic "add" flow below somewhere to go. Add a new plugin's
# target model to child_models when it lands.


@admin.register(models.SyncBaseTarget)
class SyncBaseTargetAdmin(PolymorphicParentModelAdmin, SimpleHistoryAdmin):
    base_model = models.SyncBaseTarget
    child_models = (IcalCalendarSyncTarget, CalDAVSyncTarget, PretixSyncTarget, SyncWebhookTarget)

    list_display = ("name", "key", "enabled", "status_summary", "created_at", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("name", "key")
    ordering = ("name",)
    actions = ["sync_now"]

    def status_summary(self, obj):
        counts = {
            row["status"]: row["n"]
            for row in obj.items.values("status").annotate(n=Count("id"))
        }
        pending = counts.get(DERIVED_STATE_PENDING, 0)
        errors = counts.get(DERIVED_STATE_ERROR, 0)
        total = sum(counts.values())
        return f"{total} item(s): {pending} pending, {errors} error"

    status_summary.short_description = "Status"

    def sync_now(self, request, queryset):
        from sync_core.tasks import push_pending_sync_items_task

        push_pending_sync_items_task.delay()
        self.message_user(request, "Sync of pending items queued.")

    sync_now.short_description = "Sync now (push pending items)"


@admin.register(models.CalendarSource)
class CalendarSourceAdmin(MaskedSecretFormMixin, SimpleHistoryAdmin):
    list_display = (
        "name", "key", "kind", "enabled", "status_summary", "last_fetched_at", "updated_at",
    )
    list_filter = ("enabled", "kind")
    search_fields = ("name", "key", "url", "calendar_display_name", "username")
    ordering = ("name",)
    # Mask the CalDAV/iCal-source password on display; blank-on-save keeps
    # the existing value (same convention as sync_caldav/admin.py).
    secret_fields = ("password",)
    actions = ["fetch_now"]
    readonly_fields = ("fetch_button",)

    def status_summary(self, obj):
        if obj.last_error:
            return f"error: {obj.last_error}"
        if obj.last_fetched_at:
            return "ok"
        return "never fetched"

    status_summary.short_description = "Status"

    def fetch_now(self, request, queryset):
        from sync_core.tasks import fetch_calendar_source_task

        count = 0
        for source in queryset:
            fetch_calendar_source_task.delay(source.pk)
            count += 1
        self.message_user(request, f"Fetch queued for {count} calendar source(s).")

    fetch_now.short_description = "Fetch now"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/fetch/",
                self.admin_site.admin_view(self.fetch_view),
                name="sync_core_calendarsource_fetch",
            ),
        ]
        return custom + urls

    def fetch_view(self, request, object_id):
        # Runs synchronously (fetch_calendar_source is a plain function, no
        # broker required — see sync_core/tasks.py) so the admin gets an
        # immediate pass/fail message instead of a fire-and-forget queue.
        from sync_core.models import fetch_calendar_source

        try:
            result = fetch_calendar_source(object_id)
            self.message_user(request, f"Fetched {result.get('fetched', 0)} entries.")
        except Exception as exc:
            self.message_user(request, f"Fetch failed: {exc}", level="ERROR")
        return HttpResponseRedirect(
            reverse("admin:sync_core_calendarsource_change", args=[object_id])
        )

    def fetch_button(self, obj):
        if obj.pk is None:
            return "Save the record first."
        url = reverse("admin:sync_core_calendarsource_fetch", args=[obj.pk])
        return format_html('<a class="button" href="{}">Fetch now</a>', url)

    fetch_button.short_description = "Trigger fetch"
