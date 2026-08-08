---
type: api_documentation
title: Policy API Documentation
description: Policy management API documentation
---

# Policy API

The policy API provides endpoints for managing Rego policies and evaluating them.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Policy Evaluation Engine](../backend/policy_engine.md) - Policy evaluation details
- [Backend Overview](../backend/overview.md) - Backend components

## Policy Endpoints

### GET /policies/
Lists all policies accessible to the current user.

**Permissions**: Requires `view` permission on `Policy` models.

**Response**:
```json
[
  {
    "slug": "string",
    "source": "string"
  }
]
```

### GET /policies/{slug}/
Retrieves a specific policy by slug.

**Permissions**: Requires `view` permission on `Policy` models.

**Response**:
```json
{
  "slug": "string",
  "source": "string"
}
```

### POST /policies/
Creates a new policy.

**Permissions**: Requires `add` permission on `Policy` models.

**Request**:
```json
{
  "slug": "string",
  "source": "string"
}
```

**Response**: `201 Created`

### PUT /policies/{slug}/
Updates a policy.

**Permissions**: Requires `change` permission on `Policy` models.

**Request**:
```json
{
  "source": "string"
}
```

**Response**: `200 OK`

### DELETE /policies/{slug}/
Deletes a policy.

**Permissions**: Requires `delete` permission on `Policy` models.

**Error Responses**:
- `400 Bad Request`: Policy is assigned to UDMTypes
- `404 Not Found`: Policy not found

### GET /policies/{slug}/source/
Retrieves the policy source code.

**Permissions**: Requires `view` permission on `Policy` models.

**Response**:
```json
{
  "slug": "string",
  "source": "string"
}
```

### POST /policies/evaluate/
Evaluates policies on a specific entity.

**Request**:
```json
{
  "entity_id": "uuid",
  "action": "string"
}
```

**Response**:
```json
{
  "allow": true,
  "messages": [...],
  "actions": [...]
}
```

### POST /types/{id}/evaluate/
Evaluates type policies for a UDM type.

**Request**:
```json
{
  "action": "string"
}
```

**Response**:
```json
{
  "allow": true,
  "messages": [...],
  "actions": [...]
}
```

## Policy Evaluation Flow

### 1. Input Schema
The policy evaluation uses a specific input schema defined in `userdefinedmodel.policy_input`. The schema includes:
- User information
- Group membership
- Entity data
- Linked entities

### 2. Rego Session
The Rego session is compiled once and cached per type:
- Uses `regorus` engine for evaluation
- Thread-local caching due to PyO3 restrictions
- Source hash for cache invalidation

### 3. Evaluation Results
Policy evaluation returns:
- `allow`: Boolean indicating whether the action is allowed
- `messages`: Array of messages with level and text
- `actions`: Array of action outputs (set_field_value, trigger_transition, send_notification)

## Policy Actions

### set_field_value
Sets a field value on the current node or a descendant submodel.

```json
{
  "type": "set_field_value",
  "phase": "pre" | "post",
  "field_path": "string",
  "value": "any"
}
```

### trigger_transition
Triggers a workflow transition synchronously.

```json
{
  "type": "trigger_transition",
  "phase": "pre" | "post",
  "field_slug": "string",
  "transition_name": "string",
  "target_scope": "self" | "children" | "all_descendants",
  "target_parent_field": "string"
}
```

### send_notification
Enqueues an email notification.

```json
{
  "type": "send_notification",
  "phase": "pre" | "post",
  "subject": "string",
  "template_name": "string",
  "body_text": "string",
  "body_html": "string"
}
```

## Notes

- All policy operations use Django's transaction management
- Policy evaluation happens before entity changes
- Actions are dispatched in pre/post phases
- Error handling uses `PolicyError` exceptions
