---
type: backend_documentation
title: Model Overview
description: Overview of all backend models and their relationships
---

# Model Overview

This document provides an overview of all backend models in the UDM application.

**Related Documentation**:
- [Architecture Overview](../../architecture/overview.md) - High-level system architecture
- [Backend Overview](../backend/overview.md) - Backend components

## Core Models

### 1. UserDefinedModelType

Represents a user-defined model type.

**Fields**:
- `id` (UUID, primary key)
- `name` (string): Type name
- `description` (string): Type description
- `field_config` (ForeignKey): Field configuration
- `workflow_definition` (ForeignKey): Workflow definition
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `get_field_definitions()`: Get all field definitions
- `get_workflow()`: Get workflow definition
- `get_policies()`: Get associated policies
- `is_published()`: Check if type is published

**Relationships**:
- Has many `UserDefinedModelEntity` instances
- Has one `FieldConfig`
- Has one `WorkflowDefinition`
- Has many `TypePolicy` associations

### 2. FieldConfig

Represents a field configuration.

**Fields**:
- `id` (UUID, primary key)
- `name` (string): Configuration name
- `description` (string): Configuration description
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `get_field_definitions()`: Get all field definitions
- `get_languages()`: Get configuration languages
- `get_versions()`: Get configuration versions

**Relationships**:
- Has many `FieldDefinition` instances
- Has many `ConfigLanguage` instances
- Has many `ConfigVersion` instances

### 3. ConfigVersion

Represents a version of a configuration.

**Fields**:
- `id` (UUID, primary key)
- `config` (ForeignKey): Field configuration
- `version_name` (string): Version name
- `status` (ChoiceField): draft | published | archived
- `published_at` (datetime, nullable)
- `publish_note` (string, nullable)
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `publish()`: Publish the version
- `archive()`: Archive the version
- `is_published()`: Check if published

**Relationships**:
- Belongs to one `FieldConfig`
- Has many `FieldDefinition` instances
- Has many `UserDefinedModelEntity` instances

### 4. FieldDefinition

Represents a field definition.

**Fields**:
- `id` (UUID, primary key)
- `version` (ForeignKey): Config version
- `slug` (string): Field slug
- `data_type` (ChoiceField): text | long_text | number | boolean | date | select | multi_select | submodel_select | submodel_list | workflow | file
- `label` (string): Field label
- `help_text` (string): Field help text
- `is_required` (boolean)
- `is_localized` (boolean)
- `sort_order` (integer)
- `default_value` (JSON, nullable)
- `config` (JSON): Field configuration

**Methods**:
- `get_translation(language)`: Get field translation
- `is_valid_value(value)`: Validate field value
- `get_widget_type()`: Get widget type

**Relationships**:
- Belongs to one `ConfigVersion`
- Has many `DataField` instances
- Has many `FormElement` instances

### 5. UserDefinedModelEntity

Represents an entity instance.

**Fields**:
- `id` (UUID, primary key)
- `config_version` (ForeignKey): Config version
- `user_defined_model_type` (ForeignKey): Type definition
- `owner` (ForeignKey): OpenIDUser
- `status` (string): Workflow status
- `created_at` (datetime)
- `updated_at` (datetime)
- `created_by` (ForeignKey): OpenIDUser
- `updated_by` (ForeignKey): OpenIDUser

**Methods**:
- `get_field_values()`: Get all field values
- `get_children()`: Get child entities
- `get_workflow_status()`: Get current workflow status
- `get_permissions(user)`: Get user permissions
- `materialize_defaults()`: Materialize default values
- `materialize_user_defaults(user)`: Materialize user defaults

**Relationships**:
- Belongs to one `ConfigVersion`
- Belongs to one `UserDefinedModelType`
- Belongs to one `OpenIDUser` (owner)
- Has many `FieldValue` instances
- Has many child `UserDefinedModelEntity` instances (hierarchical)

### 6. FieldValue

Represents a field value for an entity.

