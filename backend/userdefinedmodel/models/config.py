import os
import uuid
from collections import defaultdict

from django.db import models, transaction
from django.db.models import Q, UniqueConstraint
from django.utils.timezone import now

from userdefinedmodel.basemodels import MetaBase


class FieldConfig(MetaBase):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class ConfigLanguage(MetaBase):
    config = models.ForeignKey(FieldConfig, on_delete=models.CASCADE, related_name="languages")
    code = models.CharField(max_length=10)
    label = models.CharField(max_length=100)
    is_default = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            UniqueConstraint(fields=["config", "code"], name="unique_language_per_config"),
            UniqueConstraint(
                fields=["config"],
                condition=Q(is_default=True),
                name="one_default_language_per_config",
            ),
        ]

    def __str__(self):
        return f"{self.config} / {self.code}"


class ConfigVersion(MetaBase):
    class Status(models.TextChoices):
        DRAFT = "draft"
        PUBLISHED = "published"
        ARCHIVED = "archived"

    config = models.ForeignKey(FieldConfig, on_delete=models.CASCADE, related_name="versions")
    status = models.CharField(max_length=10, choices=Status, default=Status.DRAFT)
    published_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["config"],
                condition=Q(status="draft"),
                name="unique_draft_per_config",
            ),
            UniqueConstraint(
                fields=["config"],
                condition=Q(status="published"),
                name="unique_published_per_config",
            ),
        ]

    def __str__(self):
        return f"{self.config} v{self.pk} ({self.status})"

    def publish(self):
        from userdefinedmodel.models.node import UserDefinedModelEntityNode
        from userdefinedmodel.models.migration import BulkMigrationPlan

        with transaction.atomic():
            # Validate default combination before publishing
            self._validate_defaults_for_publish()

            # Archive the current published version
            ConfigVersion.objects.filter(
                config=self.config, status=self.Status.PUBLISHED
            ).update(status=self.Status.ARCHIVED)

            self.status = self.Status.PUBLISHED
            self.published_at = now()
            self.save()

            # Auto-create new DRAFT as deep copy of this published version
            new_draft = self._create_draft_copy()

            # Auto-create BulkMigrationPlan stubs for stale entities
            stale_versions = (
                UserDefinedModelEntityNode.objects.filter(
                    userdefinedmodelentity__isnull=False,
                    config_version__config=self.config,
                )
                .exclude(config_version=self)
                .values_list("config_version_id", flat=True)
                .distinct()
            )
            for old_version_id in stale_versions:
                BulkMigrationPlan.objects.get_or_create(
                    source_version_id=old_version_id,
                    target_version=self,
                    user_defined_model_type_filter=None,
                    defaults={"status": BulkMigrationPlan.Status.DRAFT},
                )

        return new_draft

    def _validate_defaults_for_publish(self):
        from django.core.exceptions import ValidationError
        from userdefinedmodel.models.config import FieldDefaultValue

        errors = defaultdict(list)
        fields = list(self.field_definitions.prefetch_related(
            "single_field_rules", "defaults"
        ))
        field_map = {f.slug: f for f in fields}

        # Build transient field-value dict from defaults
        field_values = {}
        for field in fields:
            defaults = list(field.defaults.all())
            if defaults:
                field_values[field.slug] = {d.language: d.get_value() for d in defaults} if field.is_localized else defaults[0].get_value()
            else:
                field_values[field.slug] = None

        # Run save-time single field rules against defaults
        from userdefinedmodel.models.rules import SingleFieldValidationRule
        single_rules = SingleFieldValidationRule.objects.filter(
            field__version=self, applies_to_save=True
        ).select_related("field")
        for rule in single_rules:
            if rule.field.is_localized:
                lang_values = field_values.get(rule.field.slug) or {}
                if isinstance(lang_values, dict):
                    for lang, val in lang_values.items():
                        for msg in rule.get_real_instance().validate(val):
                            errors[f"{rule.field.slug}[{lang}]"].append(msg)
            else:
                val = field_values.get(rule.field.slug)
                for msg in rule.get_real_instance().validate(val):
                    errors[rule.field.slug].append(msg)

        # Run save-time multi-field rules
        from userdefinedmodel.models.rules import MultiFieldValidationRule
        multi_rules = MultiFieldValidationRule.objects.filter(
            config_version=self, applies_to_save=True
        ).prefetch_related("associations__field")
        for rule in multi_rules:
            rule_fv = {
                a.field.slug: field_values.get(a.field.slug)
                for a in rule.associations.all()
            }
            msg = rule.get_real_instance().validate(rule_fv)
            if msg:
                for slug in rule_fv:
                    errors[slug].append(msg)

        if errors:
            raise ValidationError(dict(errors))

    def _create_draft_copy(self):
        new_draft = ConfigVersion.objects.create(
            config=self.config,
            status=ConfigVersion.Status.DRAFT,
            notes="",
        )
        field_map = {}  # old field id → new field
        for old_field in self.field_definitions.all():
            new_field = FieldDefinition.objects.create(
                version=new_draft,
                slug=old_field.slug,
                data_type=old_field.data_type,
                sort_order=old_field.sort_order,
                is_localized=old_field.is_localized,
                is_preview=old_field.is_preview,
                parent_slug=old_field.parent_slug,
                submodel_config=old_field.submodel_config,
                workflow_version=old_field.workflow_version,
                type_config=old_field.type_config,
            )
            field_map[old_field.pk] = new_field
            # Copy translations
            for t in old_field.translations.all():
                FieldDefinitionTranslation.objects.create(
                    field=new_field, language=t.language, label=t.label, help_text=t.help_text
                )
            # Copy defaults
            for d in old_field.defaults.all():
                from userdefinedmodel.models.config import FieldDefaultValue
                FieldDefaultValue.objects.create(
                    field=new_field,
                    language=d.language,
                    value_text=d.value_text,
                    value_decimal=d.value_decimal,
                    value_bool=d.value_bool,
                    value_date=d.value_date,
                    value_time=d.value_time,
                    value_datetime=d.value_datetime,
                    value_json=d.value_json,
                    value_user=d.value_user,
                    value_group=d.value_group,
                )

        # Copy single-field rules
        from userdefinedmodel.models.rules import SingleFieldValidationRule
        for old_rule in SingleFieldValidationRule.objects.filter(field__version=self):
            real = old_rule.get_real_instance()
            new_field = field_map.get(old_rule.field_id)
            if new_field:
                real.clone_to(new_field).save()

        # Copy multi-field rules
        from userdefinedmodel.models.rules import MultiFieldValidationRule, MultiFieldRuleAssociation
        for old_rule in MultiFieldValidationRule.objects.filter(config_version=self):
            real = old_rule.get_real_instance()
            real.pk = None
            real.id = None
            real.config_version = new_draft
            real.save()
            for assoc in old_rule.associations.all():
                new_field = field_map.get(assoc.field_id)
                if new_field:
                    MultiFieldRuleAssociation.objects.create(rule=real, field=new_field)

        return new_draft


