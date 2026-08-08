from django.apps import AppConfig


class SyncCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sync_core"
    verbose_name = "Sync Core"

    def ready(self):
        from sync_core.type_editor_tab import register
        register()
