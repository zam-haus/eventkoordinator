from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html
from polymorphic.admin import (
    PolymorphicInlineSupportMixin,
    StackedPolymorphicInline,
    PolymorphicParentModelAdmin,
    PolymorphicChildModelAdmin,
)

from userdefinedmodel.models import (
    FieldConfig,
    ConfigLanguage,
    ConfigVersion,
    FieldDefinition,
    FieldDefinitionTranslation,
    FieldDefaultValue,
    SlugIdSequence,
    WorkflowDefinition,
    WorkflowVersion,
    WorkflowState,
    WorkflowStateTranslation,
    WorkflowTransition,
    WorkflowTransitionTranslation,
    Policy,
    UserDefinedModelTypePolicy,
    UserDefinedModelType,
    SingleFieldValidationRule,
    RequiredRule,
    MinLengthRule,
    MaxLengthRule,
    RegexRule,
    MinValueRule,
    MaxValueRule,
    MinItemsRule,
    MaxItemsRule,
    MaxFileSizeRule,
    AllowedMimeTypesRule,
    AllowedMimeTypeEntry,
    RequiredInLanguageRule,
    MultiFieldValidationRule,
    MultiFieldRuleAssociation,
    AtLeastOneRequiredRule,
    ExactlyOneRequiredRule,
    MutualExclusionRule,
    UserDefinedModelEntityNode,
    UserDefinedModelEntity,
    SubmodelInstance,
    FieldValue,
    FileAttachment,
    StagingFile,
    EditGroup,
    FieldEdit,
    UserDefinedModelEntityMigration,
    MigrationFieldMapping,
    BulkMigrationPlan,
    BulkMigrationFieldMapping,
    BulkMigrationSubmodelMapping,
    BulkMigrationSubmodelFieldMapping,
    BulkMigrationWorkflowStateMapping,
)


# ─── Config ──────────────────────────────────────────────────────────────────

class ConfigLanguageInline(admin.TabularInline):
    model = ConfigLanguage
    extra = 0
    fields = ("code", "label", "is_default", "sort_order")


class ConfigVersionInline(admin.TabularInline):
    model = ConfigVersion
    extra = 0
    fields = ("status", "notes", "published_at", "created_at")
    readonly_fields = ("published_at", "created_at")
    show_change_link = True


@admin.register(FieldConfig)
class FieldConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "udm_type_count", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at", "referenced_by_udm_types")
    inlines = [ConfigLanguageInline, ConfigVersionInline]

    @admin.display(description="Referenced by UDM types")
    def referenced_by_udm_types(self, obj):
        types = obj.user_defined_model_types.all()
        if not types:
            return "—"
        return ", ".join(str(t) for t in types)

    @admin.display(description="# UDM types")
    def udm_type_count(self, obj):
        return obj.user_defined_model_types.count()


class FieldDefinitionTranslationInline(admin.TabularInline):
    model = FieldDefinitionTranslation
    extra = 0
    fields = ("language", "label", "help_text")


class FieldDefaultValueInline(admin.TabularInline):
    model = FieldDefaultValue
    extra = 0
    fields = (
        "language",
        "value_text", "value_decimal", "value_bool", "value_date",
        "value_time", "value_datetime", "value_json",
    )


