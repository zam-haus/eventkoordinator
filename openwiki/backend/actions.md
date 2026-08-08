---
type: backend_documentation
title: Policy Actions System
description: Documentation for the policy actions system (actions.py)
---

# Policy Actions System

The policy actions system provides a mechanism for Rego policies to trigger actions in the Django application.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Policy Evaluation Engine](policy_engine.md) - Rego policy evaluation details
- [Backend Overview](overview.md) - Backend components overview

## Overview

Actions are declared by Rego policies as structured output, validated by Pydantic, and dispatched to handlers registered with `@policy_action`.

## Architecture

### ActionContext

The `ActionContext` class provides context for action execution.

```python
class ActionContext(BaseModel):
    """Immutable context threaded through a single action dispatch chain.
    
    Use `ctx.model_copy(update={...})` to derive a modified context for
    recursive calls (e.g. when transitioning a child node).
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    node: Any
    """The node on which the triggering event occurred (entity or submodel)."""
    
    user: Any
    """The OpenIDUser who initiated the triggering event."""
    
    trigger: Literal["save", "create", "transition"]
    """Which lifecycle event produced this context."""
    
    phase: Literal["pre", "post"]
    """Current dispatch phase — pre runs before validation, post after."""
    
    edit_group: Any | None = None
    """EditGroup for the current transaction, shared across recursive calls."""
    
    visited_transitions: frozenset = frozenset()
    """(node_id_str, field_slug, transition_name) keys already visited this chain.
    Prevents infinite loops in TriggerTransitionOutput chains."""
    
    depth: int = 0
    """Recursion depth — raised by 1 for each nested trigger_transition call."""
```

### Action Context Properties

**node**: The node being acted upon. This can be:
- The root entity (for entity-level actions)
- A submodel (for submodel-specific actions)

**user**: The user who initiated the triggering event. This is the user making the API request.

**trigger**: The event type:
- `save`: Entity field changes
- `create`: New entity creation
- `transition`: Workflow transition

**phase**: Execution phase:
- `pre`: Runs before validation (use for preparatory work)
- `post`: Runs after validation (use for notifications, external calls)

**edit_group**: Shared transaction context for all actions in a chain.

**visited_transitions**: Set of `(node_id_str, field_slug, transition_name)` tuples to prevent infinite loops.

**depth**: Recursion depth counter. Maximum is 10 to prevent stack overflow.

## Action Output Schemas

### SetFieldValueOutput

Sets a field value on the current node or a descendant submodel.

```python
class SetFieldValueOutput(BaseModel):
    """Set a field value on the current node or a descendant submodel."""
    
    type: Literal["set_field_value"]
    phase: Literal["pre", "post"]
    field_path: str = Field(description="Dot-notation path to the target field")
    value: Any = Field(description="Value to write; None clears the field")
```

**Field Path Formats**:
- `"slug"` — scalar field on the triggering node
- `"select_slug.child_slug"` — field on the `submodel_select` child node
- `"list_slug[*].child_slug"` — field on **all** `submodel_list` children (iterates over all list items)

**Example**:
```json
{
  "type": "set_field_value",
  "phase": "pre",
  "field_path": "auto_status",
  "value": "pending_review"
}
```

### TriggerTransitionOutput

Triggers a named workflow transition synchronously.

```python
class TriggerTransitionOutput(BaseModel):
    """Trigger a named workflow transition synchronously.
    
    The transition runs to completion — including its own pre/post actions —
    before the next post-action in the current chain continues. A cycle guard
    (ActionContext.visited_transitions) and depth cap (10) prevent infinite
    recursion.
    """
    
    type: Literal["trigger_transition"]
    phase: Literal["pre", "post"]
    field_slug: str = Field(description="Slug of the WORKFLOW-type FieldDefinition")
    transition_name: str = Field(description="Name of the WorkflowTransition to execute")
    target_scope: Literal["self", "children", "all_descendants"] = Field(
        default="self",
        description=(
            "Which nodes to trigger the transition on. "
            "'self' = ctx.node; 'children' = direct children; "
            "'all_descendants' = entire subtree excluding ctx.node"
        ),
    )
    target_parent_field: str | None = Field(
        default=None,
        description=(
            "When set, only children attached via this parent_field slug are targeted. "
            "Use this to restrict 'children'/'all_descendants' to a specific submodel_list "
            "(e.g. 'reviews') and avoid hitting unrelated submodels."
        ),
    )
```

