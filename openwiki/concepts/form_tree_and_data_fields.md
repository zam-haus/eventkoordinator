---
type: concepts_documentation
title: Form Tree and Data Fields
description: Documentation for the form tree and data fields relationship
---

# Form Tree and Data Fields

This document explains the relationship between form tree elements and database data fields.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Publishing System](publishing.md) - Publishing system documentation

## Overview

The system separates **form structure** (how fields are displayed) from **data storage** (what values are stored).

## DataField vs FormElement vs FormElementBinding

### DataField (Database Schema)

The `DataField` model (formerly `FieldDefinition`) represents the **data schema**:

```python
class DataField(MetaBase):
    class DataType(models.TextChoices):
        TEXT_SHORT = 'text_short'
        TEXT_LONG = 'text_long'
        TEXT_MARKDOWN = 'text_markdown'
        INTEGER = 'integer'
        FLOAT = 'float'
        BOOLEAN = 'boolean'
        DATE = 'date'
        DATETIME = 'datetime'
        SELECT_SINGLE = 'select_single'
        SELECT_MULTI = 'select_multi'
        USER_SELECT = 'user_select'
        USER_SELECT_MULTI = 'user_select_multi'
        GROUP_SELECT = 'group_select'
        GROUP_SELECT_MULTI = 'group_select_multi'
        ENTITY_SELECT = 'entity_select'
        ENTITY_SELECT_MULTI = 'entity_select_multi'
        WORKFLOW = 'workflow'
        IMAGE = 'image'
        FILE = 'file'
    
    slug = models.CharField(max_length=100)
    data_type = models.CharField(max_length=30, choices=DataType)
    is_localized = models.BooleanField(default=False)
    type_config = JSONField(default=dict)
    submodel_config = JSONField(default=dict)
    workflow_version = models.ForeignKey(..., null=True, blank=True)
    defaults = JSONField(default=dict)
```

**Purpose**: Defines the **data type**, validation, and storage semantics.

### FormElement (Form Structure)

The `FormElement` model represents the **form structure**:

```python
class FormElement(MetaBase):
    PARENT_CHOICES = [
        ('tab_container', 'Tab Container'),
        ('tab', 'Tab'),
        ('hstack', 'Horizontal Stack'),
        ('hstack_group', 'Horizontal Stack Group'),
        ('save_button', 'Save Button'),
        ('tab_prev', 'Tab Previous'),
        ('tab_next', 'Tab Next'),
    ]
    
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True)
    sort_order = models.IntegerField(default=0)
    is_preview = models.BooleanField(default=False)
    element_type = models.CharField(max_length=30, choices=PARENT_CHOICES)
    slug = models.CharField(max_length=100)
    type_config = JSONField(default=dict)
```

**Purpose**: Defines the **visual structure** and **rendering** of the form.

### FormElementBinding (M:N Relationship)

The `FormElementBinding` model creates the **many-to-many relationship**:

```python
class FormElementBinding(MetaBase):
    FORM_ROLE = 'form'
    READONLY_ROLE = 'readonly'
    HIDDEN_ROLE = 'hidden'
    
    form_element = models.ForeignKey(FormElement, on_delete=models.CASCADE)
    data_field = models.ForeignKey(DataField, on_delete=models.PROTECT)
    role = models.CharField(max_length=20)  # form, readonly, hidden
```

**Purpose**: Binds form elements to data fields with different roles.

## Relationship Diagram

```
┌─────────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│   FormElement   │─────▶│  FormElementBinding │◀────│    DataField    │
│  (Structure)    │     │  (M:N Relationship)  │     │  (Data Schema)  │
└─────────────────┘     └─────────────────────┘     └─────────────────┘
      │                                      │
      │                                      │
      ▼                                      ▼
  ┌─────────────────┐                 ┌─────────────────┐
  │  FormElement    │                 │    DataField    │
  │  Translation    │                 │  FieldValue     │
  │  (Labels)       │                 │  (Values)       │
  └─────────────────┘                 └─────────────────┘
```

## Structural Types (Form Element Types)

### Tab Container

Groups tab elements:

```json
{
  "element_type": "tab_container",
  "slug": "main_tabs",
  "type_config": {
    "default_tab": "tab_1"
  }
}
```

**Behavior**: Renders as tab container with child tabs.

### Tab

Represents a single tab:

```json
{
  "element_type": "tab",
  "slug": "tab_1",
  "type_config": {
    "label": "General",
    "icon": "general"
  }
}
```

**Behavior**: Renders as a tab with child form elements.

### HStack (Horizontal Stack)

Groups fields horizontally:

```json
{
  "element_type": "hstack",
  "slug": "name_row",
  "type_config": {
    "children": ["first_name", "last_name"]
  }
}
```

**Behavior**: Renders fields side-by-side.

### HStack Group

Groups multiple HStack elements:

```json
{
  "element_type": "hstack_group",
  "slug": "contact_info",
  "type_config": {
    "title": "Contact Information"
  }
}
```

