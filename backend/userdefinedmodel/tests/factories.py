"""
factory_boy factories for userdefinedmodel test data.

Usage:
    config = FieldConfigFactory()          # with one language
    version = PublishedConfigVersionFactory(config=config)
    udm_type = UserDefinedModelTypeFactory(field_config=config)
    entity = UserDefinedModelEntityFactory(
        user_defined_model_type=udm_type,
        config_version=version,
    )
"""
import factory
from django.contrib.auth.models import Group
from factory.django import DjangoModelFactory


# ─── User ────────────────────────────────────────────────────────────────────

class UserFactory(DjangoModelFactory):
    class Meta:
        model = "openid_user_management.OpenIDUser"
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")
    is_active = True
    is_staff = False

    @classmethod
    def staff(cls, **kwargs):
        return cls(is_staff=True, **kwargs)


class StaffUserFactory(UserFactory):
    """Admin user that manages configs/types/policies. The API authorizes these
    operations against explicit Django model permissions (not is_staff), so grant
    every userdefinedmodel permission here."""
    is_staff = True

    @factory.post_generation
    def grant_udm_perms(obj, create, extracted, **kwargs):
        if not create:
            return
        from django.contrib.auth.models import Permission
        obj.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="userdefinedmodel")
        )


# ─── FieldConfig ─────────────────────────────────────────────────────────────

class FieldConfigFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.FieldConfig"

    name = factory.Sequence(lambda n: f"Config {n}")
    description = ""


class ConfigLanguageFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.ConfigLanguage"

    config = factory.SubFactory(FieldConfigFactory)
    code = "en"
    label = "English"
    is_default = True
    sort_order = 0


class WorkflowDefinitionFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.WorkflowDefinition"

    name = factory.Sequence(lambda n: f"Workflow {n}")
    description = ""


class WorkflowVersionFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.WorkflowVersion"

    workflow = factory.SubFactory(WorkflowDefinitionFactory)
    status = "draft"
    notes = ""


class PublishedWorkflowVersionFactory(WorkflowVersionFactory):
    status = "published"


class WorkflowStateFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.WorkflowState"

    version = factory.SubFactory(WorkflowVersionFactory)
    name = factory.Sequence(lambda n: f"state{n}")
    is_initial = False


class WorkflowTransitionFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.WorkflowTransition"

    version = factory.SubFactory(WorkflowVersionFactory)
    name = factory.Sequence(lambda n: f"transition{n}")
    from_state = None
    to_state = factory.SubFactory(WorkflowStateFactory)


class ConfigVersionFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.ConfigVersion"

    config = factory.SubFactory(FieldConfigFactory)
    status = "draft"
    notes = ""


class PublishedConfigVersionFactory(ConfigVersionFactory):
    status = "published"


class FieldDefinitionFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.DataField"

    version = factory.SubFactory(ConfigVersionFactory)
    slug = factory.Sequence(lambda n: f"field{n}")
    data_type = "text_short"
    is_localized = False
    type_config = {}

    @factory.post_generation
    def with_form_element(obj, create, extracted, **kwargs):
        """Backward-compat: every data field created via this factory also gets a
        1:1 'field' FormElement bound to it, plus an English translation whose
        label mirrors the slug. Pass with_form_element=False to skip (hidden field)."""
        if not create or extracted is False:
            return
        from userdefinedmodel.models import FormElement, FormElementTranslation, FormElementBinding
        el = FormElement.objects.create(
            version=obj.version, slug=obj.slug, element_type=FormElement.ElementType.FIELD,
            sort_order=0, is_preview=False, type_config={},
        )
        FormElementBinding.objects.create(form_element=el, data_field=obj, role="")
        FormElementTranslation.objects.create(
            element=el, language="en",
            label=obj.slug.replace("_", " ").title(),
            help_text="",
        )


# Alias for new code
DataFieldFactory = FieldDefinitionFactory


class FormElementFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.FormElement"

    version = factory.SubFactory(ConfigVersionFactory)
    slug = factory.Sequence(lambda n: f"element{n}")
    element_type = "field"
    sort_order = factory.Sequence(lambda n: n)
    is_preview = False
    type_config = {}


class FieldDefinitionTranslationFactory(DjangoModelFactory):
    """Deprecated: labels now live on FormElement. This factory creates a
    FormElementTranslation for a FormElement."""
    class Meta:
        model = "userdefinedmodel.FormElementTranslation"

    element = factory.SubFactory(FormElementFactory)
    language = "en"
    label = factory.LazyAttribute(lambda obj: obj.element.slug.replace("_", " ").title())
    help_text = ""


# ─── Validation rules ─────────────────────────────────────────────────────────

class RequiredRuleFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.RequiredRule"

    field = factory.SubFactory(FieldDefinitionFactory)
    applies_to_save = True
    admin_label = "Required"


class MaxLengthRuleFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.MaxLengthRule"

    field = factory.SubFactory(FieldDefinitionFactory)
    applies_to_save = True
    max_length = 500
    admin_label = ""


class MinValueRuleFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.MinValueRule"

    field = factory.SubFactory(FieldDefinitionFactory, data_type="integer")
    applies_to_save = True
    min_value = 0
    admin_label = ""


# ─── Policy ───────────────────────────────────────────────────────────────────

# Shared result aggregation appended to every test policy: the engine reads
# ONLY data.udm.result (contract §3.1-1). Test policies define plain `allow`
# (and optionally `messages` / `valid_transitions` / `actions` partial sets);
# this suffix assembles the fixed-schema result object, granting every field of
# every node when allowed (per-node maps, deny-by-default when not).
RESULT_SUFFIX = """
# ── test-framework result aggregation (appended by factories.RESULT_SUFFIX) ──
messages contains {"level": "debug", "text": "-"} if false

valid_transitions contains {"node": "-", "field": "-", "name": "-"} if false

actions contains {} if false

dashboard_columns contains {} if false

effective["_placeholder"] := "" if false

_tf_nodes := {n |
    walk(input.entity, [_, n])
    is_object(n)
    object.get(n, "schema_id", null) != null
}

_tf_grant_all[node_id] := slugs if {
    some n in _tf_nodes
    node_id := n.id
    slugs := sort([s | some s, _ in n.fields])
}

default _tf_allow := false

_tf_allow := allow

default force_delete := false

_tf_fields := _tf_grant_all if _tf_allow

else := {}

# Preview matrix: an allowing test policy enables every state-valid candidate.
valid_transitions contains {"node": n, "field": f, "name": name} if {
    _tf_allow
    some n, wfs in input.candidate_transitions
    some f, wf in wfs
    some name, _ in wf.transitions
}

additional_result["view_allowed"] := _tf_allow

additional_result["editable"] := [{"node": nid, "field": fs} | some nid, slugs in _tf_fields; some fs in slugs]

result := {
    "allow": _tf_allow,
    "messages": [m | some m in messages],
    "viewable_fields": _tf_fields,
    "editable_fields": _tf_fields,
    "valid_transitions": [t | some t in valid_transitions],
    "actions": [a | some a in actions],
    "force_delete": force_delete,
    "dashboard_columns": [c | some c in dashboard_columns],
    "additional_result": additional_result,
    "effective": effective,
}
"""


def wrap_policy(body: str) -> str:
    """Append the shared result aggregation to a test policy body."""
    return body + RESULT_SUFFIX


# Minimal allow-all policy for tests that need auth but don't care about rules
ALLOW_ALL_POLICY = wrap_policy("""
package udm

import rego.v1

allow := true
""")

# Staff-only edit policy
STAFF_EDIT_POLICY = wrap_policy("""
package udm

import rego.v1

allow if {
    input.action in {"view", "browse"}
}

allow if {
    input.action in {"edit", "save", "create", "delete"}
    input.user.is_staff
}
""")


class PolicyFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.Policy"
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"policy-{n}")
    source = ALLOW_ALL_POLICY


# ─── UserDefinedModelType ─────────────────────────────────────────────────────

class UserDefinedModelTypeFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.UserDefinedModelType"

    name = factory.Sequence(lambda n: f"Type {n}")
    field_config = None

    @factory.post_generation
    def policy(obj, create, extracted, **kwargs):
        """Attach a policy so the default-deny engine permits operations on this
        type. Defaults to allow-all; pass policy=<rego source> for a custom policy,
        or policy=False to leave the type policy-less (i.e. deny everything)."""
        if not create or extracted is False:
            return
        from userdefinedmodel.models import Policy, UserDefinedModelTypePolicy
        source = extracted if isinstance(extracted, str) else ALLOW_ALL_POLICY
        policy = Policy.objects.create(slug=f"type-policy-{obj.id}", source=source)
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=obj, policy=policy, sort_order=0
        )


# ─── Entity nodes ─────────────────────────────────────────────────────────────

class UserDefinedModelEntityFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.UserDefinedModelEntity"

    config_version = factory.SubFactory(PublishedConfigVersionFactory)
    user_defined_model_type = factory.SubFactory(UserDefinedModelTypeFactory)


class SubmodelInstanceFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.SubmodelInstance"

    config_version = factory.SubFactory(PublishedConfigVersionFactory)
    parent_node = factory.SubFactory(UserDefinedModelEntityFactory)
    parent_field = factory.SubFactory(FieldDefinitionFactory, data_type="submodel_list")
    sort_order = factory.Sequence(lambda n: n)


class FieldValueFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.FieldValue"

    node = factory.SubFactory(UserDefinedModelEntityFactory)
    field = factory.SubFactory(FieldDefinitionFactory)
    language = ""
    value_text = None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_simple_config(data_type="text_short", required=True, max_length=None):
    """
    Create a complete FieldConfig→published ConfigVersion→DataField+FormElement set
    suitable for testing entities.

    Returns: (config, version, field_def, language)
    """
    from userdefinedmodel.models import (
        FieldConfig, ConfigLanguage, ConfigVersion, DataField,
        FormElement, FormElementTranslation, FormElementBinding,
        RequiredRule, MaxLengthRule,
    )

    config = FieldConfig.objects.create(name="Test Config", description="")
    lang = ConfigLanguage.objects.create(config=config, code="en", label="English", is_default=True)

    version = ConfigVersion.objects.create(config=config, status="published")
    field = DataField.objects.create(
        version=version, slug="content", data_type=data_type,
        type_config={},
    )
    el = FormElement.objects.create(
        version=version, slug="content", element_type=FormElement.ElementType.FIELD,
        sort_order=0, is_preview=False, type_config={},
    )
    FormElementBinding.objects.create(form_element=el, data_field=field, role="")
    FormElementTranslation.objects.create(element=el, language="en", label="Content")

    if required:
        RequiredRule.objects.create(field=field, applies_to_save=False)
    if max_length:
        MaxLengthRule.objects.create(field=field, applies_to_save=True, max_length=max_length)

    return config, version, field, lang


def make_full_workflow():
    """
    Create a WorkflowDefinition with a published WorkflowVersion containing
    draft→submitted states and a submit transition.

    Returns: (workflow_version, draft_state, submitted_state, submit_transition)
    """
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )

    wf_def = WorkflowDefinition.objects.create(name="Test Workflow")
    version = WorkflowVersion.objects.create(workflow=wf_def, status=WorkflowVersion.Status.PUBLISHED)
    draft_state = WorkflowState.objects.create(version=version, name="draft", is_initial=True)
    WorkflowStateTranslation.objects.create(state=draft_state, language="en", label="Draft")
    submitted = WorkflowState.objects.create(version=version, name="submitted", is_initial=False)
    WorkflowStateTranslation.objects.create(state=submitted, language="en", label="Submitted")

    trans = WorkflowTransition.objects.create(
        version=version, name="submit", from_state=draft_state, to_state=submitted
    )
    WorkflowTransitionTranslation.objects.create(transition=trans, language="en", label="Submit")

    return version, draft_state, submitted, trans


