from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from polymorphic.admin import PolymorphicChildModelAdmin
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import HiddenFromAdminIndexMixin
from sync_ical import models


@admin.register(models.IcalCalendarSyncTarget)
class IcalCalendarSyncTargetAdmin(HiddenFromAdminIndexMixin, PolymorphicChildModelAdmin, SimpleHistoryAdmin):
    list_display = ("name", "key", "enabled", "created_at", "updated_at")
    search_fields = ("name", "key")
    ordering = ("-updated_at",)
    readonly_fields = ("sync_button",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/sync/",
                self.admin_site.admin_view(self.sync_view),
                name="sync_ical_icalcalendarsynctarget_sync",
            ),
        ]
        return custom + urls

    def sync_view(self, request, object_id):
        from sync_ical.tasks import sync_ical_target

        sync_ical_target.delay(object_id)
        self.message_user(request, "Sync queued.")
        return HttpResponseRedirect(
            reverse(
                "admin:sync_ical_icalcalendarsynctarget_change",
                args=[object_id],
            )
        )

    def sync_button(self, obj):
        if obj.pk is None:
            return "Save the record first."
        url = reverse("admin:sync_ical_icalcalendarsynctarget_sync", args=[obj.pk])
        return format_html('<a class="button" href="{}">Sync now</a>', url)

    sync_button.short_description = "Trigger sync"


@admin.register(models.IcalCalendarSyncItem)
class IcalCalendarSyncItemAdmin(SimpleHistoryAdmin):
    list_display = ("related_entity", "sync_target", "status", "remote_uid", "updated_at")
    list_filter = ("sync_target", "status")
    search_fields = ("remote_uid", "sync_target__name")
    ordering = ("-updated_at",)
    raw_id_fields = ("related_entity",)