**Fields**:
- `id` (UUID, primary key)
- `entity` (ForeignKey): UserDefinedModelEntity
- `field` (ForeignKey): FieldDefinition
- `value` (JSON): Field value
- `locale` (string, nullable): Language code
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `get_value()`: Get field value
- `set_value(value)`: Set field value
- `is_localized()`: Check if field is localized

**Relationships**:
- Belongs to one `UserDefinedModelEntity`
- Belongs to one `FieldDefinition`

### 7. WorkflowDefinition

Represents a workflow definition.

**Fields**:
- `id` (UUID, primary key)
- `name` (string): Workflow name
- `description` (string): Workflow description
- `is_active` (boolean)
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `get_states()`: Get all states
- `get_transitions()`: Get all transitions
- `get_initial_state()`: Get initial state
- `get_state(slug)`: Get state by slug
- `get_transition(slug)`: Get transition by slug

**Relationships**:
- Has many `WorkflowState` instances
- Has many `WorkflowTransition` instances
- Has many `FieldDefinition` instances (workflow fields)

### 8. WorkflowState

Represents a workflow state.

**Fields**:
- `id` (UUID, primary key)
- `workflow` (ForeignKey): WorkflowDefinition
- `slug` (string): State slug
- `label` (string): State label
- `sort_order` (integer)
- `is_initial` (boolean)

**Methods**:
- `get_transitions()`: Get outgoing transitions
- `get_label(language)`: Get state label in language

**Relationships**:
- Belongs to one `WorkflowDefinition`
- Has many `WorkflowTransition` instances (as source)

### 9. WorkflowTransition

Represents a workflow transition.

**Fields**:
- `id` (UUID, primary key)
- `workflow` (ForeignKey): WorkflowDefinition
- `slug` (string): Transition slug
- `label` (string): Transition label
- `source_state` (ForeignKey): WorkflowState
- `target_state` (ForeignKey): WorkflowState
- `sort_order` (integer)

**Methods**:
- `get_label(language)`: Get transition label in language
- `can_execute(entity, user)`: Check if transition can be executed

**Relationships**:
- Belongs to one `WorkflowDefinition`
- Belongs to one `WorkflowState` (source)
- Belongs to one `WorkflowState` (target)

### 10. Policy

Represents a Rego policy.

**Fields**:
- `id` (UUID, primary key)
- `slug` (string): Policy slug
- `source` (text): Rego policy source
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `validate()`: Validate Rego syntax
- `compile()`: Compile policy
- `evaluate(entity, user, rule, locale)`: Evaluate policy

**Relationships**:
- Has many `TypePolicy` instances

### 11. TypePolicy

Represents a policy association with a type.

**Fields**:
- `id` (UUID, primary key)
- `type` (ForeignKey): UserDefinedModelType
- `policy` (ForeignKey): Policy
- `sort_order` (integer)

**Methods**:
- `get_policy()`: Get policy
- `evaluate(entity, user, rule, locale)`: Evaluate policy

**Relationships**:
- Belongs to one `UserDefinedModelType`
- Belongs to one `Policy`

### 12. StagingFile

Represents a staged file.

**Fields**:
- `id` (UUID, primary key)
- `uploader` (ForeignKey): OpenIDUser
- `file` (FileField): File content
- `original_name` (string): Original filename
- `mime_type` (string): MIME type
- `size_bytes` (integer): File size
- `expires_at` (datetime): Expiration date
- `intended_field_id` (UUID, nullable): Target field ID
- `created_at` (datetime)

**Methods**:
- `get_file()`: Get file content
- `delete()`: Delete file
- `is_expired()`: Check if expired

**Relationships**:
- Belongs to one `OpenIDUser` (uploader)

### 13. EditGroup

Represents a group of edits.

**Fields**:
- `id` (UUID, primary key)
- `user` (ForeignKey): OpenIDUser
- `created_at` (datetime)
- `notes` (string, nullable)

**Methods**:
- `get_records()`: Get edit records
- `get_entities()`: Get affected entities

**Relationships**:
- Belongs to one `OpenIDUser`
- Has many `EditRecord` instances