**Target Scope**:
- `"self"`: Trigger on the current node only
- `"children"`: Trigger on direct children only
- `"all_descendants"`: Trigger on all descendants (excludes the current node)

**Target Parent Field**: When set, restricts the target to children attached via this parent field slug (for submodel_list).

### SendNotificationOutput

Enqueues an email notification.

```python
class SendNotificationOutput(BaseModel):
    """Enqueue an email notification."""
    
    type: Literal["send_notification"]
    phase: Literal["pre", "post"]
    subject: str = Field(default="", description="Email subject line")
    template_name: str = Field(
        default="",
        description=(
            "Base template path (without suffix). The handler will render "
            "{template_name}.txt.j2 and {template_name}.html.j2."
        ),
    )
    body_text: str = Field(default="", description="Plain text email body")
    body_html: str = Field(default="", description="HTML email body")
    recipient_user_ids: list[UUID] = Field(
        default_factory=list,
        description="List of user IDs to notify. If empty, use default recipients."
    )
```

## Action Registration

### Registering Actions

Actions are registered using the `@policy_action` decorator.

```python
from userdefinedmodel.actions import policy_action
from pydantic import BaseModel
from typing import Literal

class MyOutput(BaseModel):
    type: Literal["my_action"]
    phase: Literal["pre", "post"]
    channel: str

@policy_action("my_action", schema=MyOutput)
def handle_my_action(action: MyOutput, ctx: ActionContext) -> None:
    post_to_channel(action.channel, ctx.node)
```

**Registration Process**:
1. Define an output Pydantic model with a `type` literal field
2. Register with `@policy_action("type_name", schema=MyOutput)`
3. Registration happens at Django app startup (AppConfig.ready)
4. No database migrations required

### Built-in Actions

The system includes several built-in actions:

1. **set_field_value**: Sets a field value
2. **trigger_transition**: Triggers a workflow transition
3. **send_notification**: Sends an email notification

### Custom Actions

To add custom actions:

```python
from userdefinedmodel.actions import policy_action
from pydantic import BaseModel, Field
from typing import Literal
import logging

logger = logging.getLogger(__name__)

class ExternalSyncOutput(BaseModel):
    """Sync entity to external system."""
    type: Literal["external_sync"]
    phase: Literal["pre", "post"]
    system: str = Field(description="External system to sync to")
    
    @policy_action("external_sync", schema=ExternalSyncOutput)
    def handle_external_sync(action: ExternalSyncOutput, ctx: ActionContext) -> None:
        if action.phase == "post":
            # Only sync after transaction commits
            sync_to_external_system(ctx.node, action.system)
            logger.info(f"Synced entity {ctx.node.id} to {action.system}")
```

## Action Execution Flow

### Pre-Phase Actions

Actions with `phase="pre"` are executed **before** validation.

**Use Cases**:
- Set default values
- Pre-populate calculated fields
- Normalize data
- Set derived field values

**Characteristics**:
- Runs before `entity.validate_for_save()`
- Can be rolled back on validation failure
- Should not depend on other actions (execution order not guaranteed)

### Post-Phase Actions

Actions with `phase="post"` are executed **after** validation.

**Use Cases**:
- Send notifications
- Trigger external systems
- Log changes
- Update external systems

**Characteristics**:
- Runs after validation and state changes
- Uses `transaction.on_commit()` to ensure execution only on successful commit
- Can depend on persisted state

### Transition Actions

For workflow transitions, the flow is:

1. **Pre-actions**: Execute all pre-phase actions
2. **Validation**: Run `entity.validate_for_save()`
3. **State Change**: Update workflow state
4. **History**: Record transition in FieldEdit
5. **Post-actions**: Execute all post-phase actions

### Depth Limit

The recursion depth is capped at 10 to prevent stack overflow:

```python
def dispatch_actions(actions, ctx: ActionContext) -> None:
    if ctx.depth >= 10:
        raise PolicyError("Maximum recursion depth exceeded")
    # ... dispatch logic ...
```

