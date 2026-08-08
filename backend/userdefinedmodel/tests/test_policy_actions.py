"""
Unit tests for the policy action system (userdefinedmodel/actions.py).

Covers the registry, Pydantic schemas, dispatcher, field-path resolution, and
integration with the create / save / transition lifecycle.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from userdefinedmodel.actions import (
    ActionContext,
    PolicyEvaluationOutput,
    SetFieldValueOutput,
    TriggerTransitionOutput,
    SendNotificationOutput,
    _action_registry,
    _collect_subtree_nodes,
    _resolve_field_path,
    dispatch_actions,
    policy_action,
)

_TEST_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_ctx(**kwargs) -> ActionContext:
    node = kwargs.pop("node", MagicMock(id="node-1"))
    user = kwargs.pop("user", MagicMock())
    return ActionContext(
        node=node,
        user=user,
        trigger=kwargs.pop("trigger", "save"),
        phase=kwargs.pop("phase", "pre"),
        **kwargs,
    )


# ─── Registry tests ───────────────────────────────────────────────────────────

class RegistryTests(TestCase):
    def setUp(self):
        # Keep a snapshot so we can clean up test registrations after each test
        self._snapshot = dict(_action_registry)

    def tearDown(self):
        _action_registry.clear()
        _action_registry.update(self._snapshot)

    def test_decorator_registers_handler(self):
        from pydantic import BaseModel
        from typing import Literal

        class MySchema(BaseModel):
            type: Literal["test_register"]
            phase: Literal["pre", "post"]

        @policy_action("test_register", schema=MySchema)
        def my_handler(action, ctx):
            pass

        self.assertIn("test_register", _action_registry)
        schema_cls, handler = _action_registry["test_register"]
        self.assertIs(schema_cls, MySchema)
        self.assertIs(handler, my_handler)

    def test_decorator_duplicate_type_raises(self):
        from pydantic import BaseModel
        from typing import Literal

        class SchemaA(BaseModel):
            type: Literal["dup_test"]
            phase: Literal["pre", "post"]

        @policy_action("dup_test", schema=SchemaA)
        def handler_a(action, ctx):
            pass

        with self.assertRaises(ValueError, msg="Should raise on duplicate registration"):

            @policy_action("dup_test", schema=SchemaA)
            def handler_b(action, ctx):
                pass

    def test_decorator_returns_original_callable(self):
        from pydantic import BaseModel
        from typing import Literal

        class S(BaseModel):
            type: Literal["ret_test"]
            phase: Literal["pre", "post"]

        def my_fn(action, ctx):
            return "result"

        result = policy_action("ret_test", schema=S)(my_fn)
        self.assertIs(result, my_fn)


# ─── Pydantic schema tests ─────────────────────────────────────────────────────

class PolicyEvaluationOutputSchemaTests(TestCase):
    def test_parses_empty_actions(self):
        out = PolicyEvaluationOutput(allow=True)
        self.assertEqual(out.actions, [])

    def test_stores_action_dicts_raw(self):
        """Actions are stored as raw dicts; dispatch validates against registered schema."""
        out = PolicyEvaluationOutput(
            allow=True,
            actions=[{"type": "set_field_value", "phase": "post", "field_path": "status", "value": "done"}],
        )
        self.assertEqual(len(out.actions), 1)
        raw = out.actions[0]
        self.assertIsInstance(raw, dict)
        self.assertEqual(raw["field_path"], "status")

    def test_accepts_unknown_action_type_silently(self):
        """Unknown types are silently stored (dispatch will skip them with a warning)."""
        out = PolicyEvaluationOutput(
            allow=True,
            actions=[{"type": "nonexistent_type", "phase": "post"}],
        )
        self.assertEqual(out.actions[0]["type"], "nonexistent_type")

    def test_defaults(self):
        out = PolicyEvaluationOutput(allow=False)
        self.assertEqual(out.messages, [])
        self.assertEqual(out.viewable_fields, {})
        self.assertEqual(out.editable_fields, {})
        self.assertEqual(out.valid_transitions, [])
        self.assertEqual(out.dashboard_columns, [])
        self.assertEqual(out.actions, [])
        self.assertEqual(out.additional_result, {})


# ─── ActionContext tests ───────────────────────────────────────────────────────

class ActionContextTests(TestCase):
    def test_is_pydantic_model(self):
        from pydantic import BaseModel
        self.assertTrue(issubclass(ActionContext, BaseModel))

    def test_model_copy_update(self):
        ctx = _make_ctx(phase="pre")
        post_ctx = ctx.model_copy(update={"phase": "post"})
        self.assertEqual(post_ctx.phase, "post")
        self.assertEqual(ctx.phase, "pre")  # original unchanged

    def test_visited_transitions_defaults_to_frozenset(self):
        ctx = _make_ctx()
        self.assertIsInstance(ctx.visited_transitions, frozenset)
        self.assertEqual(ctx.visited_transitions, frozenset())

    def test_depth_defaults_to_zero(self):
        ctx = _make_ctx()
        self.assertEqual(ctx.depth, 0)


# ─── Dispatcher tests ─────────────────────────────────────────────────────────

class DispatchActionsTests(TestCase):
    def setUp(self):
        self._snapshot = dict(_action_registry)

    def tearDown(self):
        _action_registry.clear()
        _action_registry.update(self._snapshot)

    def _register_spy(self, type_name: str, phase: str = "post") -> list:
        """Register a spy handler that records calls and return its call log."""
        from pydantic import BaseModel
        from typing import Literal

        calls = []

        class SpySchema(BaseModel):
            type: Literal[type_name]
            phase: Literal["pre", "post"]

        @policy_action(type_name, schema=SpySchema)
        def handler(action, ctx):
            calls.append((action, ctx))

        return calls

    def test_pre_phase_only_fires_pre(self):
        calls = self._register_spy("spy_pre_test")
        actions = [
            {"type": "spy_pre_test", "phase": "pre"},
            {"type": "spy_pre_test", "phase": "post"},
        ]
        ctx = _make_ctx(phase="pre")
        dispatch_actions(actions, ctx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].phase, "pre")

    def test_post_phase_only_fires_post(self):
        calls = self._register_spy("spy_post_test")
        actions = [
            {"type": "spy_post_test", "phase": "pre"},
            {"type": "spy_post_test", "phase": "post"},
        ]
        ctx = _make_ctx(phase="post")
        dispatch_actions(actions, ctx)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].phase, "post")

    def test_unknown_type_logs_and_continues(self):
        actions = [{"type": "totally_unknown_xyz_type", "phase": "post"}]
        import logging
        with self.assertLogs("userdefinedmodel.actions", level=logging.WARNING) as cm:
            dispatch_actions(actions, _make_ctx(phase="post"))
        self.assertTrue(any("totally_unknown_xyz_type" in line for line in cm.output))

    def test_on_error_raise_propagates(self):
        from pydantic import BaseModel
        from typing import Literal

        class FailSchema(BaseModel):
            type: Literal["fail_raise"]
            phase: Literal["pre", "post"]
            on_error: str = "raise"

        @policy_action("fail_raise", schema=FailSchema)
        def fail_handler(action, ctx):
            raise ValueError("intentional")

        actions = [{"type": "fail_raise", "phase": "post", "on_error": "raise"}]
        with self.assertRaises(ValueError):
            dispatch_actions(actions, _make_ctx(phase="post"))

    def test_on_error_log_does_not_raise(self):
        from pydantic import BaseModel
        from typing import Literal

        class FailLogSchema(BaseModel):
            type: Literal["fail_log"]
            phase: Literal["pre", "post"]

        @policy_action("fail_log", schema=FailLogSchema)
        def fail_log_handler(action, ctx):
            raise RuntimeError("logged error")

        actions = [{"type": "fail_log", "phase": "post"}]
        # Should not raise
        dispatch_actions(actions, _make_ctx(phase="post"))

    def test_on_error_ignore_is_silent(self):
        from pydantic import BaseModel
        from typing import Literal

        class FailIgnoreSchema(BaseModel):
            type: Literal["fail_ignore"]
            phase: Literal["pre", "post"]
            on_error: str = "ignore"

        @policy_action("fail_ignore", schema=FailIgnoreSchema)
        def fail_ignore_handler(action, ctx):
            raise RuntimeError("should be ignored")

        actions = [{"type": "fail_ignore", "phase": "post", "on_error": "ignore"}]
        import logging
        with self.assertNoLogs("userdefinedmodel.actions", level=logging.WARNING):
            dispatch_actions(actions, _make_ctx(phase="post"))


# ─── Field path resolution tests ──────────────────────────────────────────────

@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class FieldPathResolutionTests(TestCase):
    databases = ["default"]

    def _make_node_with_field(self, data_type="text_short", slug="myfield"):
        from userdefinedmodel.tests.factories import (
            FieldConfigFactory, ConfigLanguageFactory, PublishedConfigVersionFactory,
            FieldDefinitionFactory, UserDefinedModelTypeFactory, UserDefinedModelEntityFactory,
            ALLOW_ALL_POLICY,
        )

        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        field = FieldDefinitionFactory(version=version, slug=slug, data_type=data_type)
        # UserDefinedModelTypeFactory.policy post-gen attaches an allow-all policy
        udm_type = UserDefinedModelTypeFactory(field_config=config, policy=ALLOW_ALL_POLICY)
        entity = UserDefinedModelEntityFactory(
            config_version=version,
            user_defined_model_type=udm_type,
        )
        return entity, field

    def test_simple_slug_resolves_to_node(self):
        entity, field = self._make_node_with_field(slug="title")
        pairs = _resolve_field_path(entity, "title")
        self.assertEqual(len(pairs), 1)
        node, fd = pairs[0]
        self.assertIs(node, entity)
        self.assertEqual(fd.slug, "title")

    def test_unknown_slug_raises(self):
        entity, _ = self._make_node_with_field(slug="known")
        with self.assertRaises(ValueError):
            _resolve_field_path(entity, "nonexistent")

    def test_dot_path_on_non_submodel_select_raises(self):
        entity, _ = self._make_node_with_field(slug="title", data_type="text_short")
        with self.assertRaises(ValueError):
            _resolve_field_path(entity, "title.sub")


# ─── Integration: TriggerTransitionOutput cycle guard ─────────────────────────

class TriggerTransitionCycleGuardTests(TestCase):
    def setUp(self):
        self._snapshot = dict(_action_registry)

    def tearDown(self):
        _action_registry.clear()
        _action_registry.update(self._snapshot)

    def test_max_depth_raises_transition_error(self):
        from userdefinedmodel.engine import TransitionError
        from userdefinedmodel.actions import _handle_trigger_transition

        node = MagicMock(id="n1")
        node.children.all.return_value = []
        ctx = _make_ctx(node=node, phase="post", depth=10)

        action = TriggerTransitionOutput(
            type="trigger_transition",
            phase="post",
            field_slug="wf",
            transition_name="go",
            target_scope="self",
        )

        with self.assertRaises(TransitionError) as cm:
            _handle_trigger_transition(action, ctx)
        self.assertIn("depth", str(cm.exception).lower())

    def test_cycle_detection_skips_with_warning(self):
        from userdefinedmodel.actions import _handle_trigger_transition

        node = MagicMock(id="n1")
        node.children.all.return_value = []
        visited = frozenset({("n1", "wf", "go")})
        ctx = _make_ctx(node=node, phase="post", visited_transitions=visited)

        action = TriggerTransitionOutput(
            type="trigger_transition",
            phase="post",
            field_slug="wf",
            transition_name="go",
            target_scope="self",
        )

        import logging
        with self.assertLogs("userdefinedmodel.actions", level=logging.WARNING) as cm:
            _handle_trigger_transition(action, ctx)
        self.assertTrue(any("cycle" in line.lower() for line in cm.output))


# ─── Integration: lifecycle hooks ─────────────────────────────────────────────

@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class SaveLifecycleActionTests(TestCase):
    """Verify that PRE and POST save actions fire at the correct points."""
    databases = ["default"]

    def setUp(self):
        self._snapshot = dict(_action_registry)

    def tearDown(self):
        _action_registry.clear()
        _action_registry.update(self._snapshot)

    def _setup(self, rego_actions: str):
        """Create a simple text entity with a Rego policy that declares actions."""
        from userdefinedmodel.tests.factories import (
            FieldConfigFactory, ConfigLanguageFactory, PublishedConfigVersionFactory,
            FieldDefinitionFactory, UserDefinedModelTypeFactory, UserDefinedModelEntityFactory,
            StaffUserFactory,
        )

        user = StaffUserFactory()
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        FieldDefinitionFactory(version=version, slug="title", data_type="text_short")
        from userdefinedmodel.tests.factories import wrap_policy
        # rego_actions arrives as 'actions := [ ... ]'; convert to the partial-set
        # form so it composes with the RESULT_SUFFIX aggregation.
        action_list = rego_actions.split(":=", 1)[1].strip()
        rego = wrap_policy(f"""
