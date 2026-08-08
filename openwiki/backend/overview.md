---
type: backend_documentation
title: Backend Components Overview
description: Overview of backend components and architecture
---

# Backend Components

This section documents the backend components of the UDM system.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Policy Evaluation Engine](policy_engine.md) - Rego policy evaluation details
- [Actions System](actions.md) - Policy actions system

## Core Components

### 1. Policy Evaluation Engine (`userdefinedmodel/engine.py`)

The policy evaluation engine evaluates Rego policies against entities.

#### RegoSession
A compiled Rego engine over a fixed set of policy sources.

**Key Methods**:
- `eval_rule(engine, rule_path)`: Evaluate a rule and return parsed JSON
- `evaluate(input_doc, rule_path, *, gather_prints=False)`: One evaluation on a fresh clone

**Thread Safety**: PyO3 regorus engines are UNSENDABLE - they may only be used on the thread that created them. The cache is therefore thread-local.

**Cache**: 
- `_engine_cache()`: Returns the thread-local cache
- `_sources_hash(sources)`: Hash of policy sources for cache invalidation
- `get_session_for_type(udm_type)`: Return the cached compiled session for a type

### 2. Policy Input Schema (`userdefinedmodel/policy_input.py`)

The policy input schema defines the structure of data passed to Rego policies.

**Key Components**:
- Input versioning
- User serialization
- Group membership resolution
- Entity data serialization

**Schema Fields**:
- `version`: Input version string
- `user`: User document
- `groups`: Group membership
- `entities`: Entity data
- `linked_entities`: Linked entities (depth 1 by default)

### 3. Action System (`userdefinedmodel/actions.py`)

The action system provides a framework for policy actions.

#### ActionContext
Immutable context threaded through a single action dispatch chain.

**Fields**:
- `node`: The node on which the triggering event occurred
- `user`: The OpenIDUser who initiated the triggering event
- `trigger`: "save", "create", or "transition"
- `phase`: "pre" or "post"
- `edit_group`: EditGroup for the current transaction
- `visited_transitions`: Frozenset of visited transitions (prevents infinite loops)
- `depth`: Recursion depth (max 10)

#### Action Output Schemas

##### SetFieldValueOutput
Sets a field value on the current node or a descendant submodel.

**Fields**:
- `type`: "set_field_value"
- `phase`: "pre" or "post"
- `field_path`: Dot-notation path to target field
- `value`: Value to write (None clears the field)

##### TriggerTransitionOutput
Triggers a named workflow transition synchronously.

**Fields**:
- `type`: "trigger_transition"
- `phase`: "pre" or "post"
- `field_slug`: Slug of the WORKFLOW-type FieldDefinition
- `transition_name`: Name of the WorkflowTransition to execute
- `target_scope`: "self", "children", or "all_descendants"
- `target_parent_field`: Restrict to children attached to a specific submodel_list field

##### SendNotificationOutput
Enqueues an email notification.

**Fields**:
- `type`: "send_notification"
- `phase`: "pre" or "post"
- `subject`: Email subject line
- `template_name`: Base template path (e.g., "proposals/submit")
- `body_text`: Plain text body (alternative to template)
- `body_html`: HTML body (alternative to template)

### 4. Policy Evaluation Flow

The policy evaluation flow follows these steps:

1. **Input Validation**: Validate the policy input schema
2. **Rego Evaluation**: Evaluate the Rego policy
3. **Action Dispatch**: Dispatch any actions returned by the policy
4. **Transaction Commit**: Commit the transaction with all changes

**Pre/Post Phases**:
- Pre-phase: Runs before validation, can modify field values
- Post-phase: Runs after validation, triggers transitions and notifications

**Action Context**:
- ActionContext is threaded through the action dispatch chain
- Can be cloned and modified for recursive calls
- Prevents infinite loops via visited_transitions tracking

### 5. Management Commands

The system provides several management commands:

- `openapi_schema`: Generate OpenAPI schema
- `render_nginx_conf`: Render Nginx configuration
- `set_default_permissions`: Set default permissions for models

### 6. Models

#### UserDefinedModelType
The UDM type definition including:
- Field configuration
- Workflow definition
- Policy assignments

#### UserDefinedModelEntity
The entity instance including:
- Config version
- Field values
- Workflow state
- Children (for hierarchical entities)

#### ConfigVersion
The versioned configuration including:
- Draft status
- Published status
- Field definitions

#### WorkflowDefinition
The workflow definition including:
- States
- Transitions
- Initial state

#### Policy
The Rego policy including:
- Slug
- Source code

## API Endpoints

### /api/udm/ - UDM API

All endpoints are mounted under `/api/udm/` and use Django authentication.

### /api/v1/ - apiv1 API

The apiv1 API provides endpoints for:
- Series and events
- Proposals and reviews
- Speakers
- Sync targets
- Calendar
- Pricing
- Calls
- Export

## Middleware

### SudoModeMiddleware
Session-scoped sudo toggle for administrative operations.

### OIDC Session Refresh Middleware
Refreshes OIDC session tokens as needed.

## Error Handling

### ApiError
Custom exception for API errors that can be raised inside `transaction.atomic()` blocks.

### PolicyError
Exception for policy validation errors with messages.

### TransitionError
Exception for workflow transition errors with detailed information.