**Behavior**: Groups related HStack elements.

### Save Button

Save button element:

```json
{
  "element_type": "save_button",
  "slug": "save",
  "type_config": {
    "label": "Save Changes"
  }
}
```

**Behavior**: Renders save button, no data field binding.

### Tab Navigation

Previous/next tab buttons:

```json
{
  "element_type": "tab_prev",
  "slug": "prev_tab",
  "type_config": {
    "label": "Previous"
  }
}
```

## FieldDefinition as Alias for DataField

### Migration Background

Previously, `FieldDefinition` contained both data schema and form structure columns:

```python
# OLD (before split)
class FieldDefinition(MetaBase):
    # Data schema columns
    slug = models.CharField(max_length=100)
    data_type = models.CharField(max_length=30)
    
    # Form structure columns (removed in split)
    parent_slug = models.CharField(max_length=100, null=True)
    sort_order = models.IntegerField(default=0)
    is_preview = models.BooleanField(default=False)
```

### New Structure (after split)

**DataField**: Contains only data schema columns:
- `slug` (renamed from original)
- `data_type`
- `is_localized`
- `type_config`
- `submodel_config`
- `workflow_version`
- `defaults`

**FormElement**: Contains form structure columns:
- `parent` (FK to self)
- `sort_order`
- `is_preview`
- `element_type`
- `slug`
- `type_config`

### Backward Compatibility

During the split migration:

1. **DataField**: Renamed from `FieldDefinition`, dropped form columns
2. **FormElement**: Created new table with form structure
3. **FormElementBinding**: Created M:N relationship
4. **FieldValue.field**: Still references `DataField` (same table/PK)

## Structural Types with No DataField Binding

Some form elements have **no DataField binding**:

### Save Button
```json
{
  "element_type": "save_button",
  "slug": "save"
}
```

### Tab Navigation (tab_prev, tab_next)
```json
{
  "element_type": "tab_prev",
  "slug": "prev_tab"
}
```

### Structural Elements
```json
{
  "element_type": "tab_container",
  "slug": "tabs"
}
```

**Key Point**: These elements exist only for form structure, not data storage.

## Backward Compatibility with Rego Policies

### Input Schema Compatibility

During the split, the Rego input schema remained compatible:

**Before Split**:
```json
{
  "entity": {
    "fields": {
      "slug": {
        "data_type": "text_short",
        "value": "..."
      }
    }
  }
}
```

**After Split**:
```json
{
  "entity": {
    "fields": {
      "slug": {
        "data_type": "text_short",  // From DataField
        "value": "..."
      }
    }
  }
}
```

**Key**: `DataField.data_type` is used as `element_type` in Rego input, maintaining compatibility.

### Migration Strategy

**Option C1 (Chosen)**: Keep `input_version=1` compatible
- Emit structural elements into `entity.fields` with `element_type` as `data_type`
- `input.schemas` built from `DataField` only
- No Rego rewrite required

**Alternative (Not Chosen)**: Bump to `input_version=2`
- Drop structural elements from input
- Requires rewriting all Rego policies
- High risk, deferred to later

## Example Form Configuration

### Complete Form Structure

```json
{
  "form_elements": [
    {
      "element_type": "tab_container",
      "slug": "tabs",
      "children": ["general_tab", "contact_tab"]
    },
    {
      "element_type": "tab",
      "slug": "general_tab",
      "children": ["name_row", "email_field"]
    },
    {
      "element_type": "hstack",
      "slug": "name_row",
      "children": ["first_name", "last_name"]
    },
    {
      "element_type": "tab_next",
      "slug": "next_tab"
    }
  ],
  "bindings": [
    {
      "form_element": "first_name",
      "data_field": "first_name",
      "role": "form"
    },
    {
      "form_element": "last_name",
      "data_field": "last_name",
      "role": "form"
    },
    {
      "form_element": "email_field",
      "data_field": "email",
      "role": "form"
    }
  ]
}
```

### Field Types

1. **Text Fields**: `text_short`, `text_long`, `text_markdown`
2. **Number Fields**: `integer`, `float`
3. **Boolean**: `boolean`
4. **Date/Time**: `date`, `datetime`
5. **Select Fields**: `select_single`, `select_multi`
6. **User/Group/Entity**: `user_select`, `user_select_multi`, `group_select`, `group_select_multi`, `entity_select`, `entity_select_multi`
7. **Workflow**: `workflow` (with workflow_version)
8. **File/Image**: `image`, `file`

## Summary

**Key Points**:
1. **DataField**: Data schema and storage (what data is stored)
2. **FormElement**: Form structure and rendering (how fields are displayed)
3. **FormElementBinding**: M:N relationship between them
4. **Structural Types**: Tab, HStack, buttons have no DataField binding
5. **Backward Compatible**: Rego policies work without rewrite
6. **Migration Path**: DataField renamed from FieldDefinition, FormElement created new
