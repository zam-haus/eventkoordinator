# Policy Action Types

Actions are declared by Rego policies as structured JSON objects in the `data.udm.actions` rule output.  Each object must include a `type` discriminator and a `phase` (`"pre"` or `"post"`).  The engine dispatches each action to its registered handler after validating the object against the schema shown below.

---

## `send_notification`

Enqueue an email notification.

In ``post`` phase the send is deferred to ``transaction.on_commit`` so the
mail is only queued if the surrounding transaction commits successfully.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `'send_notification'` | yes |  |
| `phase` | `'pre' | 'post'` | yes |  |
| `recipients_config` | `list[Any]` | no | Recipient config dicts (same structure as mailqueue) |
| `subject_template` | `str` | no | Subject template string |
| `body_template` | `str` | no | Body template string |

**Example Rego output:**

```json
{
  "type": "send_notification",
  "phase": "pre",
  "recipients_config": [],
  "subject_template": "",
  "body_template": ""
}
```

## `set_field_value`

Set a field value on the current node or a descendant submodel.

``field_path`` formats:

* ``"slug"`` — scalar field on the triggering node
* ``"select_slug.child_slug"`` — field on the ``submodel_select`` child node
* ``"list_slug[*].child_slug"`` — field on **all** ``submodel_list`` children

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `'set_field_value'` | yes |  |
| `phase` | `'pre' | 'post'` | yes |  |
| `field_path` | `str` | yes | Dot-notation path to the target field |
| `value` | `Any` | yes | Value to write; None clears the field |

**Example Rego output:**

```json
{
  "type": "set_field_value",
  "phase": "pre",
  "field_path": "<field_path>",
  "value": null
}
```

## `trigger_transition`

Trigger a named workflow transition synchronously.

The transition runs to completion — including its own pre/post actions —
before the next post-action in the current chain continues.  A cycle guard
(``ActionContext.visited_transitions``) and depth cap (10) prevent infinite
recursion.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `'trigger_transition'` | yes |  |
| `phase` | `'pre' | 'post'` | yes |  |
| `field_slug` | `str` | yes | Slug of the WORKFLOW-type FieldDefinition |
| `transition_name` | `str` | yes | Name of the WorkflowTransition to execute |
| `target_scope` | `'self' | 'children' | 'all_descendants'` | no | Which nodes to trigger the transition on. 'self' = ctx.node; 'children' = direct children; 'all_descendants' = entire subtree excluding ctx.node |

**Example Rego output:**

```json
{
  "type": "trigger_transition",
  "phase": "pre",
  "field_slug": "<field_slug>",
  "transition_name": "<transition_name>",
  "target_scope": "self"
}
```

