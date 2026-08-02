from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from polymorphic.admin import PolymorphicChildModelAdmin
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import MaskedSecretFormMixin
from sync_caldav import models


@admin.register(models.CalDAVSyncTarget)
class CalDAVSyncTargetAdmin(MaskedSecretFormMixin, PolymorphicChildModelAdmin, SimpleHistoryAdmin):
    list_display = ("name", "url", "calendar_display_name", "username", "created_at", "updated_at")
    search_fields = ("name", "url", "calendar_display_name", "username")
    ordering = ("-updated_at",)
    readonly_fields = ("sync_button",)
    # Mask the CalDAV password on display; blank-on-save keeps the existing value.
    secret_fields = ("password",)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/sync/",
                self.admin_site.admin_view(self.sync_view),
                name="sync_caldav_caldavsynctarget_sync",
            ),
        ]
        return custom + urls

    def sync_view(self, request, object_id):
        from sync_caldav.tasks import sync_caldav_target

        sync_caldav_target.delay(object_id)
        self.message_user(request, "Sync queued.")
        return HttpResponseRedirect(
            reverse(
                "admin:sync_caldav_caldavsynctarget_change",
                args=[object_id],
            )
        )

    def sync_button(self, obj):
        if obj.pk is None:
            return "Save the record first."
        url = reverse("admin:sync_caldav_caldavsynctarget_sync", args=[obj.pk])
        return format_html('<a class="button" href="{}">Sync now</a>', url)

    sync_button.short_description = "Trigger sync"


@admin.register(models.CalDAVSyncItem)
class CalDAVSyncItemAdmin(SimpleHistoryAdmin):
    list_display = ("caldav_uid", "sync_target", "related_event", "flag_push", "updated_at")
    list_filter = ("sync_target", "flag_push")
    search_fields = ("caldav_uid", "sync_target__name", "related_event__name")
    ordering = ("-updated_at",)
    raw_id_fields = ("related_event",)