### 14. EditRecord

Represents an individual edit.

**Fields**:
- `id` (UUID, primary key)
- `edit_group` (ForeignKey): EditGroup
- `entity` (ForeignKey): UserDefinedModelEntity
- `field_slug` (string): Field slug
- `old_value` (JSON, nullable)
- `new_value` (JSON, nullable)
- `created_at` (datetime)

**Methods**:
- `get_diff()`: Get value difference
- `is_changed()`: Check if value changed

- Belongs to one `EditGroup`
- Belongs to one `UserDefinedModelEntity`

### 15. MailTemplate

Represents a Jinja2 mail template for email notifications.

**Fields**:
- `id` (UUID, primary key)
- `slug` (string, unique): Template identifier
- `description` (string, nullable): Template description
- `subject` (string): Email subject (Jinja2 template)
- `body_text` (string): Plain text body (Jinja2 template)
- `body_html` (string): HTML body (Jinja2 template)
- `example_input` (JSON): Example input for testing
- `created_at` (datetime)
- `updated_at` (datetime)

**Methods**:
- `render(context)`: Render template with context
- `get_example_context()`: Get example context from example_input

**Usage**:
- Templates are stored in the database and editable in UDM Admin → UDM Templating
- Rendered in a sandboxed Jinja2 environment for security
- Supports both plain text and HTML email bodies

**Template Context**:
- `context`: Policy's own context JSON
- `input`: Full policy input document
- `entity`: `input.entity` convenience alias
- `fields`: `{slug: value}` for all fields on the node
- `node`: `{id, schema_id}` of the triggering node
- `user`: The actor
- `trigger`: Lifecycle event: `save`, `create`, `transition`
- `phase`: Dispatch phase: `pre` or `post`
- `frontend_base_url`: Base URL for frontend links
- `now`: Current datetime
- **Filters**: `timezone`, `isoformat`, `htmlquote`, `userinput`

**Security**:
- Rendered in `SandboxedEnvironment` to prevent access to dangerous attributes
- Context is JSON round-tripped to ensure only plain data
- Django settings not exposed (unlike in `project.jinja2`)

**File-based Templates**:
- Stored in `documentation/configuration/templates/` as `{slug}.txt.j2` and `{slug}.html.j2`
- Each template has a corresponding `.json` file with example input
- Used as fallback when no database template exists

**Migration Support**:
- Included in UDM bundles as `udm_mailtemplates`
- Exported/imported via `export_udm_bundle` / `import_udm_bundle` commands

**Relationships**:
- No foreign key relationships (self-contained)


## Model Relationships


### Hierarchical Structure

```
UserDefinedModelType
  |
  +-- FieldConfig
  |     |
  |     +-- ConfigVersion
  |           |
  |           +-- FieldDefinition
  |
  +-- WorkflowDefinition
        |
        +-- WorkflowState
        |
        +-- WorkflowTransition
```

### Entity Structure

```
UserDefinedModelEntity
  |
  +-- ConfigVersion
  |
  +-- UserDefinedModelType
  |
  +-- FieldValue[]
  |
  +-- Children[] (hierarchical)
```

### Workflow Structure

```
WorkflowDefinition
  |
  +-- WorkflowState[]
  |
  +-- WorkflowTransition[]
```

### Policy Structure

```
Policy
  |
  +-- TypePolicy[]
```

## Data Flow

### Entity Creation

1. Create `UserDefinedModelEntity`
2. Materialize default values
3. Materialize user defaults
4. Evaluate create policy
5. Execute actions
6. Create `FieldValue` instances
7. Create `EditGroup` and `EditRecord`

### Entity Update

1. Load entity
2. Apply changes
3. Validate changes
4. Evaluate update policy
5. Execute actions
6. Update `FieldValue` instances
7. Create `EditGroup` and `EditRecord`

### Workflow Transition

1. Load entity
2. Validate transition
3. Execute transition
4. Update status
5. Evaluate transition policy
6. Execute actions
7. Create `EditGroup` and `EditRecord`