## Recursion Prevention

### visited_transitions

The `visited_transitions` set prevents infinite loops in `TriggerTransitionOutput` chains.

**Format**: `frozenset` of `(node_id_str, field_slug, transition_name)` tuples

**Example**:
```python
# When triggering a transition, check if already visited:
transition_key = (str(ctx.node.id), field_slug, transition_name)
if transition_key in ctx.visited_transitions:
    return  # Skip to prevent infinite loop

# Add to visited set for recursive calls:
new_visited = ctx.visited_transitions | {(transition_key)}
new_ctx = ctx.model_copy(update={"visited_transitions": new_visited})
```

### Infinite Loop Prevention

The system prevents infinite loops through:

1. **visited_transitions**: Tracks which transitions have been triggered in this chain
2. **depth limit**: Max recursion depth of 10 prevents stack overflow
3. **Action ordering**: Pre-actions run first, then post-actions (no circular dependencies)

## Pre-Phase vs Post-Phase Execution

### Pre-Phase

**When**: Before `entity.validate_for_save()`
**Purpose**: Prepare data for validation
**Can Roll Back**: Yes, on validation failure
**Use Cases**:
- Set calculated default values
- Normalize input data
- Set derived field values

**Example Policy**:
```rego
default allow = false

update if {
    input.trigger == "update"
    input.entity.status == "submitted"
    action := {
        "type": "set_field_value",
        "phase": "pre",
        "field_path": "reviewed_at",
        "value": now()
    }
    data.udm.result.actions[_] := action
}
```

### Post-Phase

**When**: After `entity.validate_for_save()` and `transaction.on_commit()`
**Purpose**: Notify external systems
**Can Roll Back**: No, uses transaction.on_commit()
**Use Cases**:
- Send notifications
- Trigger external API calls
- Log changes

**Example Policy**:
```rego
default allow = false

transition if {
    input.trigger == "transition"
    input.transition_name == "publish"
    action := {
        "type": "send_notification",
        "phase": "post",
        "subject": "Content published",
        "template_name": "notifications/published",
        "recipient_user_ids": [input.user.id]
    }
    data.udm.result.actions[_] := action
}
```

## Transaction Handling

### Single Transaction

All actions for a single request run within the same `transaction.atomic()` block:

```python
with transaction.atomic():
    # ... apply policy ...
    result = evaluate_policy(...)
    if result.allow:
        pre_ctx = ActionContext(...)
        dispatch_actions(result.actions, pre_ctx)
        post_ctx = pre_ctx.model_copy(update={"phase": "post"})
        dispatch_actions(result.actions, post_ctx)
```

### Post-Phase with transaction.on_commit()

Post-phase actions are wrapped in `transaction.on_commit()`:

```python
def dispatch_actions(actions, ctx: ActionContext) -> None:
    if ctx.phase == "post":
        transaction.on_commit(lambda: _dispatch_actions(actions, ctx))
    else:
        _dispatch_actions(actions, ctx)
```

This ensures:
- Post-actions only run if the transaction commits
- Post-actions don't block the request
- No partial execution on rollback

## Error Handling

### Action Errors

Actions can raise errors to abort the transaction:

```python
def handle_external_sync(action: ExternalSyncOutput, ctx: ActionContext) -> None:
    if not ctx.user.is_staff:
        raise PolicyError("External sync only allowed for staff users")
    # ... sync logic ...
```

### PolicyError

```python
class PolicyError(Exception):
    """Raised when policy blocks an action."""
    def __init__(self, messages: list):
        super().__init__("Action blocked by policy")
        self.messages = messages
```

### Error Propagation

Errors in actions:
- **Pre-phase**: Cause `PolicyError`, rollback transaction, return 422
- **Post-phase**: Logged but don't rollback (already committed)

## Example Policies

### Simple Notification Policy

```rego
package udm

default allow = false

# Create policy
create if {
    input.trigger == "create"
    input.user.is_staff
    action := {
        "type": "send_notification",
        "phase": "post",
        "subject": "New entity created",
        "template_name": "notifications/new_entity",
        "recipient_user_ids": ["admin-user-id"]
    }
    data.udm.result.actions[_] := action
}
```

### Conditional Field Update

