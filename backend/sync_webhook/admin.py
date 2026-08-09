from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from project.admin_utils import MaskedSecretFormMixin
from sync_webhook import models

# SyncWebhookTarget/SyncWebhookItem are concrete subclasses of
# sync_core.models.SyncBaseTarget/SyncBaseItem (events-and-sync.md §3), so
# they are registered as plain ModelAdmins here (same convention as
# sync_caldav/sync_pretix) and listed in sync_core.admin.SyncBaseTargetAdmin's
# child_models for the polymorphic "add" flow.


@admin.register(models.SyncWebhookTarget)
class SyncWebhookTargetAdmin(MaskedSecretFormMixin, SimpleHistoryAdmin):
    list_display = ("name", "key", "url", "enabled", "created_at", "updated_at")
    search_fields = ("name", "key", "url")
    ordering = ("-updated_at",)
    # Mask the bearer token on display; blank-on-save keeps the existing value.
    secret_fields = ("bearer_token",)


@admin.register(models.SyncWebhookItem)
class SyncWebhookItemAdmin(SimpleHistoryAdmin):
    list_display = ("related_entity", "sync_target", "status", "sequence", "updated_at")
    list_filter = ("sync_target", "status")
    search_fields = ("sync_target__name",)
    ordering = ("-updated_at",)
    raw_id_fields = ("related_entity",)
