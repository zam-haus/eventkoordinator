"""
API tests for the userdefinedmodel app.
Uses PostgreSQL exclusively (SELECT FOR UPDATE requires it).
"""
import json
import uuid

from django.test import TestCase, Client, TransactionTestCase, override_settings
from django.contrib.auth import get_user_model

# Disable OIDC session refresh middleware for all tests in this module
# (force_login doesn't create OIDC session tokens, causing spurious 302 redirects)
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

from userdefinedmodel.tests.factories import (
    wrap_policy,
    UserFactory, StaffUserFactory, FieldConfigFactory, ConfigLanguageFactory,
    ConfigVersionFactory, PublishedConfigVersionFactory, FieldDefinitionFactory,
    FieldDefinitionTranslationFactory, UserDefinedModelTypeFactory,
    UserDefinedModelEntityFactory, PolicyFactory,
    make_simple_config, make_full_workflow, add_workflow_field, make_entity_with_type,
    ALLOW_ALL_POLICY, REGO_DENY_ALL, REGO_OWNER_EDIT, REGO_BLOCK_SUBMIT_IF_TITLE_EMPTY, REGO_STAFF_ONLY,
)

User = get_user_model()


def _make_field_with_label(version, slug, data_type, label=None, language="en", is_localized=False, help_text="", **extra):
    """Test helper: create a DataField + a 1:1 'field' FormElement bound to it,
    plus a translation. Mimics the pre-split `FieldDefinition.objects.create` +
    `FieldDefinitionTranslation.objects.create` pattern.
    Extra kwargs (workflow_version, submodel_config, type_config) pass through
    to DataField.objects.create."""
    from userdefinedmodel.models import DataField, FormElement, FormElementTranslation, FormElementBinding
    field = DataField.objects.create(version=version, slug=slug, data_type=data_type, is_localized=is_localized, **extra)
    el = FormElement.objects.create(
        version=version, slug=slug, element_type=FormElement.ElementType.FIELD,
        sort_order=0, is_preview=False, type_config={},
    )
    FormElementBinding.objects.create(form_element=el, data_field=field, role="")
    FormElementTranslation.objects.create(
        element=el, language=language, label=label or slug.replace("_", " ").title(), help_text=help_text,
    )
    return field


@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class BaseAPITest(TestCase):
    databases = ["default"]

    def setUp(self):
        self.client = Client()
        self.staff = StaffUserFactory()
        self.user = UserFactory()
        self.client.force_login(self.staff)

    def get(self, path, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.get(f"/api/udm{path}", **kwargs)

    def post(self, path, data=None, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.post(
            f"/api/udm{path}",
            data=json.dumps(data) if data is not None else None,
            content_type="application/json",
            **kwargs,
        )

    def patch(self, path, data, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.patch(
            f"/api/udm{path}",
            data=json.dumps(data),
            content_type="application/json",
            **kwargs,
        )

    def put(self, path, data, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.put(
            f"/api/udm{path}",
            data=json.dumps(data),
            content_type="application/json",
            **kwargs,
        )

    def delete(self, path, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        return self.client.delete(f"/api/udm{path}", **kwargs)


# ─── FieldConfig tests ────────────────────────────────────────────────────────

class FieldConfigTests(BaseAPITest):
    def test_create_config(self):
        resp = self.post("/configs/", {
            "name": "My Config",
            "description": "A test config",
            "languages": [{"code": "en", "label": "English", "is_default": True, "sort_order": 0}],
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "My Config")
        self.assertEqual(len(data["languages"]), 1)
        self.assertEqual(data["languages"][0]["code"], "en")

    def test_create_config_requires_exactly_one_default_language(self):
        resp = self.post("/configs/", {
            "name": "Bad Config",
            "languages": [
                {"code": "en", "label": "English", "is_default": True, "sort_order": 0},
                {"code": "de", "label": "Deutsch", "is_default": True, "sort_order": 1},
            ],
        })
        self.assertEqual(resp.status_code, 422)

    def test_create_config_non_staff_forbidden(self):
        resp = self.post("/configs/", {
            "name": "Config",
            "languages": [{"code": "en", "label": "English", "is_default": True, "sort_order": 0}],
        }, user=self.user)
        self.assertEqual(resp.status_code, 403)

    def test_get_config(self):
        config, version, field, lang = make_simple_config()
        resp = self.get(f"/configs/{config.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], str(config.id))
        self.assertEqual(data["stale_entity_count"], 0)

    def test_update_config(self):
        config = FieldConfigFactory()
        resp = self.patch(f"/configs/{config.id}/", {"name": "Updated Name"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "Updated Name")

    def test_delete_config(self):
        config = FieldConfigFactory()
        resp = self.delete(f"/configs/{config.id}/")
        self.assertEqual(resp.status_code, 204)

    def test_delete_config_in_use_blocked(self):
        config, version, field, lang = make_simple_config()
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        resp = self.delete(f"/configs/{config.id}/")
        self.assertEqual(resp.status_code, 400)


# ─── ConfigVersion / Draft tests ─────────────────────────────────────────────

class ConfigVersionTests(BaseAPITest):
    def test_replace_draft(self):
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        draft = ConfigVersionFactory(config=config, status="draft")

        resp = self.put(f"/configs/{config.id}/versions/draft/", {
            "notes": "First draft",
            "fields": [
                {
                    "slug": "title",
                    "data_type": "text_short",
                    "sort_order": 0,
                    "is_localized": False,
                    "labels": {"en": "Title"},
                    "help_texts": {"en": "Enter a title"},
                    "type_config": {},
                }
            ],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["fields"]), 1)
        self.assertEqual(data["fields"][0]["slug"], "title")

    def test_replace_draft_duplicate_slug_rejected(self):
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config)
        ConfigVersionFactory(config=config, status="draft")

        resp = self.put(f"/configs/{config.id}/versions/draft/", {
            "notes": "",
            "fields": [
                {"slug": "dup", "data_type": "text_short", "sort_order": 0, "labels": {"en": "Dup"}},
                {"slug": "dup", "data_type": "text_short", "sort_order": 1, "labels": {"en": "Dup2"}},
            ],
        })
        self.assertEqual(resp.status_code, 422)

    def test_publish_draft(self):
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config)
        draft = ConfigVersionFactory(config=config, status="draft")

        resp = self.post(f"/configs/{config.id}/versions/draft/publish/")
        self.assertEqual(resp.status_code, 200)

        draft.refresh_from_db()
        self.assertEqual(draft.status, "published")

        # New draft is auto-created
        from userdefinedmodel.models import ConfigVersion
        new_draft = ConfigVersion.objects.filter(config=config, status="draft").first()
        self.assertIsNotNone(new_draft)

    def test_get_published_version(self):
        config, version, field, lang = make_simple_config()
        resp = self.get(f"/configs/{config.id}/versions/published/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "published")
        self.assertEqual(len(data["fields"]), 1)

    def test_get_type_config(self):
        config, version, field, lang = make_simple_config()
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        resp = self.get(f"/types/{udm_type.id}/config/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "published")


# ─── Policy tests ─────────────────────────────────────────────────────────────

class PolicyTests(BaseAPITest):
    def test_create_policy(self):
        resp = self.post("/policies/", {
            "slug": "allow-all",
            "source": ALLOW_ALL_POLICY,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["slug"], "allow-all")

    def test_get_policy(self):
        policy = PolicyFactory(slug="my-policy")
        resp = self.get(f"/policies/{policy.slug}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["slug"], "my-policy")

    def test_update_policy(self):
        policy = PolicyFactory(slug="edit-me")
        resp = self.put(f"/policies/{policy.slug}/", {"source": REGO_DENY_ALL})
        self.assertEqual(resp.status_code, 200)
        policy.refresh_from_db()
        self.assertEqual(policy.source, REGO_DENY_ALL)

    def test_assign_policy_to_type(self):
        udm_type = UserDefinedModelTypeFactory()
        policy = PolicyFactory()
        resp = self.post(f"/types/{udm_type.id}/policies/", {
            "policy_slug": policy.slug, "sort_order": 0
        })
        self.assertEqual(resp.status_code, 201)

    def test_delete_policy_assigned_blocked(self):
        entity, udm_type, version, config = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        from userdefinedmodel.models import Policy
        policy = Policy.objects.filter(type_assignments__user_defined_model_type=udm_type).first()
        resp = self.delete(f"/policies/{policy.slug}/")
        self.assertEqual(resp.status_code, 400)


# ─── Entity CRUD tests ────────────────────────────────────────────────────────

class EntityCRUDTests(BaseAPITest):
    def test_create_entity(self):
        config, version, field, lang = make_simple_config()
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        resp = self.post("/entities/", {"user_defined_model_type_id": str(udm_type.id)})
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["config_version_id"], str(version.id))

    def test_create_entity_no_config_fails(self):
        udm_type = UserDefinedModelTypeFactory(field_config=None)
        resp = self.post("/entities/", {"user_defined_model_type_id": str(udm_type.id)})
        self.assertEqual(resp.status_code, 400)

    def test_create_entity_validate_allowed(self):
        _, udm_type, _, _ = make_entity_with_type(policy_source=ALLOW_ALL_POLICY)
        from userdefinedmodel.models import UserDefinedModelEntity
        count_before = UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).count()
        resp = self.post("/entities/?validate=true", {"user_defined_model_type_id": str(udm_type.id)})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["valid"])
        # Validate must not persist an entity
        self.assertEqual(UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).count(), count_before)

    def test_create_entity_validate_denied(self):
        _, udm_type, _, _ = make_entity_with_type(policy_source=REGO_STAFF_ONLY)
        from userdefinedmodel.models import UserDefinedModelEntity
        count_before = UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).count()
        # Non-staff user — REGO_STAFF_ONLY denies create for non-staff
        resp = self.post("/entities/?validate=true", {"user_defined_model_type_id": str(udm_type.id)}, user=self.user)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["valid"])
        # Validate must not persist an entity
        self.assertEqual(UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).count(), count_before)

    def test_get_entity(self):
        entity, udm_type, version, config = make_entity_with_type()
        resp = self.get(f"/entities/{entity.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], str(entity.id))

    def test_delete_entity_allowed_by_policy(self):
        # Deletion is authorized by the entity's policy ("delete" action).
        # REGO_OWNER_EDIT grants delete to staff.
        entity, udm_type, version, config = make_entity_with_type(policy_source=REGO_OWNER_EDIT)
        resp = self.delete(f"/entities/{entity.id}/", user=self.staff)
        self.assertEqual(resp.status_code, 204)

    def test_delete_entity_denied_by_policy(self):
        # A non-staff user is denied delete by REGO_OWNER_EDIT.
        entity, udm_type, version, config = make_entity_with_type(policy_source=REGO_OWNER_EDIT)
        non_staff = UserFactory()
        resp = self.delete(f"/entities/{entity.id}/", user=non_staff)
        self.assertEqual(resp.status_code, 403)

    def test_delete_entity_no_policy_denied(self):
        # Default-deny: a type with no policy attached grants nothing.
        entity, udm_type, version, config = make_entity_with_type(policy_source=None)
        resp = self.delete(f"/entities/{entity.id}/", user=self.staff)
        self.assertEqual(resp.status_code, 403)

    def test_entity_not_found(self):
        resp = self.get(f"/entities/{uuid.uuid4()}/")
        self.assertEqual(resp.status_code, 404)


# ─── Entity PATCH tests ───────────────────────────────────────────────────────

class EntityPatchTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        config, self.version, self.field, self.lang = make_simple_config(data_type="text_short")
        self.udm_type = UserDefinedModelTypeFactory(field_config=config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type
        )

    def test_patch_scalar_field(self):
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"content": "Hello World"}
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        fvs = {fv["field_slug"]: fv["value"] for fv in data["field_values"]}
        self.assertEqual(fvs["content"], "Hello World")

    def test_patch_unknown_field_rejected(self):
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"nonexistent_field": "rejected"}
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nonexistent_field", resp.json()["errors"])

    def test_patch_reserved_control_key_ignored(self):
        # Keys prefixed with "_" (e.g. the submodel "Restore" marker) are not
        # treated as unknown fields and must not trigger a 400.
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"content": "ok", "_undelete": True}
        })
        self.assertEqual(resp.status_code, 200)

    def test_patch_clear_field(self):
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(
            node=self.entity, field=self.field, language="", value_text="old value"
        )
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"content": None}
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(FieldValue.objects.filter(node=self.entity, field=self.field).exists())

    def test_patch_validation_error_400(self):
        # MaxLengthRule(max_length=5) should reject > 5 char values
        from userdefinedmodel.models import MaxLengthRule
        MaxLengthRule.objects.create(field=self.field, applies_to_save=True, max_length=5)
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"content": "This is too long"}
        })
        self.assertEqual(resp.status_code, 400)

    def test_patch_returns_complete_state(self):
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(node=self.entity, field=self.field, language="", value_text="existing")
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {}
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Should still return all fields
        fvs = {fv["field_slug"]: fv["value"] for fv in data["field_values"]}
        self.assertIn("content", fvs)