```rego
package udm

default allow = false

# Update policy
update if {
    input.trigger == "update"
    input.entity.status == "submitted"
    action := {
        "type": "set_field_value",
        "phase": "pre",
        "field_path": "auto_status",
        "value": "pending_review"
    }
    data.udm.result.actions[_] := action
}
```

### Workflow Trigger

```rego
package udm

default allow = false

# Transition policy
transition if {
    input.trigger == "transition"
    input.transition_name == "submit"
    action := {
        "type": "trigger_transition",
        "phase": "post",
        "field_slug": "workflow_field",
        "transition_name": "notify_admin",
        "target_scope": "children",
        "target_parent_field": "reviews"
    }
    data.udm.result.actions[_] := action
}
```

### Deep Recursion Example

```rego
package udm

default allow = false

# Transition policy with recursive triggering
transition if {
    input.trigger == "transition"
    input.transition_name == "approve"
    action := {
        "type": "trigger_transition",
        "phase": "post",
        "field_slug": "approval_workflow",
        "transition_name": "notify_all",
        "target_scope": "all_descendants",
        "target_parent_field": "revisions"
    }
    data.udm.result.actions[_] := action
}
```

## Testing Actions

### Unit Tests

```python
class ActionTests(TestCase):
    def test_set_field_value_action(self):
        # Arrange
        ctx = ActionContext(
            node=entity,
            user=user,
            trigger="create",
            phase="pre"
        )
        action = SetFieldValueOutput(
            type="set_field_value",
            phase="pre",
            field_path="slug",
            value="value"
        )
        
        # Act
        handle_set_field_value(action, ctx)
        
        # Assert
        self.assertEqual(entity.field_values.get(slug="slug").value, "value")
```

### Integration Tests

```python
class PolicyActionIntegrationTests(TestCase):
    def test_policy_triggers_action(self):
        # Arrange
        entity = EntityFactory()
        policy = PolicyFactory()
        
        # Act
        result = evaluate_policy(entity, user, "create")
        
        # Assert
        self.assertTrue(result.allow)
        self.assertEqual(len(result.actions), 1)
        self.assertEqual(result.actions[0].type, "send_notification")
```

## Performance Considerations

### Optimizations

1. **Batch Actions**: Process multiple actions in a single dispatch
2. **Caching**: Cache action handlers (already done via decorator)
3. **Async Processing**: Use Celery for expensive actions (post-phase only)
4. **Selective Dispatch**: Only dispatch actions when needed

### Monitoring

- Action execution time
- Error rates
- Recursion depth
- Action counts per request

## Best Practices

### Action Design

1. **Idempotent Actions**: Actions should be safe to retry
2. **Minimal Side Effects**: Minimize external dependencies
3. **Clear Error Messages**: Provide helpful error messages
4. **Logging**: Log action execution for debugging

### Policy Design

1. **Simple Policies**: Keep policies simple and focused
2. **Composable Actions**: Design actions to be composable
3. **Error Handling**: Handle errors in policies
4. **Testing**: Test policies with various inputs

## Troubleshooting

### Common Issues

1. **Infinite Recursion**
   - Check `visited_transitions` is being updated
   - Verify `depth` is incremented in recursive calls
   - Consider reducing target_scope from `all_descendants` to `children`

2. **Action Not Triggered**
   - Verify action schema matches Rego output
   - Check `@policy_action` decorator registration
   - Review policy Rego syntax (use Rego linter)

3. **Error in Action**
   - Check action handler code
   - Review error messages
   - Debug with logging

### Debugging

**Enable debug logging**:
```python
logger = logging.getLogger("userdefinedmodel.actions")
logger.setLevel(logging.DEBUG)
```

**Check visited_transitions**:
```python
logger.debug("visited_transitions: %s", ctx.visited_transitions)
logger.debug("current depth: %d", ctx.depth)
```

## Summary

**Key Points**:
1. **Pre-phase**: Runs before validation, can rollback
2. **Post-phase**: Runs after commit, cannot rollback
3. **visited_transitions**: Prevents infinite loops
4. **depth limit**: Max 10 to prevent stack overflow
5. **transaction.atomic()**: All actions in single transaction
6. **transaction.on_commit()**: Post-actions run only on success