class SingleFieldRuleInline(StackedPolymorphicInline):
    model = SingleFieldValidationRule

    class RequiredRuleInline(StackedPolymorphicInline.Child):
        model = RequiredRule
        fields = ("applies_to_save", "admin_label")

    class MinLengthRuleInline(StackedPolymorphicInline.Child):
        model = MinLengthRule
        fields = ("min_length", "applies_to_save", "admin_label")

    class MaxLengthRuleInline(StackedPolymorphicInline.Child):
        model = MaxLengthRule
        fields = ("max_length", "applies_to_save", "admin_label")

    class RegexRuleInline(StackedPolymorphicInline.Child):
        model = RegexRule
        fields = ("pattern", "failure_message", "applies_to_save", "admin_label")

    class MinValueRuleInline(StackedPolymorphicInline.Child):
        model = MinValueRule
        fields = ("min_value", "applies_to_save", "admin_label")

    class MaxValueRuleInline(StackedPolymorphicInline.Child):
        model = MaxValueRule
        fields = ("max_value", "applies_to_save", "admin_label")

    class MinItemsRuleInline(StackedPolymorphicInline.Child):
        model = MinItemsRule
        fields = ("min_items", "applies_to_save", "admin_label")

    class MaxItemsRuleInline(StackedPolymorphicInline.Child):
        model = MaxItemsRule
        fields = ("max_items", "applies_to_save", "admin_label")

    class MaxFileSizeRuleInline(StackedPolymorphicInline.Child):
        model = MaxFileSizeRule
        fields = ("max_bytes", "applies_to_save", "admin_label")

    class AllowedMimeTypesRuleInline(StackedPolymorphicInline.Child):
        model = AllowedMimeTypesRule
        fields = ("applies_to_save", "admin_label")

    class RequiredInLanguageRuleInline(StackedPolymorphicInline.Child):
        model = RequiredInLanguageRule
        fields = ("language", "applies_to_save", "admin_label")

    child_inlines = [
        RequiredRuleInline,
        MinLengthRuleInline,
        MaxLengthRuleInline,
        RegexRuleInline,
        MinValueRuleInline,
        MaxValueRuleInline,
        MinItemsRuleInline,
        MaxItemsRuleInline,
        MaxFileSizeRuleInline,
        AllowedMimeTypesRuleInline,
        RequiredInLanguageRuleInline,
    ]


class FieldDefinitionInline(admin.TabularInline):
    model = FieldDefinition
    fk_name = "version"
    extra = 0
    fields = ("slug", "data_type", "parent_slug", "sort_order", "is_localized", "is_preview")
    show_change_link = True


class MultiFieldRuleAssociationInline(admin.TabularInline):
    model = MultiFieldRuleAssociation
    extra = 0
    fields = ("field",)


class MultiFieldRuleInline(StackedPolymorphicInline):
    model = MultiFieldValidationRule

    class AtLeastOneRequiredRuleInline(StackedPolymorphicInline.Child):
        model = AtLeastOneRequiredRule
        fields = ("applies_to_save", "admin_label")

    class ExactlyOneRequiredRuleInline(StackedPolymorphicInline.Child):
        model = ExactlyOneRequiredRule
        fields = ("applies_to_save", "admin_label")

    class MutualExclusionRuleInline(StackedPolymorphicInline.Child):
        model = MutualExclusionRule
        fields = ("applies_to_save", "admin_label")

    child_inlines = [
        AtLeastOneRequiredRuleInline,
        ExactlyOneRequiredRuleInline,
        MutualExclusionRuleInline,
    ]


@admin.register(ConfigVersion)
class ConfigVersionAdmin(PolymorphicInlineSupportMixin, admin.ModelAdmin):
    list_display = ("__str__", "config", "status", "entity_count", "published_at", "created_at")
    list_filter = ("status", "config")
    search_fields = ("config__name", "notes")
    readonly_fields = ("published_at", "created_at", "updated_at", "used_as_submodel_in", "entity_node_count")
    inlines = [FieldDefinitionInline, MultiFieldRuleInline]

    @admin.display(description="Used as submodel in fields")
    def used_as_submodel_in(self, obj):
        fields = obj.used_as_submodel.select_related("version__config").all()
        if not fields:
            return "—"
        return ", ".join(f"{f.version.config.name} / {f.slug}" for f in fields)

    @admin.display(description="Entity nodes")
    def entity_node_count(self, obj):
        return obj.nodes.count()

    @admin.display(description="# entities")
    def entity_count(self, obj):
        return obj.nodes.count()

    @admin.action(description="Publish selected draft versions")
    def publish_versions(self, request, queryset):
        for version in queryset.filter(status=ConfigVersion.Status.DRAFT):
            try:
                version.publish()
                self.message_user(request, f"Published {version}", level="success")
            except Exception as e:
                self.message_user(request, f"Failed to publish {version}: {e}", level="error")

    actions = ["publish_versions"]


