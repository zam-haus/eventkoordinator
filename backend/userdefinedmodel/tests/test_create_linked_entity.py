"""Tests for the create_linked_entity policy action (events-and-sync.md §1.2,
Step 3)."""
from django.db import transaction
from django.test import TestCase, override_settings

from userdefinedmodel.actions import _action_registry
from userdefinedmodel.tests.factories import (
    StaffUserFactory, UserDefinedModelTypeFactory, make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label
from userdefinedmodel.writer import apply_patch


def _make_target_type(reference_field="origin", multi=False):
    """A target UDMType with a title field and an entity_select(_multi)
    reference field, published config, allow-all policy."""
    from userdefinedmodel.tests.factories import (
        ConfigLanguageFactory, FieldConfigFactory, PublishedConfigVersionFactory,
    )
    config = FieldConfigFactory()
    ConfigLanguageFactory(config=config, code="en", is_default=True)
    version = PublishedConfigVersionFactory(config=config)
    _make_field_with_label(version, "title", "text_short", label="Title")
    _make_field_with_label(
        version, reference_field, "entity_select_multi" if multi else "entity_select", label="Origin",
    )
    udm_type = UserDefinedModelTypeFactory(field_config=config)
    return udm_type


def _action_policy(action_json: str) -> str:
    return wrap_policy(f"""
package udm
import rego.v1
allow := true
actions contains a if {{ some a in [{action_json}] }}
""")


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class CreateLinkedEntityTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self._snapshot = dict(_action_registry)

    def tearDown(self):
        _action_registry.clear()
        _action_registry.update(self._snapshot)

    def _fire(self, trigger, user, target_type_id, *, allow_multiple=True, initial_fields=None, reference_field="origin"):
        import json as _json
        action = {
            "type": "create_linked_entity",
            "phase": "post",
            "target_type": str(target_type_id),
            "reference_field": reference_field,
            "initial_fields": initial_fields or {},
            "allow_multiple": allow_multiple,
        }
        policy = _action_policy(_json.dumps(action))
        trigger.user_defined_model_type.type_policies.all().delete()
        from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy
        p = Policy.objects.create(slug=f"policy-{trigger.id}-{Policy.objects.count()}", source=policy)
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=trigger.user_defined_model_type, policy=p, sort_order=0,
        )
        with transaction.atomic():
            apply_patch(trigger, {"title": "trigger edit"}, user)

    def test_creates_linked_entity_with_reference_set(self):
        target_type = _make_target_type()
        trigger, *_ = make_entity_with_type()
        user = StaffUserFactory()

        self._fire(trigger, user, target_type.id)

        from userdefinedmodel.models import UserDefinedModelEntity
        created = UserDefinedModelEntity.objects.filter(user_defined_model_type=target_type)
        self.assertEqual(created.count(), 1)
        event = created.first()
        fv = event.field_values.get(field__slug="origin")
        self.assertEqual(fv.value_node_id, trigger.id)

    def test_creates_multi_reference_as_list(self):
        target_type = _make_target_type(multi=True)
        trigger, *_ = make_entity_with_type()
        user = StaffUserFactory()

        self._fire(trigger, user, target_type.id, reference_field="origin")

        from userdefinedmodel.models import UserDefinedModelEntity
        event = UserDefinedModelEntity.objects.filter(user_defined_model_type=target_type).first()
        fv = event.field_values.get(field__slug="origin")
        self.assertEqual(fv.value_json, [str(trigger.id)])

    def test_initial_fields_applied(self):
        target_type = _make_target_type()
        trigger, *_ = make_entity_with_type()
        user = StaffUserFactory()

        self._fire(trigger, user, target_type.id, initial_fields={"title": "Seeded Title"})

        from userdefinedmodel.models import UserDefinedModelEntity
        event = UserDefinedModelEntity.objects.filter(user_defined_model_type=target_type).first()
        fv = event.field_values.get(field__slug="title")
        self.assertEqual(fv.value_text, "Seeded Title")

    def test_multiple_events_per_trigger_default(self):
        target_type = _make_target_type()
        trigger, *_ = make_entity_with_type()
        user = StaffUserFactory()

        self._fire(trigger, user, target_type.id)
        self._fire(trigger, user, target_type.id)

        from userdefinedmodel.models import UserDefinedModelEntity
        self.assertEqual(
            UserDefinedModelEntity.objects.filter(user_defined_model_type=target_type).count(), 2,
        )

    def test_allow_multiple_false_is_noop_when_already_linked(self):
        target_type = _make_target_type()
        trigger, *_ = make_entity_with_type()
        user = StaffUserFactory()

        self._fire(trigger, user, target_type.id, allow_multiple=False)
        self._fire(trigger, user, target_type.id, allow_multiple=False)

        from userdefinedmodel.models import UserDefinedModelEntity
        self.assertEqual(
            UserDefinedModelEntity.objects.filter(user_defined_model_type=target_type).count(), 1,
        )

    def test_unknown_target_type_logs_failure_without_crashing(self):
        import uuid
        trigger, *_ = make_entity_with_type()
        user = StaffUserFactory()

        # Should not raise: dispatch_actions catches handler errors (on_error="log").
        self._fire(trigger, user, uuid.uuid4())

        from userdefinedmodel.models.history import FieldEdit
        errored = FieldEdit.objects.filter(
            change_kind=FieldEdit.ChangeKind.POLICY_POST_ACTION,
            new_value__has_key="_error",
        )
        self.assertTrue(errored.exists())
