"""Tests for the backlink reverse-lookup helper and delete protection
(events-and-sync.md §1.1, Step 1)."""
import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from userdefinedmodel.backlinks import backlink_summary, find_backlinks
from userdefinedmodel.tests.factories import (
    ALLOW_ALL_POLICY, FieldValueFactory, StaffUserFactory, make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label

User = get_user_model()

FORCE_DELETE_POLICY = wrap_policy("""
package udm
import rego.v1

allow if input.action == "delete"

force_delete if {
    input.action == "delete"
    input.backlink_summary.count > 0
}
""")


def _link_entities(referencing_entity, target_entity, *, multi=False):
    """Add an entity_select(_multi) field on referencing_entity's config
    version pointing at target_entity, and set its value."""
    data_type = "entity_select_multi" if multi else "entity_select"
    field = _make_field_with_label(
        referencing_entity.config_version, "origin", data_type, label="Origin",
    )
    if multi:
        FieldValueFactory(node=referencing_entity, field=field, value_json=[str(target_entity.id)])
    else:
        FieldValueFactory(node=referencing_entity, field=field, value_node=target_entity)
    return field


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class BacklinkHelperTests(TestCase):
    databases = ["default"]

    def test_find_backlinks_single(self):
        target, *_ = make_entity_with_type()
        referencing, *_ = make_entity_with_type()
        _link_entities(referencing, target)

        backlinks = find_backlinks(target.id)
        self.assertEqual(len(backlinks), 1)
        self.assertEqual(backlinks[0].entity.id, referencing.id)
        self.assertEqual(backlinks[0].field_slug, "origin")

    def test_find_backlinks_multi(self):
        target, *_ = make_entity_with_type()
        referencing, *_ = make_entity_with_type()
        _link_entities(referencing, target, multi=True)

        backlinks = find_backlinks(target.id)
        self.assertEqual(len(backlinks), 1)
        self.assertEqual(backlinks[0].entity.id, referencing.id)

    def test_find_backlinks_none(self):
        target, *_ = make_entity_with_type()
        self.assertEqual(find_backlinks(target.id), [])

    def test_backlink_summary_shape(self):
        target, *_ = make_entity_with_type()
        referencing, *_ = make_entity_with_type()
        _link_entities(referencing, target)

        summary = backlink_summary(target.id)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(len(summary["by_type_field"]), 1)
        self.assertEqual(summary["by_type_field"][0]["field_slug"], "origin")
        self.assertEqual(summary["by_type_field"][0]["count"], 1)


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class DeleteProtectionTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.client = Client()
        self.staff = StaffUserFactory()
        self.client.force_login(self.staff)

    def delete(self, entity_id):
        return self.client.delete(f"/api/udm/entities/{entity_id}/")

    def test_delete_blocked_while_backlinks_exist(self):
        target, *_ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        referencing, *_ = make_entity_with_type()
        _link_entities(referencing, target)

        resp = self.delete(target.id)
        self.assertEqual(resp.status_code, 409)
        body = json.loads(resp.content)
        self.assertEqual(body["backlink_summary"]["count"], 1)

        from userdefinedmodel.models import UserDefinedModelEntity
        self.assertTrue(UserDefinedModelEntity.objects.filter(id=target.id).exists())

    def test_delete_allowed_without_backlinks(self):
        target, *_ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        resp = self.delete(target.id)
        self.assertEqual(resp.status_code, 204)

    def test_force_delete_overrides_protection(self):
        target, *_ = make_entity_with_type(policy_source=FORCE_DELETE_POLICY)
        referencing, *_ = make_entity_with_type()
        field = _link_entities(referencing, target)

        resp = self.delete(target.id)
        self.assertEqual(resp.status_code, 204)

        from userdefinedmodel.models import UserDefinedModelEntity
        self.assertFalse(UserDefinedModelEntity.objects.filter(id=target.id).exists())

        # Dangling id: entity_select is a real FK (value_node, SET_NULL) so the
        # referencing FieldValue is cleared automatically by the DB.
        fv = referencing.field_values.get(field=field)
        self.assertIsNone(fv.value_node_id)