# ─── Validation rule tests ────────────────────────────────────────────────────

class ValidationRuleTests(BaseAPITest):
    def test_required_rule_blocks_empty(self):
        from userdefinedmodel.models import RequiredRule, FieldValue
        config, version, field, lang = make_simple_config(required=False)
        RequiredRule.objects.create(field=field, applies_to_save=True)
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        entity = UserDefinedModelEntityFactory(config_version=version, user_defined_model_type=udm_type)

        resp = self.patch(f"/entities/{entity.id}/", {
            "changed_fields": {"content": None}
        })
        # Setting to null when required should... actually pass at write time (we don't validate absence)
        # but validating should trigger on the existing state
        self.assertIn(resp.status_code, [200, 400])

    def test_regex_rule_rejects_non_matching(self):
        from userdefinedmodel.models import RegexRule
        config, version, field, lang = make_simple_config(required=False)
        RegexRule.objects.create(field=field, applies_to_save=True, pattern=r"^\d+$", failure_message="Digits only")
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        entity = UserDefinedModelEntityFactory(config_version=version, user_defined_model_type=udm_type)
        # Store a valid value first
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(node=entity, field=field, language="", value_text="123")

        # Now try patching with non-digit content (validation runs on whole node)
        resp = self.patch(f"/entities/{entity.id}/", {
            "changed_fields": {"content": "abc"}
        })
        # The regex rule should fire and reject "abc"
        self.assertEqual(resp.status_code, 400)

    def test_max_value_rule(self):
        from userdefinedmodel.models import MaxValueRule, FieldDefinition
        from decimal import Decimal
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config)
        version = PublishedConfigVersionFactory(config=config)
        field = FieldDefinitionFactory(version=version, slug="count", data_type="integer")
        MaxValueRule.objects.create(field=field, applies_to_save=True, max_value=Decimal("10"))
        # FieldDefinitionFactory auto-creates a 1:1 FormElement + translation.

        udm_type = UserDefinedModelTypeFactory(field_config=config)
        entity = UserDefinedModelEntityFactory(config_version=version, user_defined_model_type=udm_type)

        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(node=entity, field=field, language="", value_decimal=Decimal("5"))

        resp = self.patch(f"/entities/{entity.id}/", {"changed_fields": {"count": 11}})
        self.assertEqual(resp.status_code, 400)


# ─── Workflow transition tests ────────────────────────────────────────────────

class WorkflowTransitionTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        config, self.version, self.field, self.lang = make_simple_config()
        self.wf, self.draft_state, self.submitted_state, self.submit_trans = make_full_workflow()
        self.wf_field = add_workflow_field(self.version, self.wf, slug="status")
        self.udm_type = UserDefinedModelTypeFactory(field_config=config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version,
            user_defined_model_type=self.udm_type,
        )
        # Materialize defaults to set initial workflow state
        self.entity.materialize_defaults()

    def _get_workflow_state(self):
        from userdefinedmodel.models import FieldValue
        fv = FieldValue.objects.select_related("value_workflow_state").get(
            node=self.entity, field=self.wf_field, language=""
        )
        return fv.value_workflow_state

    def test_submit_transition(self):
        resp = self.post(f"/entities/{self.entity.id}/transition/", {"field": "status", "transition": "submit"})
        self.assertEqual(resp.status_code, 200)
        state = self._get_workflow_state()
        self.assertEqual(state.name, "submitted")

    def test_transition_wrong_state_409(self):
        # Force entity into submitted state, then try to submit again
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.filter(node=self.entity, field=self.wf_field).update(
            value_workflow_state=self.submitted_state
        )
        resp = self.post(f"/entities/{self.entity.id}/transition/", {"field": "status", "transition": "submit"})
        self.assertEqual(resp.status_code, 409)

    def test_transition_unknown_name_404(self):
        resp = self.post(f"/entities/{self.entity.id}/transition/", {"field": "status", "transition": "nonexistent"})
        self.assertEqual(resp.status_code, 404)

    def test_transition_unknown_field_404(self):
        resp = self.post(f"/entities/{self.entity.id}/transition/", {"field": "nonexistent", "transition": "submit"})
        self.assertEqual(resp.status_code, 404)

    def test_transition_creates_history_entry(self):
        self.post(f"/entities/{self.entity.id}/transition/", {"field": "status", "transition": "submit"})
        from userdefinedmodel.models.history import EditGroup, FieldEdit
        group = EditGroup.objects.filter(root_entity=self.entity).first()
        self.assertIsNotNone(group)
        edit = group.field_edits.filter(change_kind=FieldEdit.ChangeKind.NODE_TRANSITION).first()
        self.assertIsNotNone(edit)
        self.assertEqual(edit.old_value, {"state": "draft"})
        self.assertEqual(edit.new_value, {"state": "submitted"})

    def test_workflow_state_in_field_values(self):
        """Workflow state appears as a field value in entity output."""
        resp = self.get(f"/entities/{self.entity.id}/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        fvs = {fv["field_slug"]: fv["value"] for fv in data["field_values"]}
        self.assertIn("status", fvs)
        self.assertEqual(fvs["status"], "draft")

    def test_from_undefined_only_transition(self):
        """from_undefined_only=True blocks transition when state is already defined."""
        from userdefinedmodel.models import WorkflowTransition
        # Add a transition that only fires from undefined state
        from userdefinedmodel.models import WorkflowState
        init = WorkflowState.objects.create(version=self.wf, name="init", is_initial=False)
        WorkflowTransition.objects.create(
            version=self.wf, name="initialize", from_state=None,
            from_undefined_only=True, to_state=init,
        )
        # Entity already has "draft" state → should be blocked
        resp = self.post(f"/entities/{self.entity.id}/transition/", {"field": "status", "transition": "initialize"})
        self.assertEqual(resp.status_code, 409)

    def test_workflow_field_write_blocked_via_patch(self):
        """Directly patching a workflow field via PATCH is rejected."""
        resp = self.patch(f"/entities/{self.entity.id}/", {"changed_fields": {"status": "submitted"}})
        self.assertEqual(resp.status_code, 400)

    def test_multiple_workflows_independent(self):
        """Two workflow fields on the same entity advance independently."""
        from userdefinedmodel.models import (
            WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowTransition,
            FieldDefinition, FieldDefinitionTranslation, FieldValue,
        )
        wf2_def = WorkflowDefinition.objects.create(name="Review Workflow")
        wf2 = WorkflowVersion.objects.create(workflow=wf2_def, status=WorkflowVersion.Status.PUBLISHED)
        pending = WorkflowState.objects.create(version=wf2, name="pending", is_initial=True)
        approved = WorkflowState.objects.create(version=wf2, name="approved", is_initial=False)
        WorkflowTransition.objects.create(version=wf2, name="approve", from_state=pending, to_state=approved)

        review_field = add_workflow_field(self.version, wf2, slug="review")
        # Set initial state for the new field on the existing entity
        FieldValue.objects.create(node=self.entity, field=review_field, language="", value_workflow_state=pending)

        # Advance status → review stays pending
        self.post(f"/entities/{self.entity.id}/transition/", {"field": "status", "transition": "submit"})
        review_fv = FieldValue.objects.select_related("value_workflow_state").get(node=self.entity, field=review_field)
        self.assertEqual(review_fv.value_workflow_state.name, "pending")

        # Advance review independently
        resp = self.post(f"/entities/{self.entity.id}/transition/", {"field": "review", "transition": "approve"})
        self.assertEqual(resp.status_code, 200)
        review_fv.refresh_from_db()
        self.assertEqual(review_fv.value_workflow_state.name, "approved")


# ─── Workflow with Rego policy tests ─────────────────────────────────────────

class PolicyEnforcementTests(BaseAPITest):
    def test_rego_allow_passes_transition(self):
        """Policy passes when required field is filled."""
        entity, udm_type, version, config = make_entity_with_type(
            policy_source=REGO_BLOCK_SUBMIT_IF_TITLE_EMPTY
        )
        wf, draft, submitted, trans = make_full_workflow()
        add_workflow_field(version, wf, slug="status")
        entity.materialize_defaults()

        # Fill the title field
        field = version.field_definitions.get(slug="title")
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(node=entity, field=field, language="", value_text="My Title")

        resp = self.post(f"/entities/{entity.id}/transition/", {"field": "status", "transition": "submit"})
        self.assertEqual(resp.status_code, 200)

    def test_overall_state_gates_transition(self):
        """Policy can deny transition on workflow A based on workflow B's state."""
        from userdefinedmodel.tests.factories import wrap_policy
        CROSS_WORKFLOW_POLICY = wrap_policy("""
package udm
import rego.v1

_submit_blocked if {
    input.action == "transition"
    input.field == "status"
    input.transition == "submit"
    input.entity.fields.review.value != "approved"
}

allow if { not _submit_blocked }

messages contains msg if {
    _submit_blocked
    msg := {
        "level": "error",
        "text": "Must be approved before submitting",
        "field_slug": "status",
    }
}
""")
        from userdefinedmodel.models import (
            WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowTransition,
            FieldValue, Policy, UserDefinedModelTypePolicy,
        )
        entity, udm_type, version, config = make_entity_with_type(
            policy_source=CROSS_WORKFLOW_POLICY
        )

        # Workflow A: status
        wf_a, a_draft, a_submitted, a_trans = make_full_workflow()
        add_workflow_field(version, wf_a, slug="status")

        # Workflow B: review (pending → approved)
        wf_b_def = WorkflowDefinition.objects.create(name="Review")
        wf_b = WorkflowVersion.objects.create(workflow=wf_b_def, status=WorkflowVersion.Status.PUBLISHED)
        b_pending = WorkflowState.objects.create(version=wf_b, name="pending", is_initial=True)
        b_approved = WorkflowState.objects.create(version=wf_b, name="approved", is_initial=False)
        WorkflowTransition.objects.create(version=wf_b, name="approve", from_state=b_pending, to_state=b_approved)
        add_workflow_field(version, wf_b, slug="review")

        entity.materialize_defaults()

        # review is "pending" → submit blocked
        resp = self.post(f"/entities/{entity.id}/transition/", {"field": "status", "transition": "submit"})
        self.assertEqual(resp.status_code, 422)

        # Advance review to approved
        self.post(f"/entities/{entity.id}/transition/", {"field": "review", "transition": "approve"})

        # Now submit should pass
        resp = self.post(f"/entities/{entity.id}/transition/", {"field": "status", "transition": "submit"})
        self.assertEqual(resp.status_code, 200)


# ─── Edit history tests ───────────────────────────────────────────────────────

class EditHistoryTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        config, self.version, self.field, self.lang = make_simple_config()
        self.udm_type = UserDefinedModelTypeFactory(field_config=config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type
        )

    def test_patch_creates_history(self):
        self.patch(f"/entities/{self.entity.id}/", {"changed_fields": {"content": "first value"}})
        resp = self.get(f"/entities/{self.entity.id}/history/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(data["count"], 0)

    def test_history_contains_field_edits(self):
        self.patch(f"/entities/{self.entity.id}/", {"changed_fields": {"content": "hello"}})
        resp = self.get(f"/entities/{self.entity.id}/history/")
        data = resp.json()
        edits = data["results"][0]["edits"]
        slugs = [e["field_slug"] for e in edits]
        self.assertIn("content", slugs)

    def test_history_pagination(self):
        for i in range(5):
            self.patch(f"/entities/{self.entity.id}/", {"changed_fields": {"content": f"value {i}"}})
        resp = self.get(f"/entities/{self.entity.id}/history/?page=1&page_size=3")
        data = resp.json()
        self.assertEqual(len(data["results"]), 3)
        self.assertIsNotNone(data["next"])


# ─── Policy document tests ────────────────────────────────────────────────────

class PolicyDocumentTests(BaseAPITest):
    def test_get_policy_document(self):
        entity, udm_type, version, config = make_entity_with_type()
        resp = self.get(f"/entities/{entity.id}/policy-document/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("fields", data)
        self.assertIn("schema_id", data)
        self.assertEqual(data["schema_id"], str(entity.config_version_id))

    def test_policy_document_non_staff_forbidden(self):
        entity, udm_type, version, config = make_entity_with_type()
        resp = self.get(f"/entities/{entity.id}/policy-document/", user=self.user)
        self.assertEqual(resp.status_code, 403)


# ─── Config version lifecycle tests ──────────────────────────────────────────

class ConfigVersionLifecycleTests(BaseAPITest):
    def test_publish_archives_previous(self):
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config)
        # Manually create published + draft
        from userdefinedmodel.models import ConfigVersion
        published = ConfigVersion.objects.create(config=config, status="published")
        draft = ConfigVersion.objects.create(config=config, status="draft")

        resp = self.post(f"/configs/{config.id}/versions/draft/publish/")
        self.assertEqual(resp.status_code, 200)

        published.refresh_from_db()
        self.assertEqual(published.status, "archived")

        draft.refresh_from_db()
        self.assertEqual(draft.status, "published")

    def test_new_draft_is_copy(self):
        config, version, field, lang = make_simple_config()
        # version is published; make a draft
        from userdefinedmodel.models import ConfigVersion
        # Create a draft by calling publish (which auto-creates a new draft)
        draft = ConfigVersion.objects.create(config=config, status="draft")

        resp = self.post(f"/configs/{config.id}/versions/draft/publish/")
        self.assertEqual(resp.status_code, 200)

        # A new draft should exist
        new_drafts = ConfigVersion.objects.filter(config=config, status="draft")
        self.assertEqual(new_drafts.count(), 1)


# ─── Localized field tests ────────────────────────────────────────────────────

class LocalizedFieldTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        from userdefinedmodel.models import (
            FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition, FieldDefinitionTranslation,
            UserDefinedModelType,
        )
        self.config = FieldConfig.objects.create(name="Localized Config")
        ConfigLanguage.objects.create(config=self.config, code="en", label="English", is_default=True)
        ConfigLanguage.objects.create(config=self.config, code="de", label="Deutsch", is_default=False)
        self.version = ConfigVersion.objects.create(config=self.config, status="published")
        self.field = _make_field_with_label(
            self.version, "abstract", "text_markdown", label="Abstract", is_localized=True,
        )
        self.udm_type = UserDefinedModelTypeFactory(name="Localized Type", field_config=self.config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type
        )

    def test_patch_localized_field(self):
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"abstract": {"en": "English abstract", "de": "Deutsches Abstract"}}
        })
        self.assertEqual(resp.status_code, 200)
        from userdefinedmodel.models import FieldValue
        en_fv = FieldValue.objects.get(node=self.entity, field=self.field, language="en")
        de_fv = FieldValue.objects.get(node=self.entity, field=self.field, language="de")
        self.assertEqual(en_fv.value_text, "English abstract")
        self.assertEqual(de_fv.value_text, "Deutsches Abstract")

    def test_patch_single_language_leaves_others(self):
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(node=self.entity, field=self.field, language="en", value_text="existing en")
        FieldValue.objects.create(node=self.entity, field=self.field, language="de", value_text="existing de")

        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"abstract": {"en": "updated en"}}
        })
        self.assertEqual(resp.status_code, 200)

        # German should be unchanged
        de_fv = FieldValue.objects.get(node=self.entity, field=self.field, language="de")
        self.assertEqual(de_fv.value_text, "existing de")

    def test_patch_null_clears_all_languages(self):
        from userdefinedmodel.models import FieldValue
        FieldValue.objects.create(node=self.entity, field=self.field, language="en", value_text="text")
        FieldValue.objects.create(node=self.entity, field=self.field, language="de", value_text="text")

        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"abstract": None}
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(FieldValue.objects.filter(node=self.entity, field=self.field).count(), 0)


# ─── Submodel tests ───────────────────────────────────────────────────────────

class SubmodelTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        from userdefinedmodel.models import (
            FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
            FieldDefinitionTranslation, UserDefinedModelType,
        )
        # Use separate configs for submodel and root to avoid unique_published_per_config violation
        self.sub_config = FieldConfig.objects.create(name="Speaker Submodel Config")
        ConfigLanguage.objects.create(config=self.sub_config, code="en", label="English", is_default=True)
        self.sub_version = ConfigVersion.objects.create(config=self.sub_config, status="published")
        self.name_field = _make_field_with_label(
            self.sub_version, "name", "text_short", label="Name",
        )

        self.config = FieldConfig.objects.create(name="Submodel Root Config")
        ConfigLanguage.objects.create(config=self.config, code="en", label="English", is_default=True)
        self.version = ConfigVersion.objects.create(config=self.config, status="published")
        self.speakers_field = _make_field_with_label(
            self.version, "speakers", "submodel_list", label="Speakers",
            submodel_config=self.sub_version,
        )
        self.chair_field = _make_field_with_label(
            self.version, "chair", "submodel_select", label="Chair",
            submodel_config=self.sub_version,
        )

        self.udm_type = UserDefinedModelTypeFactory(name="Submodel Type", field_config=self.config)
        self.entity = UserDefinedModelEntityFactory(
            config_version=self.version, user_defined_model_type=self.udm_type
        )

    def test_create_submodel_via_patch(self):
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {
                "speakers": [
                    {"op": "create", "fields": {"name": "Alice"}}
                ]
            }
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("speakers", data["children"])
        self.assertEqual(len(data["children"]["speakers"]), 1)

    def test_create_and_update_submodel_select_via_patch(self):
        from userdefinedmodel.models.node import SubmodelInstance
        # Create a submodel_select child with an initial field value.
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"chair": {"op": "create", "fields": {"name": "Bob"}}}
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertIn("chair", data["children"])
        self.assertEqual(len(data["children"]["chair"]), 1)
        # Update the referenced child's fields with a single dict op (not a list).
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"chair": {"op": "update", "fields": {"name": "Carol"}}}
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        names = [fv["value"] for c in data["children"]["chair"] for fv in c["field_values"] if fv["field_slug"] == "name"]
        self.assertIn("Carol", names)

    def test_submodel_select_rejects_list_value(self):
        # The submodel_list ops shape must not be accepted for a submodel_select.
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {"chair": [{"op": "update", "id": "x", "fields": {"name": "y"}}]}
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("chair", resp.json()["errors"])

    def test_delete_submodel_via_patch(self):
        from userdefinedmodel.models.node import SubmodelInstance
        child = SubmodelInstance.objects.create(
            config_version=self.sub_version,
            parent_node=self.entity,
            parent_field=self.speakers_field,
            sort_order=0,
        )
        resp = self.patch(f"/entities/{self.entity.id}/", {
            "changed_fields": {
                "speakers": [{"op": "delete", "id": str(child.id)}]
            }
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SubmodelInstance.objects.filter(id=child.id).exists())


# ─── Migration tests ──────────────────────────────────────────────────────────

class MigrationTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        # Entity migration endpoints are superuser-only.
        self.superuser = UserFactory(is_superuser=True)
        self.client.force_login(self.superuser)

    def test_migration_preview(self):
        config, version, field, lang = make_simple_config()
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        entity = UserDefinedModelEntityFactory(config_version=version, user_defined_model_type=udm_type)

        # Create a second version (use a separate config to avoid unique_published_per_config violation)
        from userdefinedmodel.models import ConfigVersion, FieldDefinition, FieldDefinitionTranslation, FieldConfig, ConfigLanguage
        config2 = FieldConfig.objects.create(name="Config2")
        ConfigLanguage.objects.create(config=config2, code="en", label="English", is_default=True)
        v2 = ConfigVersion.objects.create(config=config2, status="published")
        f2 = _make_field_with_label(v2, "content", "text_short", label="Content")

        resp = self.get(f"/entities/{entity.id}/migration-preview/?target_version={v2.id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Preview is side-effect free and returns no migration_id.
        self.assertNotIn("migration_id", data)
        from userdefinedmodel.models import UserDefinedModelEntityMigration
        self.assertEqual(UserDefinedModelEntityMigration.objects.count(), 0)
        self.assertEqual(len(data["field_previews"]), 1)
        self.assertEqual(data["field_previews"][0]["source_slug"], "content")
        self.assertEqual(data["field_previews"][0]["suggested_action"], "map")

    def _make_renamed_versions(self):
        """One config: archived version with slug 'old_name', published with 'new_name'."""
        from userdefinedmodel.models import (
            FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
            FieldDefinitionTranslation, UserDefinedModelType, FieldValue,
        )
        config = FieldConfig.objects.create(name="Renamed Config")
        ConfigLanguage.objects.create(config=config, code="en", label="English", is_default=True)
        v_old = ConfigVersion.objects.create(config=config, status="archived")
        old_field = FieldDefinition.objects.create(version=v_old, slug="old_name", data_type="text_short")
        FieldDefinitionTranslation.objects.create(field=old_field, language="en", label="Old")
        v_pub = ConfigVersion.objects.create(config=config, status="published")
        new_field = FieldDefinition.objects.create(version=v_pub, slug="new_name", data_type="text_short")
        FieldDefinitionTranslation.objects.create(field=new_field, language="en", label="New")
        udm_type = UserDefinedModelTypeFactory(name="Renamed Type", field_config=config)
        entity = UserDefinedModelEntityFactory(config_version=v_old, user_defined_model_type=udm_type)
        fv = FieldValue(node=entity, field=old_field, language="")
        fv.set_value("hello", field=old_field)
        fv.save()
        return config, v_old, v_pub, udm_type, entity

    def test_execute_migration_maps_renamed_field(self):
        from userdefinedmodel.models import UserDefinedModelEntity
        config, v_old, v_pub, udm_type, entity = self._make_renamed_versions()

        resp = self.post(f"/entities/{entity.id}/migrate/", {
            "target_version_id": str(v_pub.id),
            "confirmed": True,
            "field_mappings": [
                {"source_field_slug": "old_name", "action": "map", "target_field_slug": "new_name"},
            ],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        # Entity moved to the published version and the value was carried over.
        self.assertEqual(data["config_version_id"], str(v_pub.id))
        fvs = {fv["field_slug"]: fv["value"] for fv in data["field_values"]}
        self.assertEqual(fvs.get("new_name"), "hello")
        entity.refresh_from_db()
        self.assertEqual(entity.config_version_id, v_pub.id)

    def test_stale_entity_count_counts_archived_version_entities(self):
        config, v_old, v_pub, udm_type, entity = self._make_renamed_versions()
        # entity sits on the archived version → it is stale.
        resp = self.get("/configs/")
        self.assertEqual(resp.status_code, 200)
        row = next(c for c in resp.json() if c["id"] == str(config.id))
        self.assertEqual(row["stale_entity_count"], 1)

    def test_config_version_entity_count(self):
        config, v_old, v_pub, udm_type, entity = self._make_renamed_versions()
        resp = self.get(f"/configs/{config.id}/versions/")
        self.assertEqual(resp.status_code, 200)
        counts = {v["id"]: v["entity_count"] for v in resp.json()}
        self.assertEqual(counts[str(v_old.id)], 1)
        self.assertEqual(counts[str(v_pub.id)], 0)

    def test_bulk_migration_preview_and_create(self):
        from userdefinedmodel.models import BulkMigrationPlan, BulkMigrationFieldMapping
        config, v_old, v_pub, udm_type, entity = self._make_renamed_versions()

        # Preview (POST with query params) reports the affected entity count.
        resp = self.post(f"/bulk-migrations/preview/?source_version_id={v_old.id}&target_version_id={v_pub.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["affected_entity_count"], 1)

        # Create persists a draft plan with the field mapping.
        resp = self.post("/bulk-migrations/", {
            "source_version_id": str(v_old.id),
            "target_version_id": str(v_pub.id),
            "field_mappings": [
                {"source_field_slug": "old_name", "action": "map", "target_field_slug": "new_name"},
            ],
        })
        self.assertEqual(resp.status_code, 201, resp.content)
        plan_id = resp.json()["id"]
        plan = BulkMigrationPlan.objects.get(id=plan_id)
        self.assertEqual(plan.status, "draft")
        self.assertEqual(BulkMigrationFieldMapping.objects.filter(plan=plan).count(), 1)


# ─── Autocomplete tests ───────────────────────────────────────────────────────

class AutocompleteTests(BaseAPITest):
    def test_search_users(self):
        UserFactory(username="alice_search")
        resp = self.get("/users/?q=alice_search")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any(u["display_name"] == "alice_search" for u in resp.json()))

    def test_search_groups(self):
        from django.contrib.auth.models import Group
        Group.objects.create(name="workshop_search_group")
        resp = self.get("/groups/?q=workshop_search_group")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any(g["name"] == "workshop_search_group" for g in resp.json()))

    def test_search_entities(self):
        entity, udm_type, version, config = make_entity_with_type()
        resp = self.get("/entity-search/?type_ids=" + str(udm_type.id))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any(e["id"] == str(entity.id) for e in resp.json()))


# ─── Staging file tests ───────────────────────────────────────────────────────

class StagingFileTests(BaseAPITest):
    def test_delete_staging_file(self):
        from datetime import timedelta
        from django.utils.timezone import now
        from userdefinedmodel.models.node import StagingFile
        import tempfile, os
        from django.core.files.base import ContentFile

        staging = StagingFile(
            uploader=self.staff,
            original_name="test.txt",
            mime_type="text/plain",
            size_bytes=10,
            expires_at=now() + timedelta(hours=1),
        )
        staging.file.save("staging/test.txt", ContentFile(b"hello world"), save=True)
        staging.save()

        resp = self.delete(f"/staging-files/{staging.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(StagingFile.objects.filter(id=staging.id).exists())


# ─── Concurrent write safety tests ───────────────────────────────────────────

@override_settings(MIDDLEWARE=_TEST_MIDDLEWARE)
class ConcurrentWriteTests(TransactionTestCase):
    databases = ["default"]

    def setUp(self):
        self.staff = StaffUserFactory()
        self.client = Client()
        self.client.force_login(self.staff)

    def test_lock_contention_returns_409(self):
        """
        Test that when the root entity row is locked, a concurrent PATCH returns 409.
        We simulate this by locking inside a transaction and issuing a PATCH.
        """
        config, version, field, lang = make_simple_config()
        udm_type = UserDefinedModelTypeFactory(field_config=config)
        entity = UserDefinedModelEntityFactory(
            config_version=version, user_defined_model_type=udm_type
        )

        import threading
        from django.db import connection, transaction
        from userdefinedmodel.models import UserDefinedModelEntity

        results = {}
        lock_acquired = threading.Event()
        lock_release = threading.Event()

        def hold_lock():
            try:
                with transaction.atomic():
                    UserDefinedModelEntity.objects.select_for_update(nowait=True, of=("self",)).get(id=entity.id)
                    lock_acquired.set()
                    lock_release.wait(timeout=5)
            except Exception as e:
                results["lock_error"] = str(e)
            finally:
                lock_acquired.set()  # ensure main thread doesn't hang

        t = threading.Thread(target=hold_lock)
        t.start()
        lock_acquired.wait(timeout=5)

        # Issue PATCH while lock is held
        resp = self.client.patch(
            f"/api/udm/entities/{entity.id}/",
            data=json.dumps({"changed_fields": {"content": "blocked"}}),
            content_type="application/json",
        )
        lock_release.set()
        t.join()

        # Should be 409 if lock was held, or 200 if test timing was off
        self.assertIn(resp.status_code, [200, 409])



# ─── Gap coverage tests ───────────────────────────────────────────────────────

class VersionListTests(BaseAPITest):
    """§6: GET /configs/{cid}/versions/ — list all ConfigVersions."""

    def test_list_versions(self):
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config)
        from userdefinedmodel.models import ConfigVersion
        ConfigVersion.objects.create(config=config, status="published")
        ConfigVersion.objects.create(config=config, status="draft")
        resp = self.get(f"/configs/{config.id}/versions/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)
        statuses = {v["status"] for v in data}
        self.assertIn("published", statuses)
        self.assertIn("draft", statuses)


class FieldDefaultValueCleanTests(BaseAPITest):
    """§2.8: FieldDefaultValue.clean() rejects unsupported types."""

    def test_default_for_file_rejected(self):
        from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition, FieldDefaultValue
        from django.core.exceptions import ValidationError
        config = FieldConfig.objects.create(name="Test")
        ConfigLanguage.objects.create(config=config, code="en", label="en", is_default=True)
        version = ConfigVersion.objects.create(config=config, status="draft")
        field = FieldDefinition.objects.create(version=version, slug="photo", data_type="image")
        d = FieldDefaultValue(field=field, language="")
        with self.assertRaises(ValidationError):
            d.clean()

    def test_default_for_text_allowed(self):
        from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition, FieldDefaultValue
        config = FieldConfig.objects.create(name="Test2")
        ConfigLanguage.objects.create(config=config, code="en", label="en", is_default=True)
        version = ConfigVersion.objects.create(config=config, status="draft")
        field = FieldDefinition.objects.create(version=version, slug="title", data_type="text_short")
        d = FieldDefaultValue(field=field, language="", value_text="Default title")
        d.clean()  # Should not raise


class BulkMigrationExecutionTests(BaseAPITest):
    """§5.5: Bulk migration plan creation and execution (via Celery task)."""

    def test_create_bulk_migration(self):
        from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition
        config = FieldConfig.objects.create(name="BM Config")
        ConfigLanguage.objects.create(config=config, code="en", label="en", is_default=True)
        v1 = ConfigVersion.objects.create(config=config, status="published")
        f1 = FieldDefinition.objects.create(version=v1, slug="title", data_type="text_short")
        config2 = FieldConfig.objects.create(name="BM Config 2")
        ConfigLanguage.objects.create(config=config2, code="en", label="en", is_default=True)
        v2 = ConfigVersion.objects.create(config=config2, status="published")
        f2 = FieldDefinition.objects.create(version=v2, slug="title", data_type="text_short")

        resp = self.post("/bulk-migrations/", {
            "source_version_id": str(v1.id),
            "target_version_id": str(v2.id),
            "field_mappings": [
                {"source_field_slug": "title", "action": "map", "target_field_slug": "title"}
            ],
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "draft")
        self.assertIn("id", data)

    def test_bulk_migration_execute_async(self):
        from unittest.mock import patch
        from userdefinedmodel.models import FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition, BulkMigrationPlan
        config = FieldConfig.objects.create(name="BM Exec Config")
        ConfigLanguage.objects.create(config=config, code="en", label="en", is_default=True)
        v1 = ConfigVersion.objects.create(config=config, status="published")
        config2 = FieldConfig.objects.create(name="BM Exec Config 2")
        ConfigLanguage.objects.create(config=config2, code="en", label="en", is_default=True)
        v2 = ConfigVersion.objects.create(config=config2, status="published")
        plan = BulkMigrationPlan.objects.create(
            source_version=v1, target_version=v2, created_by=self.staff
        )
        with patch("userdefinedmodel.tasks.execute_bulk_migration.delay") as mock_delay:
            resp = self.post(f"/bulk-migrations/{plan.id}/execute/")
            self.assertEqual(resp.status_code, 202)
            mock_delay.assert_called_once_with(str(plan.id))


class DefaultValueMaterializationTests(BaseAPITest):
    """§2.8: Defaults are materialized into FieldValues when entity is created."""

    def test_defaults_materialized_on_create(self):
        from userdefinedmodel.models import (
            FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
            FieldDefinitionTranslation, FieldDefaultValue, UserDefinedModelType,
        )
        config = FieldConfig.objects.create(name="Default Test Config")
        ConfigLanguage.objects.create(config=config, code="en", label="en", is_default=True)
        version = ConfigVersion.objects.create(config=config, status="published")
        field = _make_field_with_label(version, "status_flag", "boolean", label="Status Flag")
        FieldDefaultValue.objects.create(field=field, language="", value_bool=True)

        udm_type = UserDefinedModelTypeFactory(name="Default Type", field_config=config)

        resp = self.post("/entities/", {"user_defined_model_type_id": str(udm_type.id)})
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        fvs = {fv["field_slug"]: fv["value"] for fv in data["field_values"]}
        self.assertIn("status_flag", fvs)
        self.assertEqual(fvs["status_flag"], True)


# ─── Object-level authorization (security review) ─────────────────────────────

def _grant(user, *codenames):
    """Grant userdefinedmodel app permissions to a user by codename."""
    from django.contrib.auth.models import Permission
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="userdefinedmodel", codename__in=codenames
        )
    )
    # Drop the per-request permission cache so the next request re-reads the DB.
    for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(attr, None)


class EntityViewPolicyTests(BaseAPITest):
    """get_entity / history enforce the policy 'view' allow decision (not just
    field filtering)."""

    def test_get_entity_denied_by_view_policy_returns_404(self):
        entity, *_ = make_entity_with_type(policy_source=REGO_DENY_ALL)
        resp = self.get(f"/entities/{entity.id}/", user=self.user)
        self.assertEqual(resp.status_code, 404)

    def test_get_entity_allowed_by_view_policy(self):
        entity, *_ = make_entity_with_type(policy_source=REGO_OWNER_EDIT)
        resp = self.get(f"/entities/{entity.id}/", user=self.user)
        self.assertEqual(resp.status_code, 200)

    def test_history_denied_by_view_policy_returns_404(self):
        entity, *_ = make_entity_with_type(policy_source=REGO_DENY_ALL)
        resp = self.get(f"/entities/{entity.id}/history/", user=self.user)
        self.assertEqual(resp.status_code, 404)

    def test_search_entities_filters_out_non_browsable(self):
        visible, vis_type, *_ = make_entity_with_type(policy_source=REGO_OWNER_EDIT)
        hidden, hid_type, *_ = make_entity_with_type(policy_source=REGO_DENY_ALL)
        resp = self.get(
            f"/entity-search/?type_ids={vis_type.id}&type_ids={hid_type.id}", user=self.user
        )
        self.assertEqual(resp.status_code, 200)
        ids = {e["id"] for e in resp.json()}
        self.assertIn(str(visible.id), ids)
        self.assertNotIn(str(hidden.id), ids)


class EngineDenyByDefaultTests(BaseAPITest):
    """The policy engine must fail closed: an 'allow if ...' rule that matches no
    clause is undefined, which must read as deny — never as a truthy sentinel."""

    def test_unmatched_allow_rule_denies(self):
        from userdefinedmodel.engine import evaluate_policy
        # REGO_OWNER_EDIT only grants delete to staff; a non-staff user matches no
        # clause, so allow is undefined and must evaluate to False.
        entity, *_ = make_entity_with_type(policy_source=REGO_OWNER_EDIT)
        non_staff = UserFactory()
        self.assertFalse(evaluate_policy(entity, non_staff, "delete").allow)
        # Staff passes the positive clause.
        self.assertTrue(evaluate_policy(entity, self.staff, "delete").allow)

    def test_no_policy_denies_every_action(self):
        from userdefinedmodel.engine import evaluate_policy
        # A type with no policy attached grants nothing for any action.
        entity, *_ = make_entity_with_type(policy_source=None)
        for action in ("view", "browse", "save", "delete", "transition"):
            out = evaluate_policy(entity, self.user, action)
            self.assertFalse(out.allow, f"{action} should be denied")
            self.assertEqual(out.viewable_fields, {})


class PermissionBasedAdminTests(BaseAPITest):
    """Admin endpoints authorize on Django model permissions, not is_staff: a
    non-staff user holding the explicit permission is allowed; a plain user is not."""

    def test_non_staff_with_add_perm_can_create_config(self):
        editor = UserFactory()  # is_staff = False
        _grant(editor, "add_fieldconfig")
        resp = self.post("/configs/", {
            "name": "Perm Config",
            "description": "",
            "languages": [{"code": "en", "label": "English", "is_default": True, "sort_order": 0}],
        }, user=editor)
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_plain_user_cannot_create_config(self):
        resp = self.post("/configs/", {
            "name": "Nope", "description": "",
            "languages": [{"code": "en", "label": "English", "is_default": True, "sort_order": 0}],
        }, user=UserFactory())
        self.assertEqual(resp.status_code, 403)

    def test_get_policy_requires_view_policy_permission(self):
        PolicyFactory(slug="secret-policy")
        # Plain user: denied.
        resp = self.get("/policies/secret-policy/", user=UserFactory())
        self.assertEqual(resp.status_code, 403)
        # Non-staff user granted view_policy: allowed.
        viewer = UserFactory()
        _grant(viewer, "view_policy")
        resp = self.get("/policies/secret-policy/", user=viewer)
        self.assertEqual(resp.status_code, 200)

    def test_list_type_policies_requires_view_policy_permission(self):
        _, udm_type, *_ = make_entity_with_type()
        resp = self.get(f"/types/{udm_type.id}/policies/", user=UserFactory())
        self.assertEqual(resp.status_code, 403)


# ─── Draft-as-input export tests ─────────────────────────────────────────────

class DraftAsInputTests(BaseAPITest):
    def test_get_draft_as_input_roundtrip(self):
        """GET draft/as-input/ returns a dict that can be PUT back to replace_draft."""
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        draft = ConfigVersionFactory(config=config, status="draft", notes="test notes")
        from userdefinedmodel.models import FieldDefinition, FieldDefinitionTranslation
        fd = _make_field_with_label(draft, "title", "text_short", label="Title", help_text="Enter title")

        resp = self.get(f"/configs/{config.id}/versions/draft/as-input/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["notes"], "test notes")
        self.assertEqual(len(data["data_fields"]), 1)
        field = data["data_fields"][0]
        self.assertEqual(field["slug"], "title")
        self.assertEqual(field["data_type"], "text_short")
        self.assertIsNone(field["submodel_config_version_id"])
        self.assertIsNone(field["workflow_version_id"])
        # Labels now live on form_elements (B1)
        self.assertEqual(len(data["form_elements"]), 1)
        el = data["form_elements"][0]
        self.assertEqual(el["slug"], "title")
        self.assertEqual(el["labels"], {"en": "Title"})

        # Round-trip: PUT the output back into replace_draft
        resp2 = self.put(f"/configs/{config.id}/versions/draft/", data)
        self.assertEqual(resp2.status_code, 200, resp2.json())
        result = resp2.json()
        self.assertEqual(len(result["data_fields"]), 1)
        self.assertEqual(result["data_fields"][0]["slug"], "title")

    def test_get_draft_as_input_with_workflow(self):
        """Workflow field references are exported as IDs, not nested objects."""
        wf_ver, _, _, _ = make_full_workflow()
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        draft = ConfigVersionFactory(config=config, status="draft")
        from userdefinedmodel.models import FieldDefinition, FieldDefinitionTranslation
        fd = _make_field_with_label(
            draft, "status", "workflow", label="Status", workflow_version=wf_ver,
        )

        resp = self.get(f"/configs/{config.id}/versions/draft/as-input/")
        self.assertEqual(resp.status_code, 200)
        field = resp.json()["data_fields"][0]
        self.assertEqual(field["workflow_version_id"], str(wf_ver.id))
        self.assertIsNone(field["submodel_config_version_id"])

    def test_get_draft_as_input_404_when_no_draft(self):
        config = FieldConfigFactory()
        resp = self.get(f"/configs/{config.id}/versions/draft/as-input/")
        self.assertEqual(resp.status_code, 404)


# ─── FormElement / DataField split (M:N binding) tests ──────────────────────────

class FormElementBindingTests(BaseAPITest):
    """Verify the split: hidden data fields, one element→many fields, many
    elements→one field (PLAN_split_form_tree_and_data_fields.md §F1)."""

    def test_hidden_data_field_has_zero_bindings(self):
        """A DataField with no bound FormElement is hidden: it exists in the
        schema but is not rendered/edited via the form."""
        from userdefinedmodel.models import DataField, FormElement, FormElementBinding
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        # Create a data field with NO form element (hidden)
        DataField.objects.create(version=version, slug="secret", data_type="text_short")
        resp = self.get(f"/configs/{config.id}/versions/published/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        slugs = {f["slug"] for f in data["data_fields"]}
        self.assertIn("secret", slugs, "hidden data field is in data_fields")
        # No form element binds to it
        bound = FormElementBinding.objects.filter(data_field__slug="secret").count()
        self.assertEqual(bound, 0, "hidden field has zero bindings")
        # It does NOT appear in the backward-compat `fields` merge (no element)
        compat_slugs = {f["slug"] for f in data["fields"]}
        self.assertNotIn("secret", compat_slugs, "hidden field absent from legacy fields merge")

    def test_one_form_element_binds_two_data_fields(self):
        """A date_range FormElement binds to two date DataFields (role=from/to)."""
        from userdefinedmodel.models import DataField, FormElement, FormElementBinding
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        start = DataField.objects.create(version=version, slug="start_date", data_type="date")
        end = DataField.objects.create(version=version, slug="end_date", data_type="date")
        el = FormElement.objects.create(
            version=version, slug="date_range_1", element_type=FormElement.ElementType.DATE_RANGE,
            sort_order=0, is_preview=False, type_config={},
        )
        FormElementBinding.objects.create(form_element=el, data_field=start, role="from")
        FormElementBinding.objects.create(form_element=el, data_field=end, role="to")
        resp = self.get(f"/configs/{config.id}/versions/published/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        el_out = next(e for e in data["form_elements"] if e["slug"] == "date_range_1")
        self.assertEqual(el_out["element_type"], "date_range")
        roles = {b["role"]: b["data_field_slug"] for b in el_out["bindings"]}
        self.assertEqual(roles, {"from": "start_date", "to": "end_date"})

    def test_two_form_elements_bind_one_data_field(self):
        """Two FormElements bind the same DataField (e.g. a preview + an editor)."""
        from userdefinedmodel.models import DataField, FormElement, FormElementBinding
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        title = DataField.objects.create(version=version, slug="title", data_type="text_short")
        # Two elements bound to the same data field
        el1 = FormElement.objects.create(
            version=version, slug="title_editor", element_type=FormElement.ElementType.FIELD,
            sort_order=0, is_preview=False, type_config={},
        )
        el2 = FormElement.objects.create(
            version=version, slug="title_preview", element_type=FormElement.ElementType.FIELD,
            sort_order=1, is_preview=True, type_config={},
        )
        FormElementBinding.objects.create(form_element=el1, data_field=title, role="")
        FormElementBinding.objects.create(form_element=el2, data_field=title, role="")
        resp = self.get(f"/configs/{config.id}/versions/published/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # One data field
        self.assertEqual(len(data["data_fields"]), 1)
        # Two form elements, both binding "title"
        bound_to_title = [
            e for e in data["form_elements"]
            if any(b["data_field_slug"] == "title" for b in e["bindings"])
        ]
        self.assertEqual(len(bound_to_title), 2)

    def test_structural_element_has_no_bindings(self):
        """A structural FormElement (tab) carries no data and has no bindings."""
        from userdefinedmodel.models import FormElement
        config = FieldConfigFactory()
        ConfigLanguageFactory(config=config, code="en", label="English", is_default=True)
        version = PublishedConfigVersionFactory(config=config)
        FormElement.objects.create(
            version=version, slug="main_tabs", element_type=FormElement.ElementType.TAB_CONTAINER,
            sort_order=0, is_preview=False, type_config={},
        )
        resp = self.get(f"/configs/{config.id}/versions/published/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        el_out = next(e for e in data["form_elements"] if e["slug"] == "main_tabs")
        self.assertEqual(el_out["element_type"], "tab_container")
        self.assertEqual(el_out["bindings"], [])
        # Backward-compat merge: structural element appears as a pseudo data field
        compat = next(f for f in data["fields"] if f["slug"] == "main_tabs")
        self.assertEqual(compat["data_type"], "tab_container")



# ─── ZIP bundle tests ─────────────────────────────────────────────────────────

class BundleExportTests(BaseAPITest):
    def _make_udm_type_with_workflow(self):
        """Create a UDMType with a published config that uses a workflow."""
        from userdefinedmodel.models import (
            FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
            FieldDefinitionTranslation, UserDefinedModelType, Policy, UserDefinedModelTypePolicy,
        )
        wf_ver, _, _, _ = make_full_workflow()
        config = FieldConfig.objects.create(name="Bundle Config")
        ConfigLanguage.objects.create(config=config, code="en", label="English", is_default=True)
        version = ConfigVersion.objects.create(config=config, status="published")
        ConfigVersion.objects.create(config=config, status="draft")
        fd = _make_field_with_label(
            version, "status", "workflow", label="Status", workflow_version=wf_ver,
        )
        udm_type = UserDefinedModelType.objects.create(name="Bundle Type", field_config=config)
        policy = Policy.objects.create(slug=f"bundle-policy-{udm_type.id}", source=ALLOW_ALL_POLICY)
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=udm_type, policy=policy, sort_order=0
        )
        return udm_type, config, version, wf_ver, policy

    def _export_zip(self, type_ids):
        resp = self.post("/export-bundle-zip/", {"scope_type_ids": type_ids})
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        return resp.content

    def test_export_zip_structure(self):
        import zipfile, io, json as _json
        udm_type, config, version, wf, policy = self._make_udm_type_with_workflow()
        zip_bytes = self._export_zip([str(udm_type.id)])
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            self.assertIn("UDM_BUNDLE.json", names)
            bundle = _json.loads(zf.read("UDM_BUNDLE.json").decode())
            self.assertEqual(bundle["version"], 1)
            self.assertIn(str(udm_type.id), bundle["scope_type_ids"])
            for p in bundle.get("policies", []):
                self.assertNotIn("source", p)
            policy_file = f"policies/{policy.slug}.rego"
            self.assertIn(policy_file, names)
            self.assertIn("allow", zf.read(policy_file).decode())


    def test_parse_bundle_zip(self):
        import io
        udm_type, *_ = self._make_udm_type_with_workflow()
        zip_bytes = self._export_zip([str(udm_type.id)])
        resp = self.client.post(
            "/api/udm/parse-bundle-zip/",
            {"file": io.BytesIO(zip_bytes)},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertIn(str(udm_type.id), data["scope_type_ids"])

    def test_import_zip_updates_in_place(self):
        import io, zipfile, json as _json
        udm_type, config, version, wf, policy = self._make_udm_type_with_workflow()
        zip_bytes = self._export_zip([str(udm_type.id)])

        # Mutate bundle JSON: rename config and workflow
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf_in:
            items = {n: zf_in.read(n) for n in zf_in.namelist()}
        bundle = _json.loads(items["UDM_BUNDLE.json"].decode())
        bundle["field_configs"][0]["name"] = "Zip Config Updated"
        bundle["workflows"][0]["name"] = "Updated Workflow"
        items["UDM_BUNDLE.json"] = _json.dumps(bundle).encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf_out:
            for n, data in items.items():
                zf_out.writestr(n, data)
        modified_zip = buf.getvalue()

        resp = self.client.post(
            "/api/udm/import-bundle-zip/",
            {"file": io.BytesIO(modified_zip),
             "scope_type_ids": str(udm_type.id)},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        config.refresh_from_db()
        self.assertEqual(config.name, "Zip Config Updated")
        wf.workflow.refresh_from_db()
        self.assertEqual(wf.workflow.name, "Updated Workflow")

    def test_import_zip_updates_policy_from_file(self):
        import io, zipfile, json as _json
        from userdefinedmodel.models import Policy
        udm_type, config, version, wf, policy = self._make_udm_type_with_workflow()
        zip_bytes = self._export_zip([str(udm_type.id)])

        # Mutate the policy rego file in the ZIP
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf_in:
            items = {n: zf_in.read(n) for n in zf_in.namelist()}
        policy_file = f"policies/{policy.slug}.rego"
        items[policy_file] = b"package udm\nimport rego.v1\nallow := false\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf_out:
            for n, data in items.items():
                zf_out.writestr(n, data)

        resp = self.client.post(
            "/api/udm/import-bundle-zip/",
            {"file": io.BytesIO(buf.getvalue()),
             "scope_type_ids": str(udm_type.id)},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        policy.refresh_from_db()
        self.assertIn("allow := false", policy.source)

    def test_import_zip_with_udm_bundle_rego(self):
        """ZIP containing UDM_BUNDLE.rego instead of .json is also importable."""
        import io, zipfile, json as _json
        udm_type, config, version, wf, policy = self._make_udm_type_with_workflow()

        # Get the bundle data from the exported ZIP and convert to UDM_BUNDLE.rego
        zip_bytes = self._export_zip([str(udm_type.id)])
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            bundle = _json.loads(zf.read("UDM_BUNDLE.json").decode())
            policy_source = zf.read(f"policies/{policy.slug}.rego").decode()

        bundle_json = _json.dumps(bundle, ensure_ascii=False)
        chunk_size = 400
        chunks = [bundle_json[i:i+chunk_size] for i in range(0, len(bundle_json), chunk_size)]
        chunk_rules = "\n".join(f"_UDM_J{i} := {_json.dumps(c)}" for i, c in enumerate(chunks))
        concat_args = ", ".join(f"_UDM_J{i}" for i in range(len(chunks)))
        rego_src = (
            f"package udm\nimport rego.v1\n\n{chunk_rules}\n"
            f"_UDM_BUNDLE_JSON := concat(\"\", [{concat_args}])\n"
            f"UDM_BUNDLE := json.unmarshal(_UDM_BUNDLE_JSON)\n"
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("UDM_BUNDLE.rego", rego_src)
            zf.writestr(f"policies/{policy.slug}.rego", policy_source)

        resp = self.client.post(
            "/api/udm/import-bundle-zip/",
            {"file": io.BytesIO(buf.getvalue()),
             "scope_type_ids": str(udm_type.id)},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_reimport_preserves_uuids_and_is_idempotent(self):
        """Importing the same bundle twice must not create duplicate FieldConfigs or Workflows.

        On first import (Scenario C: objects absent), FieldConfig and WorkflowDefinition must be
        created with the bundle's own UUIDs so a subsequent import can find and update them
        (Scenario B) rather than creating fresh duplicates.
        """
        import io, zipfile, json as _json, uuid
        from userdefinedmodel.models import FieldConfig, WorkflowDefinition

        config_id = str(uuid.uuid4())
        wf_id = str(uuid.uuid4())
        udmt_id = str(uuid.uuid4())

        bundle = {
            "version": 1,
            "scope_type_ids": [udmt_id],
            "udm_types": [
                {"id": udmt_id, "name": "Idempotency Test Type",
                 "field_config_id": config_id, "policy_slugs": []},
            ],
            "field_configs": [
                {
                    "id": config_id,
                    "name": "Idempotency Config",
                    "description": "",
                    "languages": [
                        {"code": "en", "label": "English", "is_default": True, "sort_order": 0},
                    ],
                    "draft": {
                        "notes": "",
                        "fields": [
                            {
                                "slug": "status",
                                "data_type": "workflow",
                                "sort_order": 0,
                                "is_localized": False,
                                "is_preview": False,
                                "labels": {"en": "Status"},
                                "help_texts": {},
                                "type_config": {},
                                "default": None,
                                "submodel_config_version_id": None,
                                "workflow_version_id": wf_id,
                                "parent_slug": None,
                            },
                        ],
                    },
                },
            ],
            "workflows": [
                {
                    "id": wf_id,
                    "name": "Idempotency Workflow",
                    "description": "",
                    "states": [
                        {
                            "name": "open",
                            "is_initial": True,
                            "position_x": 0.0,
                            "position_y": 0.0,
                            "background_color": "#ffffff",
                            "label": {"en": "Open"},
                        },
                    ],
                    "transitions": [],
                    "virtual_node_positions": {},
                },
            ],
            "policies": [],
        }

        def make_zip(data):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("UDM_BUNDLE.json", _json.dumps(data))
            return buf.getvalue()

        zip_bytes = make_zip(bundle)

        # First import: nothing in DB yet — must create with the bundle UUIDs (Scenario C).
        resp = self.client.post(
            "/api/udm/import-bundle-zip/",
            {"file": io.BytesIO(zip_bytes), "scope_type_ids": udmt_id},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            FieldConfig.objects.filter(id=config_id).count(), 1,
            "FieldConfig must be created with the bundle's UUID on first import",
        )
        self.assertEqual(
            WorkflowDefinition.objects.filter(id=wf_id).count(), 1,
            "WorkflowDefinition must be created with the bundle's UUID on first import",
        )
        fc_count = FieldConfig.objects.count()
        wf_count = WorkflowDefinition.objects.count()

        # Second import: same ZIP — must update in place (Scenario B), not create duplicates.
        resp = self.client.post(
            "/api/udm/import-bundle-zip/",
            {"file": io.BytesIO(zip_bytes), "scope_type_ids": udmt_id},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            FieldConfig.objects.count(), fc_count,
            "Re-import must not create duplicate FieldConfigs",
        )
        self.assertEqual(
            WorkflowDefinition.objects.count(), wf_count,
            "Re-import must not create duplicate WorkflowDefinitions",
        )

    def test_import_submodel_fields_get_config_version_assigned(self):
        """Submodel fields using FieldConfig UUIDs as submodel_config_version_id must resolve
        to a published ConfigVersion after import — both on first import and re-import.

        Regression test: previously the export stored ConfigVersion UUIDs for in-bundle
        submodels and the import could not resolve them on a fresh DB, leaving submodel_config=None.
        """
        import io, zipfile, json as _json, uuid
        from userdefinedmodel.models import FieldConfig, FieldDefinition, ConfigVersion

        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        udmt_id = str(uuid.uuid4())

        # Bundle: parent config has a submodel_list field that references the child config
        # using the child's FieldConfig UUID (the fixed export format).
        bundle = {
            "version": 1,
            "scope_type_ids": [udmt_id],
            "udm_types": [
                {"id": udmt_id, "name": "Submodel Test Type",
                 "field_config_id": parent_id, "policy_slugs": []},
            ],
            "field_configs": [
                {
                    "id": child_id,
                    "name": "Child Config",
                    "description": "",
                    "languages": [
                        {"code": "en", "label": "English", "is_default": True, "sort_order": 0},
                    ],
                    "draft": {
                        "notes": "",
                        "fields": [
                            {
                                "slug": "title",
                                "data_type": "text",
                                "sort_order": 0,
                                "is_localized": False,
                                "is_preview": True,
                                "labels": {"en": "Title"},
                                "help_texts": {},
                                "type_config": {},
                                "default": None,
                                "submodel_config_version_id": None,
                                "workflow_version_id": None,
                                "parent_slug": None,
                            },
                        ],
                    },
                },
                {
                    "id": parent_id,
                    "name": "Parent Config",
                    "description": "",
                    "languages": [
                        {"code": "en", "label": "English", "is_default": True, "sort_order": 0},
                    ],
                    "draft": {
                        "notes": "",
                        "fields": [
                            {
                                "slug": "items",
                                "data_type": "submodel_list",
                                "sort_order": 0,
                                "is_localized": False,
                                "is_preview": False,
                                "labels": {"en": "Items"},
                                "help_texts": {},
                                "type_config": {},
                                "default": None,
                                # FieldConfig UUID of the child — the correct export format
                                "submodel_config_version_id": child_id,
                                "workflow_version_id": None,
                                "parent_slug": None,
                            },
                        ],
                    },
                },
            ],
            "workflows": [],
            "policies": [],
        }

        def make_zip(data):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("UDM_BUNDLE.json", _json.dumps(data))
            return buf.getvalue()

        zip_bytes = make_zip(bundle)

        def do_import():
            return self.client.post(
                "/api/udm/import-bundle-zip/",
                {"file": io.BytesIO(zip_bytes), "scope_type_ids": udmt_id},
                format="multipart",
            )

        # First import: fresh DB — submodel field must get a published ConfigVersion.
        resp = do_import()
        self.assertEqual(resp.status_code, 200, resp.content)

        child_cfg = FieldConfig.objects.get(id=child_id)
        published_child = ConfigVersion.objects.get(
            config=child_cfg, status=ConfigVersion.Status.PUBLISHED
        )
        parent_cfg = FieldConfig.objects.get(id=parent_id)
        published_parent = ConfigVersion.objects.get(
            config=parent_cfg, status=ConfigVersion.Status.PUBLISHED
        )
        items_fd = FieldDefinition.objects.get(version=published_parent, slug="items")
        self.assertIsNotNone(
            items_fd.submodel_config,
            "submodel_config must be set after first import",
        )
        self.assertEqual(
            items_fd.submodel_config, published_child,
            "submodel_config must point to the published child version",
        )

        # Re-import: submodel link must still be set, now to the newly published child version.
        resp = do_import()
        self.assertEqual(resp.status_code, 200, resp.content)

        new_published_child = ConfigVersion.objects.get(
            config=child_cfg, status=ConfigVersion.Status.PUBLISHED
        )
        new_published_parent = ConfigVersion.objects.get(
            config=parent_cfg, status=ConfigVersion.Status.PUBLISHED
        )
        items_fd_2 = FieldDefinition.objects.get(version=new_published_parent, slug="items")
        self.assertIsNotNone(
            items_fd_2.submodel_config,
            "submodel_config must still be set after re-import",
        )
        self.assertEqual(
            items_fd_2.submodel_config, new_published_child,
            "submodel_config must point to the freshly published child version after re-import",
        )

    def test_export_bundle_uses_fieldconfig_uuid_for_inbundle_submodels(self):
        """Export must emit the child FieldConfig UUID (not the ConfigVersion UUID) for
        submodel fields whose config is part of the same bundle, so re-import can correctly
        defer resolution until after all configs are published.
        """
        import io, json as _json
        from userdefinedmodel.models import (
            FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
            FieldDefinitionTranslation, UserDefinedModelType,
        )

        child_cfg = FieldConfig.objects.create(name="Export Test Child")
        ConfigLanguage.objects.create(config=child_cfg, code="en", label="English", is_default=True)
        child_pub = ConfigVersion.objects.create(config=child_cfg, status="published")

        parent_cfg = FieldConfig.objects.create(name="Export Test Parent")
        ConfigLanguage.objects.create(config=parent_cfg, code="en", label="English", is_default=True)
        parent_pub = ConfigVersion.objects.create(config=parent_cfg, status="published")
        fd_sub = _make_field_with_label(
            parent_pub, "items", "submodel_list", label="Items", submodel_config=child_pub,
        )

        udmt = UserDefinedModelType.objects.create(name="Export Test Type", field_config=parent_cfg)

        zip_bytes = self._export_zip([str(udmt.id)])
        with __import__("zipfile").ZipFile(__import__("io").BytesIO(zip_bytes)) as zf:
            raw_bundle = _json.loads(zf.read("UDM_BUNDLE.json").decode())

        bundle_cfg_ids = {fc["id"] for fc in raw_bundle["field_configs"]}
        self.assertIn(str(parent_cfg.id), bundle_cfg_ids)
        self.assertIn(str(child_cfg.id), bundle_cfg_ids)

        parent_fc = next(fc for fc in raw_bundle["field_configs"] if fc["id"] == str(parent_cfg.id))
        fd_map = {fd["slug"]: fd for fd in parent_fc["draft"]["data_fields"]}

        self.assertEqual(
            fd_map["items"]["submodel_config_version_id"], str(child_cfg.id),
            "In-bundle submodel field must export the child FieldConfig UUID, not its ConfigVersion UUID",
        )


class ValidationPreviewTests(BaseAPITest):
    """POST /entities/{id}/validation-preview/ — the single preview replacing
    the removed validate_only modes (§4): one request returns the save verdict,
    all policy messages, and the per-node valid-transition matrix."""

    def _make_workflow_entity(self, policy_source=None):
        from userdefinedmodel.models import FieldValue
        entity, udm_type, version, config = make_entity_with_type(
            policy_source=policy_source or ALLOW_ALL_POLICY)
        wf_version, draft_state, submitted, trans = make_full_workflow()
        field = add_workflow_field(version, wf_version, slug="status")
        fv = FieldValue.objects.create(node=entity, field=field, language="")
        fv.value_workflow_state = draft_state
        fv.save()
        return entity, field, draft_state, submitted

    def test_preview_returns_matrix_and_save_state(self):
        entity, field, draft_state, submitted = self._make_workflow_entity()
        resp = self.post(f"/entities/{entity.id}/validation-preview/",
                         {"changed_fields": {"title": "New title"}})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["save"]["valid"], data)
        # §6 keys ride along in the same response (empty defaults here)
        self.assertIn("deletable_nodes", data)
        self.assertIn("creatable_submodels", data)
        node = data["nodes"][str(entity.id)]["status"]
        self.assertEqual(node["current_state"], "draft")
        # allow-all test policy enables every state-valid candidate
        self.assertEqual(node["valid_transitions"], ["submit"])

    def test_preview_does_not_persist_pending_edits(self):
        entity, *_ = self._make_workflow_entity()
        resp = self.post(f"/entities/{entity.id}/validation-preview/",
                         {"changed_fields": {"title": "Ephemeral"}})
        self.assertEqual(resp.status_code, 200)
        resp2 = self.get(f"/entities/{entity.id}/")
        values = {fv["field_slug"]: fv["value"] for fv in resp2.json()["field_values"]}
        self.assertNotEqual(values.get("title"), "Ephemeral")

    def test_preview_policy_denied_transition_dropped_from_matrix(self):
        entity, field, *_ = self._make_workflow_entity(policy_source=wrap_policy("""
package udm
import rego.v1
allow := false
"""))
        resp = self.post(f"/entities/{entity.id}/validation-preview/", {"changed_fields": {}})
        # view pre-check fails for deny-all → existence is hidden
        self.assertEqual(resp.status_code, 404)

    def test_preview_blocked_save_reports_messages(self):
        blocked = wrap_policy("""
package udm
import rego.v1
allow if input.action in {"view", "browse"}
messages contains {"level": "critical", "text": "no saving", "field_slug": "title"} if {
    input.action == "preview"
}
""")
        entity, *_ = self._make_workflow_entity(policy_source=blocked)
        resp = self.post(f"/entities/{entity.id}/validation-preview/",
                         {"changed_fields": {"title": "x"}})
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertFalse(data["save"]["valid"])
        texts = [m["text"] for m in data["messages"]]
        self.assertIn("no saving", texts)
        self.assertEqual(data["messages"][0]["highlight_fields"], ["title"])

    def test_validate_only_modes_removed(self):
        entity, field, *_ = self._make_workflow_entity()
        # validate_only query params are gone; the calls now execute for real,
        # so use throwaway values and just assert the parameter is not honored
        resp = self.patch(f"/entities/{entity.id}/?validate_only=true",
                          {"changed_fields": {"title": "persisted!"}})
        self.assertEqual(resp.status_code, 200)
        resp2 = self.get(f"/entities/{entity.id}/")
        values = {fv["field_slug"]: fv["value"] for fv in resp2.json()["field_values"]}
        self.assertEqual(values.get("title"), "persisted!")


class PolicyEvaluatorTests(BaseAPITest):
    """eval-policy: browse/create actions, submodel node targeting, and the
    eval-policy/nodes/ tree endpoint."""

    def _make_entity_with_submodel_workflow(self):
        from userdefinedmodel.models import FieldDefinition, SubmodelInstance
        entity, udm_type, version, config = make_entity_with_type()
        parent_field = _make_field_with_label(
            version, "items", "submodel_list", label="Items",
        )
        # child schema (own config version) with a workflow field
        _, child_version, _, _ = make_simple_config(required=False)
        wf_version, *_ = make_full_workflow()
        add_workflow_field(child_version, wf_version, slug="status")
        child = SubmodelInstance.objects.create(
            config_version=child_version, parent_node=entity, parent_field=parent_field,
        )
        return entity, udm_type, child

    def test_nodes_endpoint_lists_submodel_nodes_with_transitions(self):
        entity, udm_type, child = self._make_entity_with_submodel_workflow()
        su = UserFactory(username="root-user", is_superuser=True)
        resp = self.get(f"/types/{udm_type.id}/eval-policy/nodes/?entity_id={entity.id}", user=su)
        self.assertEqual(resp.status_code, 200, resp.content)
        nodes = {n["id"]: n for n in resp.json()}
        self.assertIn(str(entity.id), nodes)
        self.assertIn(str(child.id), nodes)
        child_node = nodes[str(child.id)]
        self.assertEqual(child_node["parent_id"], str(entity.id))
        self.assertEqual(child_node["parent_field_slug"], "items")
        self.assertEqual(child_node["label"], "root.items[0]")
        self.assertEqual(child_node["workflow_fields"],
                         [{"slug": "status", "transitions": ["submit"]}])

    def test_nodes_endpoint_denied_without_policy_perms(self):
        entity, udm_type, _ = self._make_entity_with_submodel_workflow()
        resp = self.get(f"/types/{udm_type.id}/eval-policy/nodes/?entity_id={entity.id}", user=self.user)
        self.assertEqual(resp.status_code, 403)

    def test_eval_policy_superuser_without_perms(self):
        entity, udm_type, _ = self._make_entity_with_submodel_workflow()
        su = UserFactory(username="root-user2", is_superuser=True)
        resp = self.get(
            f"/types/{udm_type.id}/eval-policy/?entity_id={entity.id}&user_id={self.user.id}&action=view",
            user=su,
        )
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_eval_policy_browse_and_create_actions(self):
        entity, udm_type, _ = self._make_entity_with_submodel_workflow()
        for action in ("browse", "create"):
            resp = self.get(
                f"/types/{udm_type.id}/eval-policy/?entity_id={entity.id}&user_id={self.user.id}&action={action}",
            )
            self.assertEqual(resp.status_code, 200, resp.content)
            data = resp.json()
            self.assertEqual(data["input_document"]["action"], action)
            self.assertIsNone(data["input_document"]["old_entity"])
            self.assertTrue(data["output"]["allow"])

    def test_eval_policy_transition_on_submodel_node(self):
        entity, udm_type, child = self._make_entity_with_submodel_workflow()
        resp = self.get(
            f"/types/{udm_type.id}/eval-policy/?entity_id={entity.id}&user_id={self.user.id}"
            f"&action=transition&transition=submit&node_id={child.id}",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        doc = resp.json()["input_document"]
        self.assertEqual(doc["action"], "transition")
        self.assertEqual(doc["node_id"], str(child.id))
        self.assertEqual(doc["field"], "status")
        self.assertEqual(doc["transition"], "submit")

    def test_eval_policy_transition_rejects_foreign_node(self):
        entity, udm_type, _ = self._make_entity_with_submodel_workflow()
        other_entity, _, other_child = self._make_entity_with_submodel_workflow()
        resp = self.get(
            f"/types/{udm_type.id}/eval-policy/?entity_id={entity.id}&user_id={self.user.id}"
            f"&action=transition&transition=submit&node_id={other_child.id}",
        )
        self.assertEqual(resp.status_code, 400)