package udm
import rego.v1
allow := true
actions contains a if {{ some a in {action_list} }}
""")
        udm_type = UserDefinedModelTypeFactory(field_config=config, policy=rego)
        entity = UserDefinedModelEntityFactory(
            config_version=version, user_defined_model_type=udm_type,
        )
        return entity, user

    def test_pre_save_action_fires_before_validate_for_save(self):
        """A PRE set_field_value action runs before validate_for_save."""
        call_order = []

        # Register a spy for set_field_value (but we test phase ordering, not actual writes)
        from pydantic import BaseModel
        from typing import Literal

        class SpySaveSchema(BaseModel):
            type: Literal["spy_pre_save"]
            phase: Literal["pre", "post"]

        @policy_action("spy_pre_save", schema=SpySaveSchema)
        def spy(action, ctx):
            call_order.append(action.phase)

        entity, user = self._setup(
            'actions := [{"type": "spy_pre_save", "phase": "pre"}, '
            '{"type": "spy_pre_save", "phase": "post"}]'
        )

        from userdefinedmodel.writer import apply_patch
        from django.db import transaction

        with transaction.atomic():
            apply_patch(entity, {"title": "hello"}, user)

        self.assertEqual(call_order, ["pre", "post"])

    def test_post_save_action_fires_after_validation(self):
        """Both pre and post fire; order must be pre then post."""
        call_order = []

        from pydantic import BaseModel
        from typing import Literal

        class SpyOrder(BaseModel):
            type: Literal["spy_order"]
            phase: Literal["pre", "post"]

        @policy_action("spy_order", schema=SpyOrder)
        def spy(action, ctx):
            call_order.append(action.phase)

        entity, user = self._setup(
            'actions := [{"type": "spy_order", "phase": "pre"}, '
            '{"type": "spy_order", "phase": "post"}]'
        )

        from userdefinedmodel.writer import apply_patch
        from django.db import transaction

        with transaction.atomic():
            apply_patch(entity, {"title": "test"}, user)

        self.assertIn("pre", call_order)
        self.assertIn("post", call_order)
        self.assertLess(call_order.index("pre"), call_order.index("post"))


# ─── Management command test ───────────────────────────────────────────────────

class GeneratePolicyActionDocsTests(TestCase):
    def test_output_contains_all_registered_types(self):
        from io import StringIO
        from django.core.management import call_command

        buf = StringIO()
        call_command("generate_policy_action_docs", stdout=buf)
        output = buf.getvalue()

        for type_name in _action_registry:
            self.assertIn(f"`{type_name}`", output, f"Missing docs for {type_name!r}")

    def test_output_contains_field_table(self):
        from io import StringIO
        from django.core.management import call_command

        buf = StringIO()
        call_command("generate_policy_action_docs", stdout=buf)
        output = buf.getvalue()

        # Every action type section should have a field table
        self.assertIn("| Field |", output)
        self.assertIn("| Type |", output)

    def test_output_contains_json_example(self):
        from io import StringIO
        from django.core.management import call_command

        buf = StringIO()
        call_command("generate_policy_action_docs", stdout=buf)
        output = buf.getvalue()

        self.assertIn("```json", output)


# ─── send_notification: template resolution and context contract ──────────────

@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE, FRONTEND_BASE_URL="https://example.test")
class SendNotificationTemplateTests(TestCase):
    """Pins the JSON context contract that bundle templates depend on."""

    def _fire(self, body_text: str, *, context: dict | None = None, slug: str = "tpl"):
        from django.core import mail
        from userdefinedmodel.writer import apply_patch
        from django.db import transaction

        entity, user = self._setup_entity(slug, context or {})
        from userdefinedmodel.models import MailTemplate
        MailTemplate.objects.create(slug=slug, subject="S", body_text=body_text)

        mail.outbox.clear()
        with transaction.atomic():
            apply_patch(entity, {"title": "hello"}, user)
        return mail.outbox

    def _setup_entity(self, slug: str, context: dict):
        import json as _json
        from userdefinedmodel.tests.factories import (
            FieldConfigFactory, ConfigLanguageFactory, PublishedConfigVersionFactory,
            FieldDefinitionFactory, UserDefinedModelTypeFactory,
            UserDefinedModelEntityFactory, StaffUserFactory, wrap_policy,
        )

        user = StaffUserFactory(email="to@example.org")
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        FieldDefinitionFactory(version=version, slug="title", data_type="text_short")
        action = {
            "type": "send_notification",
            "phase": "post",
            "template_name": slug,
            "extra_recipients": ["to@example.org"],
            "context": context,
        }
        rego = wrap_policy(
            "package udm\nimport rego.v1\nallow := true\n"
            f"actions contains a if {{ some a in [{_json.dumps(action)}] }}\n"
        )
        udm_type = UserDefinedModelTypeFactory(field_config=config, policy=rego)
        entity = UserDefinedModelEntityFactory(
            config_version=version, user_defined_model_type=udm_type,
        )
        return entity, user

    def test_db_template_is_used_and_context_keys_are_present(self):
        outbox = self._fire(
            "{{ context.foo }}|{{ input.action }}|{{ decision.allow }}|"
            "{{ fields.title }}|{{ trigger }}|{{ phase }}|{{ frontend_base_url }}",
            context={"foo": "bar"},
        )
        self.assertEqual(len(outbox), 1)
        self.assertEqual(
            outbox[0].body,
            "bar|save|True|hello|save|post|https://example.test",
        )

    def test_template_subject_is_used(self):
        outbox = self._fire("x")
        self.assertEqual(outbox[0].subject, "S")

    def test_policy_context_cannot_shadow_engine_keys(self):
        outbox = self._fire(
            "{{ input.action }}|{{ decision.allow }}",
            context={"input": {"action": "spoofed"}, "decision": {"allow": False}},
        )
        self.assertEqual(outbox[0].body, "save|True")

    def test_context_keys_are_also_exposed_at_top_level(self):
        outbox = self._fire("{{ proposal.title }}", context={"proposal": {"title": "T"}})
        self.assertEqual(outbox[0].body, "T")

    def test_filters_are_available_in_notifications(self):
        outbox = self._fire(
            '{{ context.at | timezone("Europe/Berlin") | isoformat() }}',
            context={"at": "2026-08-08T10:00:00+00:00"},
        )
        self.assertEqual(outbox[0].body, "2026-08-08 12:00:00+02:00")

    def test_unknown_slug_errors_without_blocking_the_save(self):
        """No MailTemplate row → the action errors, but the save still succeeds
        because dispatch_actions defaults to on_error=log."""
        from django.core import mail
        from userdefinedmodel.writer import apply_patch
        from django.db import transaction
        from userdefinedmodel.models import FieldEdit

        entity, user = self._setup_entity("missing-everywhere", {})
        mail.outbox.clear()
        with self.assertLogs("userdefinedmodel.actions", level="WARNING"):
            with transaction.atomic():
                apply_patch(entity, {"title": "hello"}, user)

        self.assertEqual(len(mail.outbox), 0)
        edit = FieldEdit.objects.filter(
            change_kind=FieldEdit.ChangeKind.POLICY_POST_ACTION
        ).last()
        self.assertIn("_error", edit.new_value)
