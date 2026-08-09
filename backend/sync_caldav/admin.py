from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import HiddenFromAdminIndexMixin, MaskedSecretFormMixin
from sync_caldav import models

# Note: CalDAVSyncTarget/CalDAVSyncItem are concrete subclasses of
# sync_core.models.SyncBaseTarget/SyncBaseItem (events-and-sync.md §3, Step 11)
# rather than the legacy apiv1 polymorphic base, so they are registered here
# as plain ModelAdmins instead of PolymorphicChildModelAdmin. CalDAVSyncTarget
# is hidden from the app index (HiddenFromAdminIndexMixin) since
# sync_core.admin.SyncBaseTargetAdmin already lists every concrete target
# class in one unified polymorphic view.


@admin.register(models.CalDAVSyncTarget)
class CalDAVSyncTargetAdmin(HiddenFromAdminIndexMixin, MaskedSecretFormMixin, SimpleHistoryAdmin):
    list_display = ("name", "key", "url", "calendar_display_name", "username", "enabled", "created_at", "updated_at")
    search_fields = ("name", "key", "url", "calendar_display_name", "username")
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
    list_display = ("remote_uid", "sync_target", "related_entity", "status", "is_stale", "updated_at")
    list_filter = ("sync_target", "status", "is_stale")
    search_fields = ("remote_uid", "sync_target__name")
    ordering = ("-updated_at",)
    raw_id_fields = ("related_entity",)
