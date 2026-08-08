"""Tests for GET /entities/{id}/backlinks/ (events-and-sync.md §1.5, Step 5)."""
import json

from django.test import Client, TestCase, override_settings

from userdefinedmodel.tests.factories import (
    ALLOW_ALL_POLICY, FieldValueFactory, StaffUserFactory, make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label

DENY_ALL_POLICY = wrap_policy("""
package udm
import rego.v1
allow := false
""")


def _link_origin(referencing_entity, target_entity, slug="origin"):
    field = _make_field_with_label(referencing_entity.config_version, slug, "entity_select", label="Origin")
    FieldValueFactory(node=referencing_entity, field=field, value_node=target_entity)
    return field


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class BacklinksEndpointTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.client = Client()
        self.staff = StaffUserFactory()
        self.client.force_login(self.staff)

    def get(self, entity_id, **params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"/api/udm/entities/{entity_id}/backlinks/"
        if qs:
            url += f"?{qs}"
        return self.client.get(url)

    def test_lists_viewable_backlink_with_preview(self):
        target, *_ = make_entity_with_type()
        referencing, ref_type, ref_version, _ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        title_field = ref_version.field_definitions.get(slug="title")
        FieldValueFactory(node=referencing, field=title_field, value_text="Session A")
        from userdefinedmodel.models import FormElement, FormElementBinding
        el = ref_version.form_elements.get(slug="title")
        el.is_preview = True
        el.save(update_fields=["is_preview"])
        _link_origin(referencing, target)

        resp = self.get(target.id)
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], str(referencing.id))
        self.assertEqual(body[0]["field_slug"], "origin")
        self.assertEqual(body[0]["type_id"], str(ref_type.id))
        self.assertEqual(body[0]["preview"], "Session A")

    def test_denied_backlink_is_omitted_not_errored(self):
        target, *_ = make_entity_with_type()
        referencing, *_ = make_entity_with_type(policy_source=DENY_ALL_POLICY)
        _link_origin(referencing, target)

        resp = self.get(target.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), [])

    def test_filter_by_source_type_ids(self):
        target, *_ = make_entity_with_type()
        matching, matching_type, *_ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        other, *_ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        _link_origin(matching, target)
        _link_origin(other, target)

        resp = self.get(target.id, source_type_ids=str(matching_type.id))
        body = json.loads(resp.content)
        self.assertEqual([r["id"] for r in body], [str(matching.id)])

    def test_filter_by_source_field_slug(self):
        target, *_ = make_entity_with_type()
        referencing, *_ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        _link_origin(referencing, target, slug="origin")
        _link_origin(referencing, target, slug="secondary_origin")

        resp = self.get(target.id, source_field_slug="secondary_origin")
        body = json.loads(resp.content)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["field_slug"], "secondary_origin")

    def test_not_found_for_missing_entity(self):
        import uuid
        resp = self.get(uuid.uuid4())
        self.assertEqual(resp.status_code, 404)

    def test_results_sorted_by_preview(self):
        from userdefinedmodel.models import FormElement

        target, *_ = make_entity_with_type()
        titles = ["Zebra Talk", "Apple Talk", "Mango Talk"]
        for title in titles:
            referencing, _, ref_version, _ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
            title_field = ref_version.field_definitions.get(slug="title")
            FieldValueFactory(node=referencing, field=title_field, value_text=title)
            el = ref_version.form_elements.get(slug="title")
            el.is_preview = True
            el.save(update_fields=["is_preview"])
            _link_origin(referencing, target)

        resp = self.get(target.id)
        body = json.loads(resp.content)
        self.assertEqual([r["preview"] for r in body], sorted(titles))
