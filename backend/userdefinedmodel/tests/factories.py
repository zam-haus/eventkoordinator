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


class WorkflowStateFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.WorkflowState"

    workflow = factory.SubFactory(WorkflowDefinitionFactory)
    name = factory.Sequence(lambda n: f"state{n}")
    is_initial = False


class WorkflowTransitionFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.WorkflowTransition"

    workflow = factory.SubFactory(WorkflowDefinitionFactory)
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
        model = "userdefinedmodel.FieldDefinition"

    version = factory.SubFactory(ConfigVersionFactory)
    slug = factory.Sequence(lambda n: f"field{n}")
    data_type = "text_short"
    sort_order = factory.Sequence(lambda n: n)
    is_localized = False
    type_config = {}


class FieldDefinitionTranslationFactory(DjangoModelFactory):
    class Meta:
        model = "userdefinedmodel.FieldDefinitionTranslation"

    field = factory.SubFactory(FieldDefinitionFactory)
    language = "en"
    label = factory.LazyAttribute(lambda obj: obj.field.slug.replace("_", " ").title())
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

# Minimal allow-all policy for tests that need auth but don't care about rules
ALLOW_ALL_POLICY = """
package udm

import rego.v1

allow := true
"""

# Staff-only edit policy
STAFF_EDIT_POLICY = """
package udm

import rego.v1

allow if {
    input.action in {"view", "browse"}
}

allow if {
    input.action in {"edit", "save", "create", "delete"}
    input.user.is_staff
}
"""


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
    overflow_data = {}


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
    Create a complete FieldConfig→published ConfigVersion→FieldDefinition set
    suitable for testing entities.

    Returns: (config, version, field_def, language)
    """
    from userdefinedmodel.models import (
        FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
        FieldDefinitionTranslation, RequiredRule, MaxLengthRule,
    )

    config = FieldConfig.objects.create(name="Test Config", description="")
    lang = ConfigLanguage.objects.create(config=config, code="en", label="English", is_default=True)

    version = ConfigVersion.objects.create(config=config, status="published")
    field = FieldDefinition.objects.create(
        version=version, slug="content", data_type=data_type,
        sort_order=0, type_config={},
    )
    FieldDefinitionTranslation.objects.create(field=field, language="en", label="Content")

    if required:
        # applies_to_save=False per spec: "save-time is permissive, never requires a field to be filled"
        # RequiredRule runs at transition time only
        RequiredRule.objects.create(field=field, applies_to_save=False)
    if max_length:
        MaxLengthRule.objects.create(field=field, applies_to_save=True, max_length=max_length)

    return config, version, field, lang


def make_full_workflow():
    """
    Create a WorkflowDefinition with draft→submitted states and a submit transition.

    Returns: (workflow, draft_state, submitted_state, submit_transition)
    """
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )

    wf = WorkflowDefinition.objects.create(name="Test Workflow")
    draft = WorkflowState.objects.create(workflow=wf, name="draft", is_initial=True)
    WorkflowStateTranslation.objects.create(state=draft, language="en", label="Draft")
    submitted = WorkflowState.objects.create(workflow=wf, name="submitted", is_initial=False)
    WorkflowStateTranslation.objects.create(state=submitted, language="en", label="Submitted")

    trans = WorkflowTransition.objects.create(
        workflow=wf, name="submit", from_state=draft, to_state=submitted
    )
    WorkflowTransitionTranslation.objects.create(transition=trans, language="en", label="Submit")

    return wf, draft, submitted, trans


def add_workflow_field(version, workflow, slug="status"):
    """
    Add a WORKFLOW field definition to a config version, linked to the given workflow.

    Returns the FieldDefinition.
    """
    from userdefinedmodel.models import FieldDefinition, FieldDefinitionTranslation

    field = FieldDefinition.objects.create(
        version=version,
        slug=slug,
        data_type="workflow",
        sort_order=999,
        workflow_definition=workflow,
    )
    FieldDefinitionTranslation.objects.create(field=field, language="en", label="Status")
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
        FieldConfig, ConfigLanguage, ConfigVersion, FieldDefinition,
        FieldDefinitionTranslation, UserDefinedModelType, UserDefinedModelEntity,
        Policy, UserDefinedModelTypePolicy,
    )

    config = FieldConfig.objects.create(name="Entity Config")
    ConfigLanguage.objects.create(config=config, code="en", label="English", is_default=True)
    version = ConfigVersion.objects.create(config=config, status="published")
    field = FieldDefinition.objects.create(version=version, slug="title", data_type="text_short", sort_order=0)
    FieldDefinitionTranslation.objects.create(field=field, language="en", label="Title")

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

REGO_DENY_ALL = """
package udm
import rego.v1
allow := false
"""

REGO_STAFF_ONLY = """
package udm
import rego.v1

allow if {
    input.user.is_staff
}
"""

REGO_OWNER_EDIT = """
package udm
import rego.v1

allow if {
    input.action in {"view", "browse"}
}

allow if {
    input.action in {"edit", "save", "create", "delete"}
    input.user.is_staff
}
"""

REGO_BLOCK_SUBMIT_IF_TITLE_EMPTY = """
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
"""
