"""Tests for the mark_sync policy action + worker (events-and-sync.md §4,
Step 7)."""
from django.db import transaction
from django.test import TestCase, override_settings

from sync_core.models import SyncBaseItem, SyncBaseTarget, _target_bound_to_entity_type
from sync_core.tasks import push_pending_sync_items
from userdefinedmodel.actions import _action_registry
from userdefinedmodel.tests.factories import (
    FieldValueFactory, StaffUserFactory, UserDefinedModelTypeFactory,
    make_entity_with_type, wrap_policy,
)
from userdefinedmodel.tests.test_api import _TEST_MIDDLEWARE, _make_field_with_label
from userdefinedmodel.writer import apply_patch


def _action_policy(action_json: str) -> str:
    return wrap_policy(f"""
package udm
import rego.v1
allow := true
actions contains a if {{ some a in [{action_json}] }}
""")


def _fire(entity, user, action: dict, policy_source: str | None = None):
    import json as _json
    policy = _action_policy(_json.dumps(action)) if policy_source is None else policy_source
    from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy
    entity.user_defined_model_type.type_policies.all().delete()
    p = Policy.objects.create(slug=f"policy-{entity.id}-{Policy.objects.count()}", source=policy)
    UserDefinedModelTypePolicy.objects.create(
        user_defined_model_type=entity.user_defined_model_type, policy=p, sort_order=0,
    )
    with transaction.atomic():
        apply_patch(entity, {"title": "trigger edit"}, user)


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class MarkSyncActionTests(TestCase):
    databases = ["default"]

    def setUp(self):
        self._snapshot = dict(_action_registry)
        self.target = SyncBaseTarget.objects.create(key="webhook:main", name="Webhook")
        self.user = StaffUserFactory()

    def tearDown(self):
        _action_registry.clear()
        _action_registry.update(self._snapshot)

    def test_mark_pending_snapshots_effective(self):
        entity, *_ = make_entity_with_type()
        import json as _json
        action = {"type": "mark_sync", "phase": "post", "target": "webhook:main", "status": "pending"}
        policy = wrap_policy(f"""
package udm
import rego.v1
allow := true
actions contains a if {{ some a in [{_json.dumps(action)}] }}
effective["title"] := input.entity.fields.title.value
""")
        _fire(entity, self.user, action, policy_source=policy)

        item = SyncBaseItem.objects.get(related_entity=entity, sync_target=self.target)
        self.assertEqual(item.status, "pending")
        self.assertIn("title", item.synced_payload or {})

    def test_snapshot_immutable_across_later_edits(self):
        entity, *_ = make_entity_with_type()
        _fire(entity, self.user, {
            "type": "mark_sync", "phase": "post", "target": "webhook:main", "status": "pending",
        })
        item = SyncBaseItem.objects.get(related_entity=entity, sync_target=self.target)
        snapshot_before = dict(item.synced_payload or {})

        # A later edit (no new mark_sync) must not change the stored snapshot.
        with transaction.atomic():
            from userdefinedmodel.writer import apply_patch as _apply
            entity.user_defined_model_type.type_policies.all().delete()
            from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy
            from userdefinedmodel.tests.factories import ALLOW_ALL_POLICY
            p = Policy.objects.create(slug=f"allow-{entity.id}", source=ALLOW_ALL_POLICY)
            UserDefinedModelTypePolicy.objects.create(
                user_defined_model_type=entity.user_defined_model_type, policy=p, sort_order=0,
            )
            _apply(entity, {"title": "changed after marking"}, self.user)

        item.refresh_from_db()
        self.assertEqual(item.synced_payload, snapshot_before)

    def test_error_stays_until_remarked(self):
        entity, *_ = make_entity_with_type()
        SyncBaseItem.objects.create(related_entity=entity, sync_target=self.target, status="error", last_error="boom")

        # A save with no mark_sync action must not clear the error.
        _fire(entity, self.user, {"type": "mark_sync", "phase": "post", "target": "nonexistent", "status": "pending"})
        item = SyncBaseItem.objects.get(related_entity=entity, sync_target=self.target)
        self.assertEqual(item.status, "error")

    def test_unknown_target_logs_failure_without_crashing(self):
        entity, *_ = make_entity_with_type()
        _fire(entity, self.user, {
            "type": "mark_sync", "phase": "post", "target": "does-not-exist", "status": "pending",
        })
        from userdefinedmodel.models.history import FieldEdit
        self.assertTrue(FieldEdit.objects.filter(
            change_kind=FieldEdit.ChangeKind.POLICY_POST_ACTION, new_value__has_key="_error",
        ).exists())

    def test_disallowed_status_logs_failure(self):
        entity, *_ = make_entity_with_type()
        _fire(entity, self.user, {
            "type": "mark_sync", "phase": "post", "target": "webhook:main", "status": "not-a-real-status",
        })
        from userdefinedmodel.models.history import FieldEdit
        self.assertTrue(FieldEdit.objects.filter(
            change_kind=FieldEdit.ChangeKind.POLICY_POST_ACTION, new_value__has_key="_error",
        ).exists())

    def test_via_referencing_fan_out(self):
        proposal, *_ = make_entity_with_type()
        event1, event_type, event_version, _ = make_entity_with_type()
        from userdefinedmodel.models import UserDefinedModelEntity
        event2 = UserDefinedModelEntity.objects.create(
            config_version=event_version, user_defined_model_type=event_type,
        )
        event_type_id = str(event_type.id)
        field = _make_field_with_label(event_version, "origin", "entity_select", label="Origin")
        for ev in (event1, event2):
            FieldValueFactory(node=ev, field=field, value_node=proposal)

        _fire(proposal, self.user, {
            "type": "mark_sync", "phase": "post", "target": "webhook:main", "status": "pending",
            "via_referencing": {"type": event_type_id, "field": "origin"},
        })

        self.assertEqual(SyncBaseItem.objects.filter(related_entity=event1, sync_target=self.target).count(), 1)
        self.assertEqual(SyncBaseItem.objects.filter(related_entity=event2, sync_target=self.target).count(), 1)
        self.assertEqual(SyncBaseItem.objects.filter(related_entity=proposal, sync_target=self.target).count(), 0)

    def test_target_unbound_from_type_rejected(self):
        from userdefinedmodel.models import TypeEditorTabConfig

        entity, *_ = make_entity_with_type()
        TypeEditorTabConfig.objects.create(
            config_version=entity.config_version, tab_id="sync_targets",
            config={"target_keys": ["some-other-target"]},
        )
        _fire(entity, self.user, {
            "type": "mark_sync", "phase": "post", "target": "webhook:main", "status": "pending",
        })
        from userdefinedmodel.models.history import FieldEdit
        self.assertTrue(FieldEdit.objects.filter(
            change_kind=FieldEdit.ChangeKind.POLICY_POST_ACTION, new_value__has_key="_error",
        ).exists())
        self.assertFalse(SyncBaseItem.objects.filter(related_entity=entity, sync_target=self.target).exists())

    def test_target_bound_to_type_allowed(self):
        from userdefinedmodel.models import TypeEditorTabConfig

        entity, *_ = make_entity_with_type()
        TypeEditorTabConfig.objects.create(
            config_version=entity.config_version, tab_id="sync_targets",
            config={"target_keys": ["webhook:main"]},
        )
        _fire(entity, self.user, {
            "type": "mark_sync", "phase": "post", "target": "webhook:main", "status": "pending",
        })
        self.assertTrue(SyncBaseItem.objects.filter(related_entity=entity, sync_target=self.target).exists())

    def test_derived_state_target_unavailable_when_unbound_after_sync(self):
        from userdefinedmodel.models import TypeEditorTabConfig
        from sync_core.models import DERIVED_STATE_TARGET_UNAVAILABLE

        entity, *_ = make_entity_with_type()
        item = SyncBaseItem.objects.create(
            related_entity=entity, sync_target=self.target, status="synced",
        )
        self.assertNotEqual(item.derived_state(), DERIVED_STATE_TARGET_UNAVAILABLE)

        TypeEditorTabConfig.objects.create(
            config_version=entity.config_version, tab_id="sync_targets",
            config={"target_keys": []},
        )
        self.assertEqual(item.derived_state(), DERIVED_STATE_TARGET_UNAVAILABLE)

    def test_post_save_restale_on_change(self):
        entity, *_ = make_entity_with_type()
        item = SyncBaseItem.objects.create(
            related_entity=entity, sync_target=self.target, status="synced",
            synced_payload={"title": {"data_type": "text_short", "localized": False, "value": "old"}},
        )
        self.assertFalse(item.is_stale)

        from userdefinedmodel.tests.factories import ALLOW_ALL_POLICY
        entity.user_defined_model_type.type_policies.all().delete()
        from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy
        p = Policy.objects.create(slug=f"allow-{entity.id}", source=ALLOW_ALL_POLICY)
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=entity.user_defined_model_type, policy=p, sort_order=0,
        )
        with transaction.atomic():
            apply_patch(entity, {"title": "new title"}, self.user)

        item.refresh_from_db()
        self.assertTrue(item.is_stale)