## Indexes

### Performance Indexes

- `UserDefinedModelEntity.user_defined_model_type_id`
- `UserDefinedModelEntity.config_version_id`
- `UserDefinedModelEntity.owner_id`
- `FieldValue.entity_id`
- `FieldValue.field_id`
- `FieldDefinition.version_id`
- `WorkflowState.workflow_id`
- `WorkflowTransition.source_state_id`
- `WorkflowTransition.target_state_id`

## Constraints

### Unique Constraints

- `FieldDefinition.version_id` + `slug` unique
- `WorkflowState.workflow_id` + `slug` unique
- `WorkflowTransition.workflow_id` + `slug` unique

### Foreign Key Constraints

- All foreign keys have `on_delete=CASCADE` or `on_delete=SET_NULL`
- Integrity constraints enforced at database level

## Performance Optimizations

### Query Optimization

1. **Select Related**: Use `select_related` for foreign keys
2. **Prefetch Related**: Use `prefetch_related` for many-to-many
3. **Index Optimization**: Add indexes for common queries
4. **Query Caching**: Cache query results

### Memory Optimization

1. **Chunked Processing**: Process large datasets in chunks
2. **Streaming**: Stream large responses
3. **Lazy Loading**: Load data on demand

## Database Schema

### Tables

- `userdefinedmodel_udmtype`: User-defined model types
- `userdefinedmodel_fieldconfig`: Field configurations
- `userdefinedmodel_configversion`: Configuration versions
- `userdefinedmodel_fielddefinition`: Field definitions
- `userdefinedmodel_entity`: Entities
- `userdefinedmodel_fieldvalue`: Field values
- `userdefinedmodel_workflowdefinition`: Workflow definitions
- `userdefinedmodel_workflowstate`: Workflow states
- `userdefinedmodel_workflowtransition`: Workflow transitions
- `userdefinedmodel_policy`: Policies
- `userdefinedmodel_typepolicy`: Type-policy associations
- `userdefinedmodel_stagingfile`: Staging files
- `userdefinedmodel_editgroup`: Edit groups
- `userdefinedmodel_editrecord`: Edit records

### Migrations

- All changes tracked in Django migrations
- Migrations stored in `userdefinedmodel/migrations/`

## Testing

### Model Tests

```python
class ModelTests(TestCase):
    def test_entity_creation(self):
        entity = UserDefinedModelEntity.objects.create(
            config_version=version,
            user_defined_model_type=udm_type
        )
        self.assertIsNotNone(entity.id)
    
    def test_field_value_validation(self):
        field = FieldDefinition.objects.create(...)
        entity = EntityFactory()
        
        with self.assertRaises(ValidationError):
            FieldValue.objects.create(
                entity=entity,
                field=field,
                value="invalid"
            )
```

## Best Practices

### Model Design

1. **Single Responsibility**: Each model has one purpose
2. **Clear Names**: Use clear, descriptive names
3. **Appropriate Types**: Use appropriate field types
4. **Constraints**: Use constraints to enforce data integrity

### Query Optimization

1. **Minimal Queries**: Minimize database queries
2. **Batch Processing**: Process in batches
3. **Indexes**: Use indexes for performance
4. **Caching**: Cache query results

### Data Integrity

1. **Foreign Keys**: Use foreign keys for relationships
2. **Constraints**: Use constraints for validation
3. **Transactions**: Use transactions for atomicity
4. **Error Handling**: Handle errors gracefully

## Troubleshooting

### Common Issues

1. **Query Performance**
   - Add indexes
   - Optimize queries
   - Use caching

2. **Data Integrity**
   - Check constraints
   - Use transactions
   - Handle errors

3. **Model Registration**
   - Verify model inheritance
   - Check model registration
   - Review model relationships

## Future Enhancements

### Planned Models

1. **Audit Log**: Track all changes
2. **Versioning**: Version entities
3. **Search Index**: Search-optimized models
4. **Analytics**: Analytics models
