---
type: concepts_documentation
title: Publishing System
description: Comprehensive documentation for the config and workflow publishing system
---

# Publishing System

This document provides comprehensive documentation for the publishing system that manages the lifecycle of configuration versions and workflow versions. It covers the validation, archiving, and versioning mechanisms that ensure data consistency and prevent conflicts during publishing operations.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Config API](../api/configs.md#publish-endpoint) - Config publish endpoint
- [Workflow API](../api/workflows.md#publish-endpoint) - Workflow publish endpoint

## Overview

The publishing system is responsible for transitioning configurations and workflows from draft to published state while maintaining data integrity and version history. Each config and workflow can have exactly one draft and one published version at any time, with automatic archiving of previous published versions.

## Key Concepts

### Version Statuses

Both `ConfigVersion` and `WorkflowVersion` models support three statuses:

- **`draft`** - Current working version that can be edited
- **`published`** - Active version used by entities and exposed to users
- **`archived`** - Historical version kept for reference and migration

### Unique Constraints

Database-level constraints ensure exactly one draft and one published version per config/workflow:

```python
# ConfigVersion constraints
UniqueConstraint(
    fields=["config"],
    condition=Q(status="draft"),
    name="unique_draft_per_config",
),
UniqueConstraint(
    fields=["config"],
    condition=Q(status="published"),
    name="unique_published_per_config",
)

# WorkflowVersion constraints
UniqueConstraint(
    fields=["workflow"],
    condition=Q(status="draft"),
    name="unique_draft_per_workflow",
),
UniqueConstraint(
    fields=["workflow"],
    condition=Q(status="published"),
    name="unique_published_per_workflow",
)
```

## Config Version Publishing (`ConfigVersion.publish`)

The `publish()` method on `ConfigVersion` handles the transition from draft to published state.

### Process Flow

1. **Validation Phase**
   - Validate default values against single-field rules
   - Validate default values against multi-field rules
   - Validate that all submodel fields have a `submodel_config` assigned

2. **Archival Phase**
   - Archive the current published version (if any)
   - Mark the draft version as published

3. **New Draft Creation**
   - Create a new draft version as a deep copy of the published version
   - Auto-create BulkMigrationPlan stubs for stale entities

### Validation Requirements

#### Default Values Validation

Before publishing, the system validates all default values:

- **Single-field rules**: Validation rules that apply to individual fields
- **Multi-field rules**: Validation rules that span multiple fields
- **Localized fields**: Each language variant is validated separately

#### Submodel Validation

All submodel fields (type `SUBMODEL_SELECT` or `SUBMODEL_LIST`) must have `submodel_config` assigned before publishing:

```python
def _validate_submodels_for_publish(self):
    """A published config must not contain submodel fields without a
    submodel_config."""
    orphaned = [
        fd.slug for fd in self.field_definitions.filter(
            data_type__in=(
                DataField.DataType.SUBMODEL_SELECT,
                DataField.DataType.SUBMODEL_LIST,
            ),
            submodel_config__isnull=True,
        )
    ]
    if orphaned:
        raise ValidationError({
            slug: ["submodel_config_version_id is required for submodel types before publishing"]
            for slug in orphaned
        })
```

### Deep Copy of Field Definitions

The `_create_draft_copy()` method creates a complete deep copy:

```python
def _create_draft_copy(self):
    new_draft = ConfigVersion.objects.create(
        config=self.config,
        status=ConfigVersion.Status.DRAFT,
        notes="",
    )
    field_map = {}  # old data field id → new data field
    
    # Copy field definitions with all related data
    for old_field in self.field_definitions.all():
        new_field = DataField.objects.create(
            version=new_draft,
            slug=old_field.slug,
            data_type=old_field.data_type,
            is_localized=old_field.is_localized,
            submodel_config=old_field.submodel_config,
            workflow_version=old_field.workflow_version,
            type_config=old_field.type_config,
        )
        field_map[old_field.pk] = new_field
        
        # Copy field defaults (values, not references)
        for d in old_field.defaults.all():
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
    
    # Copy form elements with their structure and translations
    element_map = {}
    for old_el in self.form_elements.all().order_by("sort_order", "id"):
        new_el = FormElement.objects.create(
            version=new_draft,
            slug=old_el.slug,
            element_type=old_el.element_type,
            parent=None,  # resolved after all exist
            sort_order=old_el.sort_order,
            is_preview=old_el.is_preview,
            type_config=old_el.type_config,
        )
        element_map[old_el.pk] = new_el
    
    # Resolve parents after all elements exist
    for old_el in self.form_elements.all():
        new_el = element_map[old_el.pk]
        if old_el.parent_id:
            new_el.parent = element_map.get(old_el.parent_id)
            new_el.save(update_fields=["parent"])
        # Copy translations and bindings
    
    # Copy validation rules
    for old_rule in SingleFieldValidationRule.objects.filter(field__version=self):
        new_field = field_map.get(old_rule.field_id)
        if new_field:
            old_rule.clone_to(new_field).save()
    
    # Copy multi-field rules
    for old_rule in MultiFieldValidationRule.objects.filter(config_version=self):
        real = old_rule.get_real_instance()
        real.pk = None
        real.id = None
        real.config_version = new_draft
        real.save()
    
    return new_draft
```

### Auto-Archiving of Published Versions

When publishing, the current published version is automatically archived:

```python
def publish(self):
    with transaction.atomic():
        # Archive the current published version
        ConfigVersion.objects.filter(
            config=self.config, status=self.Status.PUBLISHED
        ).update(status=self.Status.ARCHIVED)
        
        self.status = self.Status.PUBLISHED
        self.published_at = now()
        self.save()
        
        # Auto-create new DRAFT as deep copy
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
```

### Bulk Migration Plan Auto-Creation

When publishing, the system automatically creates BulkMigrationPlan stubs for all stale entity versions:

- **Stale entities**: Entities that reference an older config version
- **Migration plans**: Auto-created but not executed until manually triggered
- **Type filter**: Set to `None` to apply to all types in the config

## Workflow Version Publishing (`WorkflowVersion.publish`)

The `publish()` method on `WorkflowVersion` handles workflow version transitions.

### Process Flow

1. **Archival Phase**
   - Archive the current published version
   - Mark the draft version as published

2. **New Draft Creation**
   - Create a new draft version as a copy of the published version
   - Preserve all states, transitions, and properties

### State and Transition Copy

The `_create_draft_copy()` method preserves:

- **State definitions**: Name, position, colors, initial flag
- **Transitions**: From/to states, handles, properties
- **Virtual node positions**: Layout information for visual editors
- **Properties**: Default transition properties for Rego policies
- **Translations**: All localized labels

```python
def publish(self):
    with transaction.atomic():
        WorkflowVersion.objects.filter(
            workflow=self.workflow, status=self.Status.PUBLISHED
        ).update(status=self.Status.ARCHIVED)

        self.status = self.Status.PUBLISHED
        self.published_at = now()
        self.save()

        return self._create_draft_copy()

def _create_draft_copy(self):
    new_draft = WorkflowVersion.objects.create(
        workflow=self.workflow,
        status=WorkflowVersion.Status.DRAFT,
        notes="",
        virtual_node_positions=self.virtual_node_positions,
        properties=self.properties,
    )
    state_map = {}
    for old_state in self.states.prefetch_related("translations").all():
        new_state = WorkflowState.objects.create(
            version=new_draft,
            name=old_state.name,
            is_initial=old_state.is_initial,
            position_x=old_state.position_x,
            position_y=old_state.position_y,
            background_color=old_state.background_color,
        )
        state_map[old_state.pk] = new_state
        for t in old_state.translations.all():
            WorkflowStateTranslation.objects.create(
                state=new_state, language=t.language, label=t.label
            )
    for old_trans in self.transitions.prefetch_related("translations").select_related("from_state", "to_state").all():
        new_trans = WorkflowTransition.objects.create(
            version=new_draft,
            name=old_trans.name,
            from_state=state_map.get(old_trans.from_state_id) if old_trans.from_state_id else None,
            from_undefined_only=old_trans.from_undefined_only,
            to_state=state_map[old_trans.to_state_id],
            source_handle=old_trans.source_handle,
            target_handle=old_trans.target_handle,
            properties=old_trans.properties,
        )
        for t in old_trans.translations.all():
            WorkflowTransitionTranslation.objects.create(
                transition=new_trans, language=t.language, label=t.label
            )
    return new_draft
```

### Virtual Node Position Inheritance

The `virtual_node_positions` JSON field is preserved during publishing:

```python
# Example structure
{
    "states": {
        "state-uuid-1": {"x": 100, "y": 50, "w": 120, "h": 60},
        "state-uuid-2": {"x": 100, "y": 150, "w": 120, "h": 60}
    },
    "transitions": {
        "transition-uuid-1": {
            "source_handle": "right",
            "target_handle": "left",
            "points": [[100, 80], [150, 80], [150, 150], [100, 150]]
        }
    }
}
```

## API Endpoints

### Config Publish Endpoint

**Endpoint**: `POST /configs/{id}/publish/`

**Permissions**: Requires `change_fieldconfig` permission.

**Behavior**:
1. Validates the draft config version
2. Archives the current published version (if any)
3. Publishes the draft version
4. Creates a new draft version as deep copy
5. Auto-creates BulkMigrationPlan stubs for stale entities

**Response**:
```json
{
  "id": "uuid",
  "status": "draft|published",
  "config_id": "uuid",
  "created_at": "2024-01-01T00:00:00Z",
  "published_at": "2024-01-01T00:00:00Z",
  "field_definitions": [...],
  "form_elements": [...],
  "validation_rules": [...]
}
```

**Error Responses**:
- `404 Not Found`: Config or draft version doesn't exist
- `422 Unprocessable Entity`: Validation errors (invalid defaults, missing submodel config)
- `403 Forbidden`: User lacks permissions

### Workflow Publish Endpoint

**Endpoint**: `POST /workflows/{workflow_id}/versions/draft/publish/`

**Permissions**: Requires `change_datafield` permission.

**Behavior**:
1. Validates the draft workflow version
2. Archives the current published version (if any)
3. Publishes the draft version
4. Creates a new draft version as copy

**Response**:
```json
{
  "id": "uuid",
  "name": "string",
  "description": "string",
  "initial_state": "state_name",
  "states": [...],
  "transitions": [...],
  "virtual_node_positions": {...},
  "draft_version_id": "uuid",
  "published_version_id": "uuid",
  "last_edited_at": "2024-01-01T00:00:00Z",
  "last_published_at": "2024-01-01T00:00:00Z"
}
```

**Error Responses**:
- `404 Not Found`: Workflow or draft version doesn't exist
- `403 Forbidden`: User lacks permissions

## Practical Examples

### Example 1: Publishing a Config with Submodel Dependencies

```python
# Scenario: Publishing a config with a submodel field that references another config

# Draft config A has a submodel_select field referencing config B
config_a_draft.field_definitions.create(
    slug="related_data",
    data_type="submodel_select",
    submodel_config=config_b_published,  # Must be published
)

# Publishing config A
config_a_draft.publish()  # ✅ Succeeds - config B is published

# Now config A is published, and a new draft is created
# Config B remains unchanged and can be edited independently
```

### Example 2: Publishing with Validation Errors

```python
# Scenario: Publishing a config with invalid default values

# Draft config has a field with invalid default
field = config_draft.field_definitions.create(
    slug="age",
    data_type="integer",
    is_localized=False,
)
field.defaults.create(value_integer=-5)  # Invalid: negative value

# Single-field rule requires age >= 0
# Publishing fails with validation errors

try:
    config_draft.publish()
except ValidationError as e:
    # Error: {"age": ["Must be >= 0"]}
    # Draft remains unchanged
    pass
```

### Example 3: Publishing a Workflow with Custom Properties

```python
# Scenario: Publishing a workflow with custom transition properties

# Draft workflow has transition properties for Rego policies
workflow_draft.properties = {
    "transition_defaults": {
        "requires_review": False,
        "notifications": ["admin"]
    }
}
workflow_draft.save()

# Publishing preserves these properties
new_published = workflow_draft.publish()
assert new_published.status == "published"
assert new_published.properties == {
    "transition_defaults": {
        "requires_review": False,
        "notifications": ["admin"]
    }
}

# New draft inherits the properties
new_draft = WorkflowVersion.objects.get(
    workflow=workflow_draft.workflow,
    status="draft"
)
assert new_draft.properties == {
    "transition_defaults": {
        "requires_review": False,
        "notifications": ["admin"]
    }
}
```

### Example 4: Bulk Migration Plan Auto-Creation

```python
# Scenario: Multiple entity versions exist when publishing

# Entities reference different versions
entity1 = UserDefinedModelEntityNode.objects.create(
    config_version=config_v1,
    userdefinedmodelentity=entity_obj,
)

entity2 = UserDefinedModelEntityNode.objects.create(
    config_version=config_v2,
    userdefinedmodelentity=entity_obj,
)

# Publish config_v3
new_draft = config_v3.publish()

# BulkMigrationPlan stubs are auto-created
# For entity1 (v1 → v3)
# For entity2 (v2 → v3)

assert BulkMigrationPlan.objects.filter(
    source_version=config_v1,
    target_version=config_v3,
).exists()
assert BulkMigrationPlan.objects.filter(
    source_version=config_v2,
    target_version=config_v3,
).exists()
```

## Best Practices

### For Config Authors

1. **Always validate submodel references**: Ensure all submodel fields have their `submodel_config` set before publishing.

2. **Test defaults thoroughly**: Validate default values against validation rules before publishing.

3. **Use descriptive version notes**: Add context to published versions for audit purposes.

### For Workflow Authors

1. **Test state transitions**: Verify all transitions work correctly before publishing.

2. **Document state names**: Use clear, consistent state names that describe the workflow state.

3. **Preserve layout information**: Ensure virtual node positions are saved when editing workflows visually.

### For API Users

1. **Handle validation errors**: Catch 422 responses and display field-level error messages.

2. **Check version status**: Always verify the current status before publishing.

3. **Understand the draft cycle**: Publishing creates a new draft - changes are additive, not destructive.

## Troubleshooting

### Common Issues

1. **"No draft to publish" (404)**
   - Create a draft version before attempting to publish
   - `POST /configs/{id}/versions/draft/` or `PUT /configs/{id}/versions/draft/`

2. **"submodel_config_version_id is required" (422)**
   - Set `submodel_config` on all submodel fields before publishing
   - Reference a published config version

3. **Validation rule errors (422)**
   - Review field validation rules and adjust defaults or rules
   - Check both single-field and multi-field rules

4. **Permission denied (403)**
   - Ensure user has `change_fieldconfig` (configs) or `change_datafield` (workflows) permissions