class WorkerTests(TestCase):
    databases = ["default"]

    def test_push_pending_marks_error_when_unimplemented(self):
        entity, *_ = make_entity_with_type()
        target = SyncBaseTarget.objects.create(key="webhook:worker-test", name="Webhook")
        item = SyncBaseItem.objects.create(related_entity=entity, sync_target=target, status="pending")

        result = push_pending_sync_items()
        self.assertEqual(result, {"pushed": 0, "failed": 1})

        item.refresh_from_db()
        self.assertEqual(item.status, "error")
        self.assertIn("push()", item.last_error)

    def test_push_pending_empty_is_noop(self):
        self.assertEqual(push_pending_sync_items(), {"pushed": 0, "failed": 0})


class TargetBoundToEntityTypeTests(TestCase):
    """events-and-sync.md Step 13.1: binding must be read off the type's
    bound **published** version, not the entity's own (possibly lagging)
    config_version_id."""
    databases = ["default"]

    def test_reads_type_bound_published_version_not_entitys_own(self):
        from userdefinedmodel.models import ConfigVersion, TypeEditorTabConfig

        entity, udm_type, old_version, config = make_entity_with_type()
        target = SyncBaseTarget.objects.create(key="webhook:main", name="Webhook")

        # Entity still sits on old_version (never migrated), but the type's
        # config gets republished to a new version with a binding that
        # excludes the target.
        old_version.status = "archived"
        old_version.save()
        new_version = ConfigVersion.objects.create(config=config, status="published")
        TypeEditorTabConfig.objects.create(
            config_version=new_version, tab_id="sync_targets", config={"target_keys": []},
        )

        # entity.config_version_id still points at old_version, which has no
        # tab config row (would be treated as "no restriction") — the fix
        # must resolve via udm_type.field_config's published version instead.
        self.assertNotEqual(entity.config_version_id, new_version.id)
        self.assertFalse(_target_bound_to_entity_type(target.key, entity.id))

    def test_bound_when_key_listed_on_type_published_version(self):
        from userdefinedmodel.models import TypeEditorTabConfig

        entity, udm_type, version, config = make_entity_with_type()
        target = SyncBaseTarget.objects.create(key="webhook:main", name="Webhook")
        TypeEditorTabConfig.objects.create(
            config_version=version, tab_id="sync_targets", config={"target_keys": ["webhook:main"]},
        )
        self.assertTrue(_target_bound_to_entity_type(target.key, entity.id))
