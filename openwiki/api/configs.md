---
type: api_documentation
title: Config API Documentation
description: Configuration API documentation for types, drafts, and versions
---

# Config API

The config API provides endpoints for managing configuration versions, drafts, and published configurations.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [API Overview](udm_overview.md) - API overview and architecture
- [Publishing System](../concepts/publishing.md) - Publishing system details

## Config Endpoints

### GET /configs/
Lists all configurations accessible to the current user.

**Permissions**: Requires `view` permission on `FieldConfig` models.

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "string",
    "description": "string",
    "stale_entity_count": 0,
    "languages": [
      {
        "code": "en",
        "label": "English",
        "is_default": true,
        "sort_order": 0
      }
    ]
  }
]
```

### GET /configs/{id}/
Retrieves a specific configuration by ID.

**Permissions**: Requires `view` permission on `FieldConfig` models.

**Response**:
```json
{
  "id": "uuid",
  "name": "string",
  "description": "string",
  "field_definitions": [...],
  "languages": [...]
}
```

### POST /configs/
Creates a new configuration.

**Permissions**: Requires `add` permission on `FieldConfig` models.

**Request**:
```json
{
  "name": "string",
  "description": "string",
  "languages": [
    {
      "code": "string",
      "label": "string",
      "is_default": true,
      "sort_order": 0
    }
  ]
}
```

**Response**: `201 Created`

### PATCH /configs/{id}/
Updates a configuration.

**Permissions**: Requires `change` permission on `FieldConfig` models.

**Request**:
```json
{
  "name": "string",
  "description": "string",
  "languages": [...]
}
```

### DELETE /configs/{id}/
Deletes a configuration.

**Permissions**: Requires `delete` permission on `FieldConfig` models.

## Config Version Endpoints

### GET /configs/{id}/versions/
Lists all versions for a configuration.

**Permissions**: Requires `view` permission on `ConfigVersion` models.

**Response**:
```json
[
  {
    "id": "uuid",
    "status": "draft|published|archived",
    "notes": "string",
    "published_at": "2024-01-01T00:00:00Z",
    "created_at": "2024-01-01T00:00:00Z",
    "entity_count": 0
  }
]
```

### GET /configs/{id}/versions/published/
Retrieves the published version for a configuration.

**Permissions**: Requires `view` permission on `ConfigVersion` models.

**Response**: `ConfigVersionOut` object

### GET /config-versions/{version_id}/
Fetches a single config version by ID (any status). Used to render an entity's form against its actual pinned version.

**Permissions**: Requires `view` permission on `ConfigVersion` models.

**Response**: `ConfigVersionOut` object

### GET /configs/{id}/versions/draft/
Retrieves the draft version for a configuration.

**Permissions**: Requires `change` permission on `FieldConfig` models.

**Response**: `ConfigVersionOut` object

### GET /configs/{id}/versions/draft/as-input/
Returns the draft config version in `ConfigDraftIn` shape for round-trip editing.

**Permissions**: Requires `change` permission on `FieldConfig` models.

**Response**: `ConfigDraftExportOut` object

### PUT /configs/{id}/versions/draft/
Replaces the entire draft version with new configuration data.

**Permissions**: Requires `change` permission on `FieldConfig` models.

**Request**: `ConfigDraftIn` object containing:
- `notes`: Draft notes
- `data_fields`: Array of field definitions
- `form_elements`: Array of form structure elements
- `fields` (deprecated): Legacy mixed shape

**Response**: `ConfigVersionOut` object

**Validation**:
- Each form element must bind to an existing data field in the same version
- Data field slugs must be unique within the version
- Form element parents must be valid elements in the same version

### POST /configs/{id}/versions/draft/publish/
Publishes the current draft version and creates a new draft copy.

**Permissions**: Requires `change` permission on `FieldConfig` models.

**Response**: `ConfigVersionOut` object for the newly published version

## Publishing System

### ConfigVersion.publish() Method

The `ConfigVersion.publish()` method implements the publishing workflow for configuration versions.

#### Workflow

1. **Validation Phase**
   - Validates that all defaults pass single-field and multi-field rules
   - Validates that every submodel field has a `submodel_config` assigned
   - Raises `ValidationError` if any validation fails

2. **Archive Current Published Version**
   - Any existing published version is automatically archived by updating its status to `ARCHIVED`

3. **Mark Current Version as Published**
   - Sets the current version's status to `PUBLISHED`
   - Sets `published_at` to current timestamp
   - Persists the changes

4. **Create New Draft Copy**
   - Creates a new draft version as a deep copy of the published version
   - Copies all field definitions, form elements, rules, and translations
   - Returns the new draft version object

#### Deep Copy Behavior

When creating the new draft copy:

- **Field Definitions**: All fields are copied with same properties:
  - `slug`, `data_type`, `is_localized`
  - `submodel_config` reference (if any)
  - `workflow_version` reference (if any)
  - `type_config` (deep copy of JSON)
  - **Defaults**: User defaults and transition defaults are copied for each field
    - For localized fields, language-specific defaults are preserved
    - For non-localized fields, single default value is preserved

- **Form Elements**: All form elements are copied with:
  - `slug`, `element_type`, `sort_order`, `is_preview`, `type_config`
  - Parent-child relationships preserved (two-pass: create then resolve parents)
  - **Form Element Translations**: All language translations are copied
  - **Form Element Bindings**: All bindings to data fields are preserved

- **Validation Rules**: All validation rules are copied:
  - Single-field validation rules (per field, applied on save)
  - Multi-field validation rules (across fields, applied on save)

#### Validation Requirements

**Submodel Fields Must Have Config Assigned**
- A published config cannot contain submodel fields (SUBMODEL_SELECT or SUBMODEL_LIST) without a `submodel_config` reference
- Drafts may temporarily contain orphaned submodel fields (for editing purposes)
- Publishing will fail with validation errors if any submodel field lacks `submodel_config`

**Default Validation**
- All field defaults are validated against single-field rules
- Multi-field validation rules are evaluated against the default values
- Any validation failures prevent publishing

#### Auto-creation of BulkMigrationPlan Stubs

After publishing, the method automatically creates `BulkMigrationPlan` stubs for stale entities:

- Identifies all entity nodes that reference the old published version
- For each stale version, creates a `BulkMigrationPlan` with:
  - `source_version`: The old version referenced by entities
  - `target_version`: The newly published version
  - `user_defined_model_type_filter`: `None` (applies to all types)
  - `status`: `DRAFT`

These stubs can be expanded later to create actual migration plans for each entity.

---

## API Endpoints

### POST /configs/{id}/publish/

Publishes the draft version for a configuration.

**Endpoint**: `POST /configs/{config_id}/publish/`

**Authentication**: Required (Django auth)

**Permissions**: Requires `userdefinedmodel.change_fieldconfig`

**Path Parameters**:
- `config_id` (UUID): The ID of the configuration to publish

**Request Body**: Empty

**Response**:
```json
{
  "id": "uuid",
  "config_id": "uuid",
  "status": "published",
  "notes": "",
  "published_at": "2024-01-01T00:00:00Z",
  "created_at": "2024-01-01T00:00:00Z",
  "field_definitions": [...],
  "form_elements": [...]
}
```

**Error Responses**:
- `403 Forbidden`: User lacks required permissions
- `404 Not Found`: Configuration or draft version not found
- `422 Unprocessable Entity`: Validation failed (details in `errors` field)

**Error Response (Validation Failure)**:
```json
{
  "errors": {
    "field_slug": ["submodel_config_version_id is required for submodel types before publishing"]
  }
}
```

**Implementation Details**:
1. Validates the current user has `change` permission on `FieldConfig`
2. Fetches the configuration and validates it exists
3. Fetches the draft version (status=`DRAFT`) for the configuration
4. Calls `ConfigVersion.publish()` method:
   - Validates all defaults against rules
   - Validates all submodel fields have submodel_config assigned
   - Archives the current published version (if any)
   - Marks current version as published
   - Creates new draft copy with deep copy of all content
   - Creates BulkMigrationPlan stubs for stale entities
5. Returns the newly published version (status=`PUBLISHED`)
