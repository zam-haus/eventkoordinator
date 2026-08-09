from django.apps import AppConfig


class SyncIcalConfig(AppConfig):
    name = 'sync_ical'

    def ready(self):
        from sync_ical.type_editor_tab import register
        register()
