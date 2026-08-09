from django.apps import AppConfig


class SyncWebhookConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sync_webhook"
    verbose_name = "Sync Webhook"
