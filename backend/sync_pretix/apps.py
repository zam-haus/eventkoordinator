from django.apps import AppConfig


class PricingConfig(AppConfig):
    name = "sync_pretix"

    def ready(self):
        from sync_pretix.type_editor_tab import register
        register()
