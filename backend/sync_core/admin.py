"""Django admin for sync_core (events-and-sync.md §3, Step 11 loose end).

Registers the polymorphic ``SyncBaseTarget``/``SyncBaseItem`` base models and
the (non-polymorphic) ``CalendarSource`` pull-side model, plus manual
"sync now" / "fetch now" actions that enqueue the existing Celery tasks
(previously only shell/management-command triggerable, see sync_core/tasks.py).

``SyncBaseTarget`` has no concrete subclasses yet in this codebase — the
sync_ical/sync_caldav/sync_pretix plugins still subclass the legacy
``apiv1.models.sync.syncbasedata.SyncBaseTarget`` pending their Step 11 port
onto this relocated framework. Follow the pattern here (mirroring
apiv1/admin.py's ``SyncBaseTargetAdmin``) and register each concrete
subclass's own ``PolymorphicChildModelAdmin`` (as sync_ical/admin.py and
sync_caldav/admin.py do for the legacy base) once those ports land — add it
to ``SyncBaseTargetAdmin.child_models`` at that point.
"""
from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from polymorphic.admin import PolymorphicParentModelAdmin
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import MaskedSecretFormMixin
from sync_core import models
from sync_core.models import DERIVED_STATE_ERROR, DERIVED_STATE_PENDING


@admin.register(models.SyncBaseTarget)
class SyncBaseTargetAdmin(PolymorphicParentModelAdmin, SimpleHistoryAdmin):
    base_model = models.SyncBaseTarget
    # No concrete subclasses exist yet (see module docstring) — add each
    # plugin's target model here once it is ported onto sync_core.
    child_models = ()

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
