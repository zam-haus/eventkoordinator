"""Tests for input.sync exposure in the policy engine and EntityOut.sync_items
(events-and-sync.md §3.2, Step 6)."""
from django.test import TestCase, override_settings

from sync_core.models import SyncBaseItem, SyncBaseTarget
from userdefinedmodel.engine import evaluate_policy
from userdefinedmodel.tests.factories import ALLOW_ALL_POLICY, UserFactory, make_entity_with_type
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class SyncInputTests(TestCase):
    databases = ["default"]

    def test_input_sync_empty_without_items(self):
        entity, *_ = make_entity_with_type()
        user = UserFactory()
        output = evaluate_policy(entity, user, "view")
        self.assertEqual(output.input_document.get("sync"), {})

    def test_input_sync_reflects_items(self):
        entity, *_ = make_entity_with_type()
        target = SyncBaseTarget.objects.create(key="webhook:main", name="Webhook")
        SyncBaseItem.objects.create(related_entity=entity, sync_target=target, status="error", last_error="x")
        user = UserFactory()

        output = evaluate_policy(entity, user, "view")
        sync = output.input_document.get("sync")
        self.assertIn("webhook:main", sync)
        self.assertEqual(sync["webhook:main"]["derived_state"], "error")

    def test_entity_out_sync_items_via_api(self):
        from userdefinedmodel.tests.factories import StaffUserFactory
        from django.test import Client

        entity, *_ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        target = SyncBaseTarget.objects.create(key="caldav:main", name="Main")
        SyncBaseItem.objects.create(related_entity=entity, sync_target=target, status="synced")

        client = Client()
        staff = StaffUserFactory()
        client.force_login(staff)
        resp = client.get(f"/api/udm/entities/{entity.id}/")
        import json
        body = json.loads(resp.content)
        self.assertEqual(body["sync_items"]["caldav:main"]["derived_state"], "synced")