@admin.register(FieldDefinition)
class FieldDefinitionAdmin(PolymorphicInlineSupportMixin, admin.ModelAdmin):
    list_display = ("slug", "data_type", "version", "sort_order", "is_localized", "is_preview")
    list_filter = ("data_type", "is_localized", "is_preview", "version__config")
    search_fields = ("slug", "version__config__name")
    readonly_fields = ("created_at", "updated_at", "referenced_by_child_nodes", "referenced_by_field_values")
    inlines = [FieldDefinitionTranslationInline, FieldDefaultValueInline, SingleFieldRuleInline]

    @admin.display(description="Child nodes using this field")
    def referenced_by_child_nodes(self, obj):
        count = obj.child_nodes.count()
        return count or "—"

    @admin.display(description="Field values using this definition")
    def referenced_by_field_values(self, obj):
        count = obj.values.count()
        return count or "—"


@admin.register(SlugIdSequence)
class SlugIdSequenceAdmin(admin.ModelAdmin):
    list_display = ("prefix", "next_value", "owner_config")
    search_fields = ("prefix",)


# ─── Workflow ────────────────────────────────────────────────────────────────

class WorkflowStateTranslationInline(admin.TabularInline):
    model = WorkflowStateTranslation
    extra = 0
    fields = ("language", "label")


class WorkflowStateInline(admin.TabularInline):
    model = WorkflowState
    extra = 0
    fields = ("name", "is_initial", "background_color", "position_x", "position_y")
    show_change_link = True


class WorkflowTransitionTranslationInline(admin.TabularInline):
    model = WorkflowTransitionTranslation
    extra = 0
    fields = ("language", "label")


class WorkflowTransitionInline(admin.TabularInline):
    model = WorkflowTransition
    extra = 0
    fields = ("name", "from_state", "from_undefined_only", "to_state", "source_handle", "target_handle")
    show_change_link = True


class WorkflowVersionInline(admin.TabularInline):
    model = WorkflowVersion
    extra = 0
    fields = ("status", "notes", "published_at", "created_at")
    readonly_fields = ("published_at", "created_at")
    show_change_link = True


@admin.register(WorkflowDefinition)
class WorkflowDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name",)
    inlines = [WorkflowVersionInline]



@admin.register(WorkflowVersion)
class WorkflowVersionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "workflow", "status", "field_definition_count", "published_at", "created_at")
    list_filter = ("status", "workflow")
    search_fields = ("workflow__name", "notes")
    readonly_fields = ("published_at", "created_at", "updated_at", "referenced_by_field_definitions")
    inlines = [WorkflowStateInline, WorkflowTransitionInline]

    @admin.display(description="Used in field definitions")
    def referenced_by_field_definitions(self, obj):
        fields = obj.field_definitions.select_related("version__config").all()
        if not fields:
            return "—"
        return ", ".join(f"{f.version.config.name} / {f.slug}" for f in fields)

    @admin.display(description="# field definitions")
    def field_definition_count(self, obj):
        return obj.field_definitions.count()

    @admin.action(description="Publish selected draft workflow versions")
    def publish_versions(self, request, queryset):
        for version in queryset.filter(status=WorkflowVersion.Status.DRAFT):
            try:
                version.publish()
                self.message_user(request, f"Published {version}", level="success")
            except Exception as e:
                self.message_user(request, f"Failed to publish {version}: {e}", level="error")

    actions = ["publish_versions"]


@admin.register(WorkflowState)
class WorkflowStateAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "is_initial", "background_color")
    list_filter = ("is_initial", "version__workflow")
    search_fields = ("name", "version__workflow__name")
    inlines = [WorkflowStateTranslationInline]


@admin.register(WorkflowTransition)
class WorkflowTransitionAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "from_state", "to_state")
    list_filter = ("version__workflow",)
    search_fields = ("name", "version__workflow__name")
    inlines = [WorkflowTransitionTranslationInline]


# ─── Policy ──────────────────────────────────────────────────────────────────

@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ("slug", "udm_type_count", "created_at")
    search_fields = ("slug", "source")
    readonly_fields = ("created_at", "updated_at", "referenced_by_udm_types")

    @admin.display(description="Referenced by UDM types")
    def referenced_by_udm_types(self, obj):
        types = obj.user_defined_model_types.all()
        if not types:
            return "—"
        return ", ".join(str(t) for t in types)

    @admin.display(description="# UDM types")
    def udm_type_count(self, obj):
        return obj.user_defined_model_types.count()