def add_workflow_field(version, workflow_version, slug="status"):
    """
    Add a WORKFLOW data field + 'field' FormElement to a config version, linked
    to the given workflow version.

    Returns the DataField.
    """
    from userdefinedmodel.models import DataField, FormElement, FormElementTranslation, FormElementBinding

    field = DataField.objects.create(
        version=version,
        slug=slug,
        data_type="workflow",
        workflow_version=workflow_version,
    )
    el = FormElement.objects.create(
        version=version, slug=slug, element_type=FormElement.ElementType.FIELD,
        sort_order=999, is_preview=False, type_config={},
    )
    FormElementBinding.objects.create(form_element=el, data_field=field, role="")
    FormElementTranslation.objects.create(element=el, language="en", label="Status")
    return field


def make_entity_with_type(policy_source=ALLOW_ALL_POLICY):
    """
    Create a complete entity with UDMType, published config, and a policy.

    Defaults to an allow-all policy so the default-deny engine permits operations.
    Pass an explicit policy_source for custom rules, or policy_source=None to leave
    the type policy-less (deny everything).

    Returns: (entity, udm_type, version, config)
    """
    from userdefinedmodel.models import (
        FieldConfig, ConfigLanguage, ConfigVersion, DataField,
        FormElement, FormElementTranslation, FormElementBinding,
        UserDefinedModelType, UserDefinedModelEntity,
        Policy, UserDefinedModelTypePolicy,
    )

    config = FieldConfig.objects.create(name="Entity Config")
    ConfigLanguage.objects.create(config=config, code="en", label="English", is_default=True)
    version = ConfigVersion.objects.create(config=config, status="published")
    field = DataField.objects.create(version=version, slug="title", data_type="text_short")
    el = FormElement.objects.create(
        version=version, slug="title", element_type=FormElement.ElementType.FIELD,
        sort_order=0, is_preview=False, type_config={},
    )
    FormElementBinding.objects.create(form_element=el, data_field=field, role="")
    FormElementTranslation.objects.create(element=el, language="en", label="Title")

    udm_type = UserDefinedModelType.objects.create(name="Test Type", field_config=config)

    entity = UserDefinedModelEntity.objects.create(
        config_version=version, user_defined_model_type=udm_type,
    )

    if policy_source:
        policy = Policy.objects.create(slug=f"policy-{entity.id}", source=policy_source)
        UserDefinedModelTypePolicy.objects.create(
            user_defined_model_type=udm_type, policy=policy, sort_order=0
        )

    return entity, udm_type, version, config


# ─── Rego policy fixtures ─────────────────────────────────────────────────────

REGO_ALLOW_ALL = ALLOW_ALL_POLICY

REGO_DENY_ALL = wrap_policy("""
package udm
import rego.v1
allow := false
""")

REGO_STAFF_ONLY = wrap_policy("""
package udm
import rego.v1

allow if {
    input.user.is_staff
}
""")

REGO_OWNER_EDIT = wrap_policy("""
package udm
import rego.v1

allow if {
    input.action in {"view", "browse"}
}

allow if {
    input.action in {"edit", "save", "create", "delete"}
    input.user.is_staff
}
""")

REGO_BLOCK_SUBMIT_IF_TITLE_EMPTY = wrap_policy("""
package udm
import rego.v1

_submit_blocked if {
    input.action == "transition"
    input.transition == "submit"
    input.field == "status"
    not input.entity.fields.title.value
}

allow if { not _submit_blocked }

messages contains msg if {
    _submit_blocked
    msg := {
        "level": "error",
        "text": "Title is required for submission",
        "field_slug": "title",
    }
}
""")
