---
type: api_documentation
title: API Endpoints Reference
description: Comprehensive documentation for all API endpoints
---

# API Endpoints Reference

This document provides comprehensive documentation for all API endpoints in the OpenWiki application.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [API Overview](udm_overview.md) - API overview and architecture
- [Backend Components](../backend/overview.md) - Backend components

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [API Overview](udm_overview.md) - API overview and architecture
- [Backend Components](../backend/overview.md) - Backend components

## Table of Contents

1. [UDM API Endpoints](#udm-api-endpoints)
   - [Configuration Endpoints](#configuration-endpoints)
   - [Entity Endpoints](#entity-endpoints)
   - [Policy Endpoints](#policy-endpoints)
   - [Workflow Endpoints](#workflow-endpoints)
   - [Bundle Endpoints](#bundle-endpoints)
   - [Staging Endpoints](#staging-endpoints)
   - [Autocomplete Endpoints](#autocomplete-endpoints)

2. [Sync Endpoints](#sync-endpoints)
   - [Sync Target Endpoints](#sync-target-endpoints)
   - [Sync Operations Endpoints](#sync-operations-endpoints)

3. [Auth Endpoints](#auth-endpoints)
   - [User Management Endpoints](#user-management-endpoints)
   - [Group Management Endpoints](#group-management-endpoints)
   - [Permission Endpoints](#permission-endpoints)
   - [Sudo Mode Endpoints](#sudo-mode-endpoints)

4. [OpenID Connect Endpoints](#openid-connect-endpoints)

---

## UDM API Endpoints

The UDM API is mounted at `/api/udm/` and provides endpoints for managing user-defined models.

### Configuration Endpoints

#### 1. List Configurations

**Endpoint**: `GET /configs/`

**Description**: Retrieve all configuration versions.

**Authentication**: Required

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "Configuration Name",
    "description": "Configuration description",
    "languages": [
      {
        "code": "en",
        "label": "English",
        "is_default": true,
        "sort_order": 0
      }
    ],
    "stale_entity_count": 0,
    "created_at": "2023-01-01T00:00:00Z",
    "updated_at": "2023-01-01T00:00:00Z"
  }
]
```

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied

#### 2. Create Configuration

**Endpoint**: `POST /configs/`

**Description**: Create a new configuration.

**Authentication**: Required

**Request Body**:
```json
{
  "name": "Configuration Name",
  "description": "Configuration description",
  "languages": [
    {
      "code": "en",
      "label": "English",
      "is_default": true,
      "sort_order": 0
    }
  ]
}
```

**Response**:
```json
{
  "id": "uuid",
  "name": "Configuration Name",
  "description": "Configuration description",
  "languages": [...],
  "stale_entity_count": 0
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `422`: Validation error

#### 3. Get Configuration

**Endpoint**: `GET /configs/{config_id}/`

**Description**: Retrieve a specific configuration.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Response**:
```json
{
  "id": "uuid",
  "name": "Configuration Name",
  "description": "Configuration description",
  "languages": [...],
  "stale_entity_count": 0
}
```

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration not found

#### 4. Update Configuration

**Endpoint**: `PATCH /configs/{config_id}/`

**Description**: Update a configuration.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Request Body**:
```json
{
  "name": "Updated Name",
  "description": "Updated description",
  "languages": [...]
}
```

**Response**:
```json
{
  "id": "uuid",
  "name": "Updated Name",
  "description": "Updated description",
  "languages": [...]
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration not found
- `422`: Validation error

#### 5. Delete Configuration

**Endpoint**: `DELETE /configs/{config_id}/`

**Description**: Delete a configuration.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Response**:
- `204`: No content

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration not found

### Draft Configuration Endpoints

#### 6. Replace Draft

**Endpoint**: `POST /configs/{config_id}/replace-draft/`

**Description**: Replace the draft version of a configuration.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Request Body**:
```json
{
  "field_definitions": [...],
  "field_configs": [...],
  "workflow_definitions": [...]
}
```

**Response**:
```json
{
  "id": "uuid",
  "name": "Configuration Name",
  "description": "Configuration description"
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration not found
- `422`: Validation error

#### 7. Publish Draft

**Endpoint**: `POST /configs/{config_id}/publish/`

**Description**: Publish a draft configuration version.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Request Body**:
```json
{
  "version_id": "uuid"
}
```

**Response**:
```json
{
  "id": "uuid",
  "status": "published"
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration or version not found
- `422`: Validation error

### Field Definition Endpoints

#### 8. List Field Definitions

**Endpoint**: `GET /configs/{config_id}/field-definitions/`

**Description**: List field definitions for a configuration.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Query Parameters**:
- `page_size` (int, default: 200): Page size
- `page` (int, default: 1): Page number

**Response**:
```json
{
  "field_definitions": [...],
  "total": 100,
  "page": 1,
  "page_size": 200
}
```

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration not found

#### 9. Create Field Definition

**Endpoint**: `POST /configs/{config_id}/field-definitions/`

**Description**: Create a new field definition.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID

**Request Body**:
```json
{
  "slug": "field_slug",
  "data_type": "string",
  "is_localized": false,
  "label": "Field Label",
  "help_text": "Field help text"
}
```

**Response**:
```json
{
  "id": "uuid",
  "slug": "field_slug",
  "data_type": "string",
  "is_localized": false,
  "label": "Field Label"
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration not found
- `422`: Validation error

#### 10. Update Field Definition

**Endpoint**: `PATCH /configs/{config_id}/field-definitions/{field_id}/`

**Description**: Update a field definition.

**Authentication**: Required

**Path Parameters**:
- `config_id` (UUID): Configuration ID
- `field_id` (UUID): Field definition ID

**Request Body**:
```json
{
  "label": "Updated Label",
  "help_text": "Updated help text"
}
```

**Response**:
```json
{
  "id": "uuid",
  "slug": "field_slug",
  "label": "Updated Label"
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Configuration or field definition not found
- `422`: Validation error

### Type Endpoints

#### 11. List Types

**Endpoint**: `GET /types/`

**Description**: List all UDM types.

**Authentication**: Required

**Response**:
```json
[
  {
    "id": "uuid",
    "name": "Type Name",
    "description": "Type description",
    "field_config_id": "uuid",
    "created_at": "2023-01-01T00:00:00Z",
    "updated_at": "2023-01-01T00:00:00Z"
  }
]
```

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied

#### 12. Create Type

**Endpoint**: `POST /types/`

**Description**: Create a new UDM type.

**Authentication**: Required

**Request Body**:
```json
{
  "name": "Type Name",
  "description": "Type description",
  "field_config_id": "uuid"
}
```

**Response**:
```json
{
  "id": "uuid",
  "name": "Type Name",
  "description": "Type description",
  "field_config_id": "uuid"
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `422`: Validation error

#### 13. Update Type

**Endpoint**: `PATCH /types/{type_id}/`

**Description**: Update a UDM type.

**Authentication**: Required

**Path Parameters**:
- `type_id` (UUID): Type ID

**Request Body**:
```json
{
  "name": "Updated Name",
  "description": "Updated description"
}
```

**Response**:
```json
{
  "id": "uuid",
  "name": "Updated Name",
  "description": "Updated description"
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Type not found
- `422`: Validation error

### Entity Endpoints

#### 14. List Entities

**Endpoint**: `GET /entities/`

**Description**: List entities for a UDM type.

**Authentication**: Required

**Query Parameters**:
- `type_id` (UUID): UDM type ID
- `page_size` (int, default: 200): Page size
- `page` (int, default: 1): Page number

**Response**:
```json
[
  {
    "id": "uuid",
    "user_defined_model_type_id": "uuid",
    "config_version_id": "uuid",
    "field_values": [...],
    "created_at": "2023-01-01T00:00:00Z",
    "updated_at": "2023-01-01T00:00:00Z"
  }
]
```

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied

#### 15. Get Entity

**Endpoint**: `GET /entities/{entity_id}/`

**Description**: Retrieve a specific entity.

**Authentication**: Required

**Path Parameters**:
- `entity_id` (UUID): Entity ID

**Response**:
```json
{
  "id": "uuid",
  "user_defined_model_type_id": "uuid",
  "config_version_id": "uuid",
  "field_values": [...],
  "created_at": "2023-01-01T00:00:00Z",
  "updated_at": "2023-01-01T00:00:00Z"
}
```

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Entity not found

#### 16. Create Entity

**Endpoint**: `POST /entities/`

**Description**: Create a new entity.

**Authentication**: Required

**Request Body**:
```json
{
  "user_defined_model_type_id": "uuid",
  "field_values": {
    "field_slug": "value"
  }
}
```

**Response**:
```json
{
  "id": "uuid",
  "user_defined_model_type_id": "uuid",
  "field_values": {...}
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `422`: Validation error

#### 17. Update Entity

**Endpoint**: `PATCH /entities/{entity_id}/`

**Description**: Update an entity.

**Authentication**: Required

**Path Parameters**:
- `entity_id` (UUID): Entity ID

**Request Body**:
```json
{
  "field_values": {
    "field_slug": "updated_value"
  }
}
```

**Response**:
```json
{
  "id": "uuid",
  "field_values": {...}
}
```

**Errors**:
- `400`: Invalid request body
- `401`: Not authenticated
- `403`: Permission denied
- `404`: Entity not found
- `422`: Validation error

#### 18. Delete Entity

**Endpoint**: `DELETE /entities/{entity_id}/`

**Description**: Delete an entity.

**Authentication**: Required

**Path Parameters**:
- `entity_id` (UUID): Entity ID

**Response**:
- `204`: No content

**Errors**:
- `401`: Not authenticated
- `403`: Permission denied
#### 19. Transition Entity

**Endpoint**: `POST /entities/{entity_id}/transition/`

**Description**: Apply pending edits and execute a workflow transition **atomically**. The transition and any pending edits are committed or rolled back together. The policy evaluates the patched (not-yet-committed) state against the persisted (pre-patch) snapshot.

**Authentication**: Required

**Path Parameters**:
- `entity_id` (UUID): Entity ID

**Request Body**:
```json
{
  "field": "workflow_field_slug",
  "transition": "transition_name",
  "changed_fields": [...]
}
```

**Response**:
```json
{
  "id": "uuid",
  "type_id": "uuid",
  "config_version_id": "uuid",
  "field_values": [...],
  "children": [...],
  "workflow_state": "string",
  "policy_messages": [...]
}
```

**Atomicity Guarantees**:
1. **Single Transaction**: The entire operation runs in one `transaction.atomic()` block
2. **Pre-snapshot**: The persisted (pre-patch) state is captured before any changes
3. **Policy Context**: The transition policy evaluates the new state against the old state as context
4. **Rollback on Failure**: Any failure (policy denial, validation error, exception) triggers rollback
5. **Action Dispatch**: Pre-actions run, then validation, then state change, then post-actions - all in one transaction

**Atomicity Flow**:
```
1. Lock root entity with select_for_update(nowait=True)
2. Snapshot persisted entity document (old_entity_doc)
3. If changed_fields present, apply patch to entity
4. Evaluate transition policy using old_entity_doc as context
5. If denied, raise PolicyError → transaction rollback
6. Dispatch pre-actions
7. Validate subtree (save rules floor)
8. Apply state change to workflow field
9. Record transition in history
10. Dispatch post-actions
11. Return entity with policy_messages
```

**Errors**:
- `400`: Invalid request body (missing required fields)
- `401`: Not authenticated
- `403`: Permission denied (no delete permission or entity doesn't exist)
- `404`: Entity not found or workflow field not found
- `409`: Concurrent modification (OperationalError from select_for_update)
- `422`: Policy denied transition or validation error with `policy_messages`

**Transition Descriptor Structure**:

The `transition_descriptor` in the input document contains:

```json
{
  "name": "transition_name",
  "from_state": {
    "id": "uuid",
    "name": "state_name",
    "sort_order": 1
  },
  "to_state": {
    "id": "uuid",
    "name": "target_state_name",
    "sort_order": 2
  },
  "from_undefined_only": false,
  "sort_order": 1,
  "config_version": "uuid"
}
```

**Field Transition Validation**:

Workflow transitions must validate against the workflow state machine:

1. **From State Check**: The field must be in the correct state for the transition
   - If `from_undefined_only` is true, the field must be null/undefined
   - If `from_state` is set, the field must be in that specific state
   - If no from_state is set, any state is allowed

2. **Workflow Field Type**: The field must be of type WORKFLOW

3. **Workflow Version Match**: The transition must belong to the field's workflow_version

4. **State Validity**: The target state must exist in the same workflow_version

**Transition Validation Example**:

```python
# Check from_state / from_undefined_only
if transition.from_undefined_only:
    if current_state is not None:
        raise TransitionError(
            f"Transition '{name}' only allowed from undefined state, but field '{field_slug}' is in '{current_state.name}'.",
            http_status=409,
        )
elif transition.from_state is not None:
    if current_state is None or current_state.id != transition.from_state_id:
        current_name = current_state.name if current_state else "None"
        raise TransitionError(
            f"Field '{field_slug}' is in state '{current_name}', but transition '{name}' requires '{transition.from_state.name}'.",
            http_status=409,
        )
```

**Workflow Field Validation Rules**:

The `single_field_rules` with `applies_to_transition` enforces:

1. **Transition-Specific Rules**: Rules can be marked `applies_to_transition: true` to apply only during transitions
2. **Field-Level Permissions**: Workflow field permissions are checked during transition
3. **State Validation**: Workflow state transitions must follow the defined state machine
4. **Policy Enforced**: All validation is performed via Rego policies, not just Python validation