class SlugIdSequence(MetaBase):
    """Global counter per SLUG_ID prefix. Survives config version copies."""
    prefix = models.CharField(max_length=200, unique=True)
    next_value = models.PositiveIntegerField(default=1)
    owner_config = models.ForeignKey(
        FieldConfig,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="slug_sequences",
    )

    def __str__(self):
        return f"{self.prefix} (next={self.next_value})"


class FieldDefinition(MetaBase):
    class DataType(models.TextChoices):
        TEXT_SHORT = "text_short"
        TEXT_LONG = "text_long"
        TEXT_MARKDOWN = "text_markdown"
        TEXT_RICHTEXT = "text_richtext"
        INTEGER = "integer"
        FLOAT = "float"
        BOOLEAN = "boolean"
        DATE = "date"
        TIME = "time"
        DATETIME = "datetime"
        SELECT_SINGLE = "select_single"
        SELECT_MULTI = "select_multi"
        IMAGE = "image"
        FILE = "file"
        USER_SELECT = "user_select"
        USER_SELECT_MULTI = "user_select_multi"
        GROUP_SELECT = "group_select"
        GROUP_SELECT_MULTI = "group_select_multi"
        SUBMODEL_SELECT = "submodel_select"
        SUBMODEL_LIST = "submodel_list"
        ENTITY_SELECT = "entity_select"
        ENTITY_SELECT_MULTI = "entity_select_multi"
        SLUG_ID = "slug_id"
        WORKFLOW = "workflow"
        # Structural / layout types (no data value)
        TAB_CONTAINER = "tab_container"
        TAB = "tab"
        SAVE_BUTTON = "save_button"
        HSTACK = "hstack"
        HSTACK_GROUP = "hstack_group"
        TAB_PREV = "tab_prev"
        TAB_NEXT = "tab_next"

    STRUCTURAL_TYPES = frozenset({
        DataType.TAB_CONTAINER,
        DataType.TAB,
        DataType.SAVE_BUTTON,
        DataType.HSTACK,
        DataType.HSTACK_GROUP,
        DataType.TAB_PREV,
        DataType.TAB_NEXT,
    })

    version = models.ForeignKey(ConfigVersion, on_delete=models.CASCADE, related_name="field_definitions")
    slug = models.SlugField(max_length=80)
    data_type = models.CharField(max_length=30, choices=DataType)
    parent_slug = models.SlugField(max_length=80, blank=True, default="")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_localized = models.BooleanField(default=False)
    is_preview = models.BooleanField(default=False)
    submodel_config = models.ForeignKey(
        ConfigVersion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="used_as_submodel",
    )
    workflow_version = models.ForeignKey(
        "userdefinedmodel.WorkflowVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="field_definitions",
    )
    type_config = models.JSONField(default=dict)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            UniqueConstraint(fields=["version", "slug"], name="unique_slug_in_version"),
        ]

    def __str__(self):
        return f"{self.version} / {self.slug}"


