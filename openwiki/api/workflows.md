---
type: api_documentation
title: Workflow API Documentation
description: Workflow management API documentation
---

# Workflow API

The workflow API provides endpoints for managing workflows and triggering transitions.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [API Overview](udm_overview.md) - API overview and architecture
- [Publishing System](../concepts/publishing.md) - Publishing system details

## Workflow Endpoints

### GET /workflows/
Lists all workflows accessible to the current user.

**Permissions**: Requires `view` permission on `WorkflowDefinition` models.

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "string",
    "slug": "string",
    "description": "string",
    "is_initial": true,
    "transitions": [...]
  }
]
```

### GET /workflows/{id}/
Retrieves a specific workflow by ID.

**Permissions**: Requires `view` permission on `WorkflowDefinition` models.

**Response**:
```json
{
  "id": "uuid",
  "name": "string",
  "slug": "string",
  "description": "string",
  "is_initial": true,
  "states": [...],
  "transitions": [...]
}
```

### POST /workflows/
Creates a new workflow.

**Permissions**: Requires `add` permission on `WorkflowDefinition` models.

**Request**:
```json
{
  "name": "string",
  "slug": "string",
  "description": "string",
  "states": [...],
  "transitions": [...]
}
```

**Response**: `201 Created`

### PATCH /workflows/{id}/
Updates a workflow.

**Permissions**: Requires `change` permission on `WorkflowDefinition` models.

**Request**:
```json
{
  "name": "string",
  "description": "string",
  "states": [...],
  "transitions": [...]
}
```

### DELETE /workflows/{id}/
Deletes a workflow.

**Permissions**: Requires `delete` permission on `WorkflowDefinition` models.

## Workflow Version Endpoints

### GET /workflows/{id}/versions/
Lists all versions for a workflow.

**Permissions**: Requires `view` permission on `WorkflowVersion` models.

**Response**:
```json
[
  {
    "id": "uuid",
    "status": "draft|published|archived",
    "notes": "string",
    "published_at": "2024-01-01T00:00:00Z",
    "created_at": "2024-01-01T00:00:00Z"
  }
]
```

### GET /workflows/{id}/versions/published/
Retrieves the published version for a workflow.

**Permissions**: Requires `view` permission on `WorkflowVersion` models.

**Response**: `WorkflowVersionOut` object

### GET /workflow-versions/{version_id}/
Fetches a single workflow version by ID (any status).

**Permissions**: Requires `view` permission on `WorkflowVersion` models.

**Response**: `WorkflowVersionOut` object

### GET /workflows/{id}/versions/draft/
Retrieves the draft version for a workflow.

**Permissions**: Requires `change` permission on `WorkflowDefinition` models.

**Response**: `WorkflowVersionOut` object

### PUT /workflows/{id}/versions/draft/
Replaces the entire draft version with new workflow data.

**Permissions**: Requires `change` permission on `WorkflowDefinition` models.

**Request**: `WorkflowDraftIn` object containing:
- `states`: Array of state definitions
- `transitions`: Array of transition definitions
- `virtual_node_positions`: Position data for UI rendering

**Response**: `WorkflowDefinitionOut` object

**Validation**:
- Exactly one state must have `is_initial=True`
- All transitions must reference valid states in the same version
- State names must be unique within the version

### POST /workflows/{id}/versions/draft/publish/
Publishes the current draft version and creates a new draft copy.

**Permissions**: Requires `change` permission on `WorkflowDefinition` models.

**Response**: `WorkflowDefinitionOut` object for the newly published version

## Publishing System

### WorkflowVersion.publish() Method

The `WorkflowVersion.publish()` method implements the publishing workflow for workflow versions.

#### Workflow

1. **Archive Current Published Version**
   - Any existing published version is automatically archived by updating its status to `ARCHIVED`

2. **Mark Current Version as Published**
   - Sets the current version's status to `PUBLISHED`
   - Sets `published_at` to current timestamp
   - Persists the changes

3. **Create New Draft Copy**
   - Creates a new draft version as a copy of the published version
   - Copies all states, transitions, translations, and properties
   - Returns the new draft version object

#### Copy Behavior

When creating the new draft copy:

- **Workflow Properties**: Preserved from published version:
  - `virtual_node_positions`: JSON object with node positions for UI rendering
  - `properties`: Free-form defaults merged into every transition descriptor

- **States**: All states are copied with:
  - `name`, `is_initial`, `position_x`, `position_y`, `background_color`
  - **State Translations**: All language translations are copied
  - State ordering and relationships are preserved

- **Transitions**: All transitions are copied with:
  - `name`, `from_state` (if applicable), `to_state`
  - `from_undefined_only`: Whether the transition is available from undefined state
  - `source_handle`, `target_handle`: Handle identifiers for UI rendering
  - `properties`: Transition-specific properties (overrides version defaults)
  - **Transition Translations**: All language translations are copied
  - All virtual node positions and properties are inherited from the published version

#### Translation Preservation

Both state and transition translations are fully preserved during publishing:
- All language labels are copied to the new draft
- Translation keys are preserved (no re-keying)
- Translation values are copied verbatim

#### Virtual Node Position Inheritance

The `virtual_node_positions` JSON object is copied from the published version:
- Contains layout positions for all workflow elements (states, transitions)
- Used by the UI to render the workflow graph
- Preserves any manual layout adjustments made in the UI

---

## API Endpoints

### POST /workflows/{id}/publish/

Publishes the draft version for a workflow.

**Endpoint**: `POST /workflows/{workflow_id}/publish/`

**Authentication**: Required (Django auth)

**Permissions**: Requires `userdefinedmodel.change_datafield`

**Path Parameters**:
- `workflow_id` (UUID): The ID of the workflow to publish

**Request Body**: Empty

**Response**:
```json
{
  "id": "uuid",
  "name": "string",
  "description": "string",
  "draft_version_id": "uuid",
  "published_version_id": "uuid",
  "states": [...],
  "transitions": [...],
  "virtual_node_positions": {...}
}
```

**Error Responses**:
- `403 Forbidden`: User lacks required permissions
- `404 Not Found`: Workflow or draft version not found
- `422 Unprocessable Entity`: Validation failed (details in `errors` field)

**Error Response (Validation Failure)**:
```json
{
  "errors": {
    "detail": "exactly one state must have is_initial=True"
  }
}
```

**Implementation Details**:
1. Validates the current user has `change` permission on `WorkflowDefinition`
2. Fetches the workflow definition and validates it exists
3. Fetches the draft version (status=`DRAFT`) for the workflow
4. Calls `WorkflowVersion.publish()` method:
   - Archives the current published version (if any)
   - Marks current version as published with timestamp
   - Creates new draft copy with all states and transitions
   - Preserves all virtual node positions and properties
5. Returns the newly published version (status=`PUBLISHED`)

---

## Workflow Transition Endpoints

### GET /workflows/{id}/transitions/
Lists all transitions for a workflow.

**Permissions**: Requires `view` permission on `WorkflowDefinition` models.

**Response**:
```json
[
  {
    "name": "string",
    "description": "string",
    "from_state": "state_name",
    "to_state": "state_name",
    "from_undefined_only": false,
    "source_handle": "left",
    "target_handle": "right",
    "properties": {}
  }
]
```

### GET /workflows/{id}/state-counts/
Returns entity counts per state for fields using this workflow.

**Permissions**: Requires `view` permission on `WorkflowDefinition` models.

**Response**:
```json
{
  "state_name_1": 10,
  "state_name_2": 5,
  "state_name_3": 0
}
```

**Purpose**: Shows how many entities exist in each workflow state, useful for understanding workflow adoption and identifying unused states.
