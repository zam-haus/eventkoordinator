---
type: api_documentation
title: Entity API Documentation
description: Entity CRUD and workflow API documentation
---

# Entity API

The entity API provides endpoints for managing user-defined entities, including CRUD operations and workflow transitions.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [API Overview](udm_overview.md) - API overview and architecture
- [Policy Evaluation Engine](../backend/policy_engine.md) - Policy evaluation details

## Entity Endpoints

### GET /entities/

Lists entities for a specific UDM type, filtered by user permissions.

**Permissions**: Requires `view` permission on `UserDefinedModelEntity` models.

**Query Parameters**:
- `type_id` (required): UUID of the UDM type
- `page_size` (optional): Page size, default 200, max 200

**Response**:
```json
[
  {
    "id": "uuid",
    "type_id": "uuid",
    "config_version_id": "uuid",
    "field_values": [...],
    "children": [...],
    "workflow_state": "string",
    "policy_messages": [...]
  }
]
```

### GET /entities/{id}/

Retrieves a specific entity by ID.

**Permissions**: Object-level view authorization. The entity's policy "view" allow decision gates whether the entity is visible at all.

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

**Status Codes**:
- `200 OK`: Entity found and user has view permission
- `403 Forbidden`: Access denied by policy (with `policy_messages` explaining why)
- `404 Not Found`: Entity not found (or access denied with no messages)

**Object-Level Authorization**: The policy "view" evaluation determines visibility. If the policy denies view, the entity returns 404 to avoid leaking existence.

### POST /entities/

Creates a new entity.

**Permissions**: Requires `add` permission on `UserDefinedModelEntity` models.

**Request**:
```json
{
  "user_defined_model_type_id": "uuid",
  "field_values": [...]
}
```

**Response**: `201 Created`

**Error Responses**:
- `404 Not Found`: UDMType or config version not found
- `400 Bad Request`: Missing required fields or invalid request structure
- `422 Unprocessable Entity`: Policy validation errors with `policy_messages`

**Status Code Behavior**:
- `201 Created`: Entity created successfully
- `404`: Configuration not found (UDMType or published version)
- `422`: Policy denied creation with `policy_messages` explaining why

**Transaction Behavior**:
```python
with transaction.atomic():
    entity = UserDefinedModelEntity.objects.create(...)
    entity.materialize_defaults()
    entity.materialize_user_defaults(request.user)
    result = evaluate_policy(entity, request.user, "create", ...)
    if not result.allow:
        raise PolicyError(result.messages)
    pre_ctx = ActionContext(...)
    dispatch_actions(result.actions, pre_ctx)
    post_ctx = pre_ctx.model_copy(update={"phase": "post"})
    dispatch_actions(result.actions, post_ctx)
```

### PATCH /entities/{id}/

Updates an entity. Uses optimistic locking with `select_for_update(nowait=True)` to prevent concurrent modification.

**Permissions**: Object-level update authorization. The entity's policy "change" decision gates whether the update is allowed.

**Request**:
```json
{
  "changed_fields": [...]
}
```

**Response**: `200 OK` with updated entity

**Error Responses**:
- `400 Bad Request`: Validation errors in field values
- `409 Conflict`: Concurrent modification detected (via `OperationalError`)
- `422 Unprocessable Entity`: Policy validation errors with `policy_messages`

**Status Codes**:
- `409`: Returned when `OperationalError` occurs due to concurrent modification
- `422`: Returned for policy errors with `policy_messages`

**Transaction Atomicity**: The entire update (patch application, policy evaluation, action dispatch) runs in a single transaction. On `PolicyError`, the entire transaction is rolled back.

### DELETE /entities/{id}/

Deletes an entity. Requires explicit policy "delete" permission.

**Permissions**: Object-level delete authorization. The entity's policy "delete" decision gates deletion.

**Status Codes**:
- `204 No Content`: Entity deleted successfully
- `403 Forbidden`: Delete denied by policy
- `404 Not Found`: Entity not found

**Deletion Process**:
1. Check `select_for_update` lock (concurrent modification detection)
2. Evaluate "delete" policy (default-deny: no policy = no delete)
3. If allowed, delete entity
4. Transaction wraps the entire operation

### POST /entities/{id}/transition/

Applies pending edits and executes a workflow transition **atomically**.

**Atomicity Guarantee**: The transition and any pending edits are committed or rolled back together. The policy evaluates the patched (not-yet-committed) state against the persisted (pre-patch) snapshot.

**Request**:
```json
{
  "field": "workflow_field_slug",
  "transition": "transition_name",
  "changed_fields": [...]
}
```

**Response**: `200 OK` with updated entity and `policy_messages`

**Error Responses**:
- `400 Bad Request`: Invalid request format
- `404 Not Found`: Entity not found
- `409 Conflict`: Concurrent modification detected
- `422 Unprocessable Entity`: Policy errors with `policy_messages`
- `409 Conflict`: Invalid transition state (from_state mismatch)