# ─── UDM Type ────────────────────────────────────────────────────────────────

class UserDefinedModelTypePolicyInline(admin.TabularInline):
    model = UserDefinedModelTypePolicy
    extra = 0
    fields = ("policy", "sort_order")


@admin.register(UserDefinedModelType)
class UserDefinedModelTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "label", "field_config", "created_at")
    search_fields = ("name", "label")
    list_filter = ("field_config",)
    inlines = [UserDefinedModelTypePolicyInline]


# ─── Entities ────────────────────────────────────────────────────────────────

# (form field name, model attribute holding the raw value)
_VALUE_COLUMNS = (
    ("value_text", "value_text"),
    ("value_decimal", "value_decimal"),
    ("value_bool", "value_bool"),
    ("value_date", "value_date"),
    ("value_time", "value_time"),
    ("value_datetime", "value_datetime"),
    ("value_json", "value_json"),
    ("value_user", "value_user_id"),
    ("value_group", "value_group_id"),
    ("value_node", "value_node_id"),
    ("value_file", "value_file_id"),
    ("value_workflow_state", "value_workflow_state_id"),
)


def _field_value_mismatches(fv):
    """Return (correct_column, [wrongly populated columns]) for a FieldValue."""
    expected = FieldValue._DATA_TYPE_COLUMN.get(fv.field.data_type)
    wrong = [
        name for name, attr in _VALUE_COLUMNS
        if name != expected and getattr(fv, attr) is not None
    ]
    return expected, wrong


class FieldValueInlineForm(forms.ModelForm):
    class Meta:
        model = FieldValue
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance")
        if instance is None or instance.pk is None or instance.field_id is None:
            return  # new row: field/data_type unknown yet, show everything
        expected, wrong = _field_value_mismatches(instance)
        for name, _attr in _VALUE_COLUMNS:
            if name not in self.fields:
                continue
            if name == expected:
                continue
            if name in wrong:
                self.fields[name].help_text = format_html(
                    '<span style="color:#ba2121;font-weight:bold;">'
                    "⚠ Wrong column for data type “{}” (expected “{}”)</span>",
                    instance.field.data_type, expected or "—",
                )
            else:
                # Empty and irrelevant for this data type: keep the cell so
                # tabular column headers stay aligned, but grey it out. A real
                # HiddenInput (is_hidden=True) would be pulled out of its <td>.
                self.fields[name].disabled = True
                self.fields[name].widget = forms.TextInput(
                    attrs={
                        "style": (
                            "background:var(--darkened-bg,#f3f3f3);"
                            "color:var(--body-quiet-color,#999);"
                            "border-color:var(--border-color,#ddd);"
                            "pointer-events:none;opacity:.5;"
                        ),
                        "tabindex": "-1",
                        "placeholder": "n/a",
                    }
                )


class FieldValueInline(admin.TabularInline):
    model = FieldValue
    form = FieldValueInlineForm
    fk_name = "node"
    extra = 0
    fields = (
        "field", "language",
        "value_text", "value_decimal", "value_bool", "value_date",
        "value_time", "value_datetime", "value_json",
        "value_user", "value_group", "value_node", "value_file", "value_workflow_state",
    )
    raw_id_fields = (
        "field",
        "value_user", "value_group", "value_node", "value_file", "value_workflow_state",
    )
    show_change_link = True


