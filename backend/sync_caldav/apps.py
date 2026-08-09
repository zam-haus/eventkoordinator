from django.apps import AppConfig


class SyncCalDAVConfig(AppConfig):
    name = "sync_caldav"

    def ready(self):
        from sync_caldav.type_editor_tab import register
        register()