**Transition Atomicity**:
1. Lock the root entity with `select_for_update(nowait=True)`
2. Snapshot the persisted state before patching
3. Apply pending edits in the same transaction
4. Evaluate transition policy using both states (old for context, new for evaluation)
5. Execute transition (pre-actions → validation → state change → post-actions)
6. If any step fails, the entire transaction rolls back

### POST /entities/{entity_id}/validation-preview/

Applies pending edits in a rolled-back transaction and returns the save verdict.

**Purpose**: Validates edits without committing them, useful for previewing changes.

**Response**:
```json
{
  "save": {"valid": true, "errors": {}},
  "messages": [...],
  "nodes": {
    "node_id": {
      "current_state": "state_name",
      "transitions": {"transition_name": {...}, ...}
    }
  }
}
```

**Atomicity**: Uses `transaction.atomic()` with `set_rollback(True)` to ensure no changes persist.

## PolicyError Exception Handling

### PolicyError Class

```python
class PolicyError(Exception):
    """Raised when policy blocks a save. Carries the full messages list."""
    def __init__(self, messages: list):
        super().__init__("Save blocked by policy")
        self.messages = messages
```

### Exception Flow

```python
try:
    with transaction.atomic():
        # ... evaluate policy ...
        if not result.allow:
            raise PolicyError(result.messages)
        # ... dispatch actions ...
except PolicyError as e:
    return JsonResponse({"policy_messages": e.messages}, status=422)
```

### Behavior
- `PolicyError` is caught at the API level
- It carries `messages` with detailed policy feedback
- HTTP 422 is returned with `policy_messages` in the response body
- The transaction is rolled back automatically due to the exception

### Message Format

```json
{
  "policy_messages": [
    {
      "level": "error",
      "text": "Cannot transition to 'published' without manager approval",
      "highlight_fields": ["manager_approval"]
    }
  ]
}
```

## Transaction Atomicity

### transaction.atomic() Behavior

**All entity operations use `transaction.atomic()`** to ensure ACID compliance:

1. **create_entity**: Creates entity, materializes defaults, evaluates policy, dispatches actions - all in one transaction
2. **patch_entity**: Updates entity, applies patch, evaluates policy, dispatches actions - all in one transaction
3. **delete_entity**: Locks entity, evaluates delete policy, deletes - all in one transaction
4. **transition_entity**: Locks entity, snapshots state, applies patch, evaluates policy, executes transition - all in one transaction

### Rollback on PolicyError

When `PolicyError` is raised:
1. The transaction is automatically rolled back (due to exception)
2. No database changes persist
3. The API returns 422 with `policy_messages`
4. The caller can retry with corrected data

### Concurrent Modification Detection

```python
try:
    entity = (UserDefinedModelEntity.objects
              .select_for_update(nowait=True, of=("self",))
              .get(id=entity_id))
except OperationalError:
    return _http409_concurrent()  # 409 Conflict
```

- `select_for_update(nowait=True)` raises `OperationalError` if another transaction holds the lock
- This returns 409 Conflict to the client
- The client can retry the operation

## Policy Messages vs Error Responses

### Policy Messages (Structured Feedback)

Policy messages are **structured feedback** from the policy engine:

```json
{
  "policy_messages": [
    {"level": "error", "text": "...", "highlight_fields": ["field1"]},
    {"level": "warning", "text": "...", "highlight_fields": []}
  ]
}
```

**Use Cases**:
- Policy denies action with explanation
- Workflow transition denied with conditions
- Validation errors with field highlights

### Standard Errors

Standard errors are **validation errors** or **system errors**:

```json
{
  "errors": {
    "field_name": ["Error message 1", "Error message 2"]
  }
}
```

**Use Cases**:
- Form field validation failures
- Invalid request format
- Database constraint violations

### Response Examples

**Policy Error (422)**:
```json
{
  "policy_messages": [
    {
      "level": "error",
      "text": "Cannot transition to 'published' without manager approval",
      "highlight_fields": ["manager_approval"]
    }
  ]
}
```

**Validation Error (400)**:
```json
{
  "errors": {
    "name": ["This field is required."],
    "email": ["Enter a valid email address."]
  }
}
```

**Concurrency Error (409)**:
```json
{
  "error": " Concurrent modification detected."
}
```

## Summary

**Key Points**:
1. **422 Status**: Used for policy errors with `policy_messages`
2. **409 Status**: Used for concurrent modification (`OperationalError`)
3. **Atomic Transactions**: All entity operations run in `transaction.atomic()`
4. **PolicyError**: Exception that triggers rollback and returns 422 with messages
5. **Deny-by-default**: Missing policies return 404 (no existence leak)
6. **Transaction Atomicity**: Policy errors rollback the entire transaction
