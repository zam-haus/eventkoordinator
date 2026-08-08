"""Tests for the plugin type-editor tab registry + API (events-and-sync.md
§5, Step 8)."""
import json

from django.test import Client, TestCase, override_settings
from pydantic import BaseModel, ConfigDict, Field

from userdefinedmodel.tests.factories import PublishedConfigVersionFactory, StaffUserFactory, UserFactory
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE
from userdefinedmodel.type_editor_tabs import (
    _TAB_REGISTRY, clear_registry, get_registered_tabs, get_tab, register_type_editor_tab,
)


class DemoTabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(default="")


class RegistryTests(TestCase):
    def setUp(self):
        self._snapshot = dict(_TAB_REGISTRY)

    def tearDown(self):
        clear_registry()
        _TAB_REGISTRY.update(self._snapshot)

    def test_register_and_list(self):
        register_type_editor_tab("demo_tab", "Demo Tab", DemoTabConfig)
        ids = {t.id for t in get_registered_tabs()}
        self.assertIn("demo_tab", ids)
        self.assertEqual(get_tab("demo_tab").label, "Demo Tab")

    def test_duplicate_registration_raises(self):
        register_type_editor_tab("demo_tab", "Demo Tab", DemoTabConfig)
        with self.assertRaises(ValueError):
            register_type_editor_tab("demo_tab", "Other Label", DemoTabConfig)

    def test_sync_core_registers_its_tab_at_startup(self):
        # apps.py's ready() already ran at Django startup — no import needed.
        self.assertIsNotNone(get_tab("sync_targets"))


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class TypeEditorTabApiTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.client = Client()
        self.staff = StaffUserFactory()
        self.user = UserFactory()
        self._snapshot = dict(_TAB_REGISTRY)
        register_type_editor_tab("demo_tab_api", "Demo Tab API", DemoTabConfig)

    def tearDown(self):
        clear_registry()
        _TAB_REGISTRY.update(self._snapshot)

    def test_list_tabs_includes_registered(self):
        self.client.force_login(self.user)
        resp = self.client.get("/api/udm/type-editor-tabs/")
        self.assertEqual(resp.status_code, 200)
        ids = {t["id"] for t in json.loads(resp.content)}
        self.assertIn("demo_tab_api", ids)

    def test_get_config_defaults_to_empty(self):
        version = PublishedConfigVersionFactory()
        self.client.force_login(self.user)
        resp = self.client.get(f"/api/udm/config-versions/{version.id}/tab-configs/demo_tab_api/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), {"tab_id": "demo_tab_api", "config": {}})

    def test_put_requires_permission(self):
        version = PublishedConfigVersionFactory()
        self.client.force_login(self.user)
        resp = self.client.put(
            f"/api/udm/config-versions/{version.id}/tab-configs/demo_tab_api/",
            data=json.dumps({"config": {"url": "https://example.org"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_put_validates_and_persists(self):
        version = PublishedConfigVersionFactory()
        self.client.force_login(self.staff)
        resp = self.client.put(
            f"/api/udm/config-versions/{version.id}/tab-configs/demo_tab_api/",
            data=json.dumps({"config": {"url": "https://example.org"}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

        resp2 = self.client.get(f"/api/udm/config-versions/{version.id}/tab-configs/demo_tab_api/")
        self.assertEqual(json.loads(resp2.content)["config"], {"url": "https://example.org"})

    def test_put_rejects_invalid_config(self):
        version = PublishedConfigVersionFactory()
        self.client.force_login(self.staff)
        resp = self.client.put(
            f"/api/udm/config-versions/{version.id}/tab-configs/demo_tab_api/",
            data=json.dumps({"config": {"unexpected_field": 1}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_unknown_tab_404s(self):
        version = PublishedConfigVersionFactory()
        self.client.force_login(self.staff)
        resp = self.client.put(
            f"/api/udm/config-versions/{version.id}/tab-configs/no-such-tab/",
            data=json.dumps({"config": {}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_tab_config_copied_to_new_draft_on_publish(self):
        from userdefinedmodel.models import ConfigVersion, TypeEditorTabConfig

        version = PublishedConfigVersionFactory()
        TypeEditorTabConfig.objects.create(
            config_version=version, tab_id="demo_tab_api", config={"url": "https://example.org"},
        )
        new_draft = version.publish()
        copied = TypeEditorTabConfig.objects.get(config_version=new_draft, tab_id="demo_tab_api")
        self.assertEqual(copied.config, {"url": "https://example.org"})