class FieldDefinitionTranslation(MetaBase):
    field = models.ForeignKey(FieldDefinition, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=10)
    label = models.CharField(max_length=200)
    help_text = models.TextField(blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["field", "language"],
                name="unique_label_translation_per_field_language",
            )
        ]

    def __str__(self):
        return f"{self.field} [{self.language}]"


class TypedValue(models.Model):
    value_text = models.TextField(null=True, blank=True)
    value_decimal = models.DecimalField(max_digits=30, decimal_places=10, null=True, blank=True)
    value_bool = models.BooleanField(null=True, blank=True)
    value_date = models.DateField(null=True, blank=True)
    value_time = models.TimeField(null=True, blank=True)
    value_datetime = models.DateTimeField(null=True, blank=True)
    value_json = models.JSONField(null=True, blank=True)
    value_user = models.ForeignKey(
        "openid_user_management.OpenIDUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    value_group = models.ForeignKey(
        "auth.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    value_node = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntityNode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    value_file = models.ForeignKey(
        "userdefinedmodel.FileAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(class)s_set",
    )
    value_workflow_state = models.ForeignKey(
        "userdefinedmodel.WorkflowState",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True

    # Which column stores the value for each data_type
    _DATA_TYPE_COLUMN = {
        FieldDefinition.DataType.TEXT_SHORT: "value_text",
        FieldDefinition.DataType.TEXT_LONG: "value_text",
        FieldDefinition.DataType.TEXT_MARKDOWN: "value_text",
        FieldDefinition.DataType.TEXT_RICHTEXT: "value_text",
        FieldDefinition.DataType.SELECT_SINGLE: "value_text",
        FieldDefinition.DataType.INTEGER: "value_decimal",
        FieldDefinition.DataType.FLOAT: "value_decimal",
        FieldDefinition.DataType.SLUG_ID: "value_decimal",
        FieldDefinition.DataType.BOOLEAN: "value_bool",
        FieldDefinition.DataType.DATE: "value_date",
        FieldDefinition.DataType.TIME: "value_time",
        FieldDefinition.DataType.DATETIME: "value_datetime",
        FieldDefinition.DataType.SELECT_MULTI: "value_json",
        FieldDefinition.DataType.USER_SELECT_MULTI: "value_json",
        FieldDefinition.DataType.GROUP_SELECT_MULTI: "value_json",
        FieldDefinition.DataType.ENTITY_SELECT_MULTI: "value_json",
        FieldDefinition.DataType.USER_SELECT: "value_user",
        FieldDefinition.DataType.GROUP_SELECT: "value_group",
        FieldDefinition.DataType.SUBMODEL_SELECT: "value_node",
        FieldDefinition.DataType.ENTITY_SELECT: "value_node",
        FieldDefinition.DataType.IMAGE: "value_file",
        FieldDefinition.DataType.FILE: "value_file",
        FieldDefinition.DataType.WORKFLOW: "value_workflow_state",
        # SUBMODEL_LIST: no value column
    }

    def get_value(self, field: "FieldDefinition | None" = None):
        if field is None:
            field = getattr(self, "field", None)
        if field is None:
            return None
        col = self._DATA_TYPE_COLUMN.get(field.data_type)
        if col is None:
            return None
        # WORKFLOW: resolve FK to state name string for serialization
        if field.data_type == FieldDefinition.DataType.WORKFLOW:
            state = self.value_workflow_state
            return state.name if state else None
        # FK columns: return the PK directly to avoid lazy-loading ORM objects
        # which are not JSON-serialisable and cause N+1 queries.
        if col in ("value_user", "value_group", "value_node", "value_file"):
            return getattr(self, col + "_id")
        val = getattr(self, col)
        # For INTEGER and SLUG_ID fields stored as Decimal, return int
        if field.data_type in (FieldDefinition.DataType.INTEGER, FieldDefinition.DataType.SLUG_ID) and val is not None:
            return int(val)
        return val

    def set_value(self, value, field: "FieldDefinition | None" = None):
        if field is None:
            field = getattr(self, "field", None)
        if field is None:
            raise ValueError("field required for set_value")
        col = self._DATA_TYPE_COLUMN.get(field.data_type)
        if col is None:
            # SUBMODEL_LIST: no value column
            return
        # Clear all other value columns
        all_cols = [
            "value_text", "value_decimal", "value_bool", "value_date",
            "value_time", "value_datetime", "value_json",
            "value_user_id", "value_group_id", "value_node_id", "value_file_id",
            "value_workflow_state_id",
        ]
        for c in all_cols:
            if c != col and c != col + "_id":
                setattr(self, c, None)

        # Sanitise richtext
        if field.data_type == FieldDefinition.DataType.TEXT_RICHTEXT and value is not None:
            import nh3
            value = nh3.clean(value)

        # FK columns use _id suffix
        if col in ("value_user", "value_group", "value_node", "value_file", "value_workflow_state"):
            setattr(self, col + "_id", value.pk if hasattr(value, "pk") else value)
        else:
            setattr(self, col, value)

    def _clean_typed_value(self, field: "FieldDefinition"):
        from django.core.exceptions import ValidationError
        import decimal

        dt = field.data_type
        col = self._DATA_TYPE_COLUMN.get(dt)

        if dt in (FieldDefinition.DataType.SUBMODEL_LIST, FieldDefinition.DataType.WORKFLOW):
            return  # No value column to validate here; WORKFLOW state is set via transition only

        # Verify the correct column is set (or all are null)
        has_value = False
        for c in ["value_text", "value_decimal", "value_bool", "value_date",
                  "value_time", "value_datetime", "value_json",
                  "value_user_id", "value_group_id", "value_node_id", "value_file_id",
                  "value_workflow_state_id"]:
            v = getattr(self, c, None)
            if v is not None:
                if c == col or c == col + "_id":
                    has_value = True
                else:
                    raise ValidationError(
                        {field.slug: f"Unexpected value in column {c} for data_type {dt}"}
                    )

        # Type-specific validation
        val = getattr(self, col if col not in ("value_user", "value_group", "value_node", "value_file", "value_workflow_state") else col + "_id", None)

        if val is None:
            return  # null is always OK at this layer (required validation is in rules)

        if dt in (FieldDefinition.DataType.INTEGER, FieldDefinition.DataType.SLUG_ID):
            if not isinstance(val, (int, decimal.Decimal)) or (isinstance(val, decimal.Decimal) and val != val.to_integral_value()):
                raise ValidationError({field.slug: "Value must be an integer"})
            if dt == FieldDefinition.DataType.SLUG_ID and int(val) < 1:
                raise ValidationError({field.slug: "Slug ID value must be a positive integer"})
        elif dt == FieldDefinition.DataType.SELECT_SINGLE:
            choices = (field.type_config or {}).get("choices", [])
            if choices and val not in choices:
                raise ValidationError({field.slug: f"'{val}' is not a valid choice"})
        elif dt == FieldDefinition.DataType.SELECT_MULTI:
            choices = (field.type_config or {}).get("choices", [])
            if not isinstance(val, list):
                raise ValidationError({field.slug: "Value must be a list"})
            if choices:
                for item in val:
                    if item not in choices:
                        raise ValidationError({field.slug: f"'{item}' is not a valid choice"})
        elif dt in (FieldDefinition.DataType.USER_SELECT_MULTI, FieldDefinition.DataType.GROUP_SELECT_MULTI, FieldDefinition.DataType.ENTITY_SELECT_MULTI):
            if not isinstance(val, list):
                raise ValidationError({field.slug: "Value must be a list"})


class FieldDefaultValue(TypedValue, MetaBase):
    field = models.ForeignKey(FieldDefinition, on_delete=models.CASCADE, related_name="defaults")
    language = models.CharField(max_length=10, default="", blank=True)

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["field", "language"],
                name="unique_default_per_field_language",
            )
        ]

    # Types that cannot have defaults (per §2.8); SLUG_ID uses auto-generated sequence,
    # WORKFLOW initial state comes from is_initial on WorkflowState.
    _NO_DEFAULT_TYPES = frozenset([
        FieldDefinition.DataType.IMAGE,
        FieldDefinition.DataType.FILE,
        FieldDefinition.DataType.ENTITY_SELECT,
        FieldDefinition.DataType.ENTITY_SELECT_MULTI,
        FieldDefinition.DataType.SUBMODEL_SELECT,
        FieldDefinition.DataType.SUBMODEL_LIST,
        FieldDefinition.DataType.SLUG_ID,
        FieldDefinition.DataType.WORKFLOW,
        *FieldDefinition.STRUCTURAL_TYPES,
    ])

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.field_id and self.field.data_type in self._NO_DEFAULT_TYPES:
            raise ValidationError(
                {self.field.slug: f"Defaults are not supported for data_type '{self.field.data_type}'."}
            )
        self._clean_typed_value(self.field)

    def __str__(self):
        return f"Default for {self.field} [{self.language}]"
