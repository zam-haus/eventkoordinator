"""Tests for dynamic link expansion (events-and-sync.md §2: linked_inputs /
backlink_inputs, Step 2)."""
from django.test import TestCase, override_settings

from userdefinedmodel.engine import LinkResolutionError, evaluate_policy
from userdefinedmodel.tests.factories import (
    FieldValueFactory, UserFactory, make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label

FORWARD_UNCONDITIONAL_POLICY = wrap_policy("""
package udm
import rego.v1

allow := true

linked_inputs contains "origin"

additional_result["origin_title"] := input.linked.origin.fields.title.value if input.linked.origin != null
additional_result["origin_title"] := null if input.linked.origin == null
""")

FORWARD_CONDITIONAL_POLICY = wrap_policy("""
package udm
import rego.v1

allow := true

linked_inputs contains "origin" if {
    input.entity.fields.want_origin.value == true
}
""")

DEEP_PATH_POLICY = wrap_policy("""
package udm
import rego.v1

allow := true

linked_inputs contains "origin"
linked_inputs contains "origin.origin" if {
    input.linked.origin != null
}

additional_result["grand_title"] := input.linked["origin.origin"].fields.title.value if {
    input.linked["origin.origin"] != null
}
""")

MULTI_PATH_POLICY = wrap_policy("""
package udm
import rego.v1

allow := true

linked_inputs contains "origins"

additional_result["origin_titles"] := [d.fields.title.value | some d in input.linked.origins; d != null]
""")

UNSTABLE_POLICY = wrap_policy("""
package udm
import rego.v1

allow := true

# Requests a NEW, never-before-seen path every iteration: guaranteed to never
# converge, exercising the fixpoint iteration limit.
linked_inputs contains p if {
    n := count(object.keys(input.linked))
    p := sprintf("nonexistent_field_%d", [n])
}
""")


def _backlink_policy(source_type_id: str) -> str:
    return wrap_policy("""
package udm
import rego.v1

allow := true

backlink_inputs contains {
    "name": "referrers",
    "source_type": "%s",
    "source_field": "origin",
}

additional_result["referrer_titles"] := [d.fields.title.value | some d in input.backlinks.referrers]
additional_result["referrer_sync"] := [d.sync | some d in input.backlinks.referrers]
""" % source_type_id)


def _set_title(entity, version, title: str):
    field = version.field_definitions.get(slug="title")
    FieldValueFactory(node=entity, field=field, value_text=title)


def _link_origin(referencing_entity, target_entity, *, multi=False, slug="origin"):
    data_type = "entity_select_multi" if multi else "entity_select"
    field = _make_field_with_label(referencing_entity.config_version, slug, data_type, label="Origin")
    if multi:
        FieldValueFactory(node=referencing_entity, field=field, value_json=[str(target_entity.id)])
    else:
        FieldValueFactory(node=referencing_entity, field=field, value_node=target_entity)
    return field


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class LinkExpansionTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self.user = UserFactory()

    def test_forward_path_unconditional(self):
        target, _, target_version, _ = make_entity_with_type()
        _set_title(target, target_version, "Target Title")
        referencing, *_ = make_entity_with_type(policy_source=FORWARD_UNCONDITIONAL_POLICY)
        _link_origin(referencing, target)

        output = evaluate_policy(referencing, self.user, "view")
        self.assertTrue(output.allow)
        self.assertEqual(output.additional_result.get("origin_title"), "Target Title")

    def test_forward_path_null_when_unset(self):
        referencing, *_ = make_entity_with_type(policy_source=FORWARD_UNCONDITIONAL_POLICY)
        _make_field_with_label(referencing.config_version, "origin", "entity_select", label="Origin")

        output = evaluate_policy(referencing, self.user, "view")
        self.assertTrue(output.allow)
        self.assertIsNone(output.additional_result.get("origin_title"))

    def test_conditional_request_not_requested(self):
        target, *_ = make_entity_with_type()
        referencing, _, ref_version, _ = make_entity_with_type(policy_source=FORWARD_CONDITIONAL_POLICY)
        _make_field_with_label(ref_version, "want_origin", "boolean", label="Want origin")
        want_field = ref_version.field_definitions.get(slug="want_origin")
        FieldValueFactory(node=referencing, field=want_field, value_bool=False)
        _link_origin(referencing, target)

        # No exception, no path resolved (allow still true) — request simply
        # never fires, proving requests can be genuinely conditional on input.
        output = evaluate_policy(referencing, self.user, "view")
        self.assertTrue(output.allow)

    def test_deep_path(self):
        grandparent, _, gp_version, _ = make_entity_with_type()
        _set_title(grandparent, gp_version, "Grandparent Title")
        parent, *_ = make_entity_with_type()
        _link_origin(parent, grandparent)
        referencing, *_ = make_entity_with_type(policy_source=DEEP_PATH_POLICY)
        _link_origin(referencing, parent)

        output = evaluate_policy(referencing, self.user, "view")
        self.assertTrue(output.allow)
        self.assertEqual(output.additional_result.get("grand_title"), "Grandparent Title")

    def test_multi_path(self):
        t1, _, v1, _ = make_entity_with_type()
        _set_title(t1, v1, "First")
        t2, _, v2, _ = make_entity_with_type()
        _set_title(t2, v2, "Second")
        referencing, *_ = make_entity_with_type(policy_source=MULTI_PATH_POLICY)
        field = _make_field_with_label(
            referencing.config_version, "origins", "entity_select_multi", label="Origins",
        )
        FieldValueFactory(node=referencing, field=field, value_json=[str(t1.id), str(t2.id)])

        output = evaluate_policy(referencing, self.user, "view")
        self.assertTrue(output.allow)
        self.assertCountEqual(output.additional_result.get("origin_titles"), ["First", "Second"])

    def test_backlink_resolution(self):
        target, target_type, *_ = make_entity_with_type(policy_source=None)
        referencing, *_ = make_entity_with_type()
        _link_origin(referencing, target)
        _set_title(referencing, referencing.config_version, "Referencing Entity")

        from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy
        policy = Policy.objects.create(
            slug=f"policy-{target.id}", source=_backlink_policy(str(referencing.user_defined_model_type_id)),
        )
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=target_type, policy=policy, sort_order=0,
        )

        output = evaluate_policy(target, self.user, "view")
        self.assertTrue(output.allow)
        self.assertEqual(output.additional_result.get("referrer_titles"), ["Referencing Entity"])
        # §3.2 loose end: backlink docs carry their own `sync` map, same as
        # the root entity's `input.sync` — empty here since no sync items
        # exist for the referencing entity.
        self.assertEqual(output.additional_result.get("referrer_sync"), [{}])

    def test_unstable_requests_raise(self):
        entity, *_ = make_entity_with_type(policy_source=UNSTABLE_POLICY)
        with self.assertRaises(LinkResolutionError):
            from userdefinedmodel.engine import build_policy_input, get_session_for_type, get_udm_type_for_node, resolve_linked_and_backlinks
            udm_type = get_udm_type_for_node(entity)
            session = get_session_for_type(udm_type)
            input_doc = build_policy_input(entity, self.user, "view")
            resolve_linked_and_backlinks(session, input_doc, input_doc["entity"], input_doc["entity"]["id"])