class FieldValueWarningMixin:
    """Warn the admin when a FieldValue stores data in a column that does not
    match its FieldDefinition's data_type."""

    def change_view(self, request, object_id, form_url="", extra_context=None):
        values = (
            FieldValue.objects.filter(node_id=object_id)
            .select_related("field")
        )
        for fv in values:
            expected, wrong = _field_value_mismatches(fv)
            if wrong:
                messages.warning(
                    request,
                    f"Field value #{fv.pk} ({fv.field.slug}, data type "
                    f"“{fv.field.data_type}”) has data in unexpected column(s): "
                    f"{', '.join(wrong)}. Expected column: {expected or '— (none)'}.",
                )
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(UserDefinedModelEntity)
class UserDefinedModelEntityAdmin(FieldValueWarningMixin, admin.ModelAdmin):
    list_display = ("id", "user_defined_model_type", "config_version", "created_at")
    list_filter = ("user_defined_model_type", "config_version__config")
    search_fields = ("id",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("user_defined_model_type", "config_version")
    inlines = [FieldValueInline]


@admin.register(SubmodelInstance)
class SubmodelInstanceAdmin(FieldValueWarningMixin, admin.ModelAdmin):
    list_display = ("id", "parent_node", "parent_field", "sort_order", "created_at")
    list_filter = ("config_version__config",)
    search_fields = ("id",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("parent_node", "parent_field", "config_version")
    inlines = [FieldValueInline]


@admin.register(FileAttachment)
class FileAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "mime_type", "size_bytes", "deleted_at", "created_at")
    list_filter = ("mime_type", "deleted_at")
    search_fields = ("original_name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(StagingFile)
class StagingFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "uploader", "mime_type", "size_bytes", "expires_at", "created_at")
    list_filter = ("mime_type",)
    search_fields = ("original_name",)
    readonly_fields = ("created_at", "updated_at")


# ─── History ─────────────────────────────────────────────────────────────────

class FieldEditInline(admin.TabularInline):
    model = FieldEdit
    extra = 0
    fields = ("change_kind", "field", "language", "old_value", "new_value")
    readonly_fields = ("change_kind", "field", "language", "old_value", "new_value")


@admin.register(EditGroup)
class EditGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "node", "root_entity", "saved_by", "saved_at")
    list_filter = ("saved_at",)
    search_fields = ("saved_by__email",)
    readonly_fields = ("id", "created_at", "updated_at", "saved_at")
    inlines = [FieldEditInline]


# ─── Migration ───────────────────────────────────────────────────────────────

class MigrationFieldMappingInline(admin.TabularInline):
    model = MigrationFieldMapping
    extra = 0
    fields = ("source_field", "action", "target_field")


@admin.register(UserDefinedModelEntityMigration)
class UserDefinedModelEntityMigrationAdmin(admin.ModelAdmin):
    list_display = ("id", "user_defined_model_entity", "source_version", "target_version", "executed_at")
    list_filter = ("executed_at",)
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [MigrationFieldMappingInline]


class BulkMigrationFieldMappingInline(admin.TabularInline):
    model = BulkMigrationFieldMapping
    extra = 0
    fields = ("source_field", "action", "target_field")


class BulkMigrationSubmodelMappingInline(admin.TabularInline):
    model = BulkMigrationSubmodelMapping
    extra = 0
    fields = ("source_parent_field", "target_submodel_version")
    show_change_link = True


class BulkMigrationWorkflowStateMappingInline(admin.TabularInline):
    model = BulkMigrationWorkflowStateMapping
    extra = 0
    fields = ("field_slug", "from_state", "to_state")


@admin.register(BulkMigrationPlan)
class BulkMigrationPlanAdmin(admin.ModelAdmin):
    list_display = (
        "id", "source_version", "target_version", "status",
        "total_entities", "done_entities", "failed_entities", "executed_at",
    )
    list_filter = ("status",)
    search_fields = ("source_version__config__name", "target_version__config__name")
    readonly_fields = ("id", "created_at", "updated_at", "executed_at", "total_entities", "done_entities", "failed_entities")
    inlines = [
        BulkMigrationFieldMappingInline,
        BulkMigrationSubmodelMappingInline,
        BulkMigrationWorkflowStateMappingInline,
    ]


@admin.register(BulkMigrationSubmodelMapping)
class BulkMigrationSubmodelMappingAdmin(admin.ModelAdmin):
    list_display = ("id", "plan", "source_parent_field", "target_submodel_version")
    readonly_fields = ("id", "created_at", "updated_at")

    class BulkMigrationSubmodelFieldMappingInline(admin.TabularInline):
        model = BulkMigrationSubmodelFieldMapping
        extra = 0
        fields = ("source_field", "action", "target_field")

    inlines = [BulkMigrationSubmodelFieldMappingInline]
