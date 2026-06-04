# Policy Action Types

Actions are declared by Rego policies as structured JSON objects in the `data.udm.actions` rule output.  Each object must include a `type` discriminator and a `phase` (`"pre"` or `"post"`).  The engine dispatches each action to its registered handler after validating the object against the schema shown below.

---

## `create_submodel_item`

Create a new item in a ``submodel_list`` field, optionally pre-seeded with field values.

Field values in ``fields`` may use these special interpolation markers
(replaced before the submodel item is written):

* ``"$$user.id"``       → ``str(ctx.user.id)``
* ``"$$user.email"``    → ``ctx.user.email``
* ``"$$user.username"`` → ``ctx.user.username``

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `'create_submodel_item'` | yes |  |
| `phase` | `'pre' | 'post'` | yes |  |
| `field_slug` | `str` | yes | Slug of the submodel_list FieldDefinition to append to |
| `fields` | `dict[str, Any]` | no | Initial field values for the new submodel item (supports $$ markers) |

**Example Rego output:**

```json
{
  "type": "create_submodel_item",
  "phase": "pre",
  "field_slug": "<field_slug>",
  "fields": {}
}
```

## `send_notification`

Enqueue an email notification.

In ``post`` phase the send is deferred to ``transaction.on_commit`` so the
mail is only queued if the surrounding transaction commits successfully.

**Template-based sending** (recommended to stay within Rego's 1 024-char
line limit): set ``template_name`` to a base path (e.g.
``"proposals/submit"``) and the handler will render
``{template_name}.txt.j2`` and ``{template_name}.html.j2`` via Django's
template loader.  Template context: ``node``, ``user``, ``trigger``.

**Inline sending**: leave ``template_name`` empty and provide
``body_text`` / ``body_html`` directly.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `'send_notification'` | yes |  |
| `phase` | `'pre' | 'post'` | yes |  |
| `subject` | `str` | no | Email subject line |
| `template_name` | `str` | no | Base template path (without suffix).  Renders <name>.txt.j2 and <name>.html.j2. |
| `body_text` | `str` | no | Plain-text body (used when template_name is empty) |
| `body_html` | `str` | no | HTML body (used when template_name is empty) |
| `recipient_field` | `str | None` | no | Slug of a user_select field on the triggering node whose user's email address is the primary recipient.  Stacked with extra_recipients. |
| `extra_recipients` | `list[str]` | no | Additional explicit email addresses to send to. |

**Example Rego output:**

```json
{
  "type": "send_notification",
  "phase": "pre",
  "subject": "",
  "template_name": "",
  "body_text": "",
  "body_html": "",
  "recipient_field": null,
  "extra_recipients": []
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

When ``target_scope`` is ``"children"`` or ``"all_descendants"``, use
``target_parent_field`` to restrict to children attached to a specific
``submodel_list`` field (e.g. ``"reviews"``).  Without it, all children
of the triggering node are targeted, including those that don't have the
workflow field — those are silently skipped.

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `'trigger_transition'` | yes |  |
| `phase` | `'pre' | 'post'` | yes |  |
| `field_slug` | `str` | yes | Slug of the WORKFLOW-type FieldDefinition |
| `transition_name` | `str` | yes | Name of the WorkflowTransition to execute |
| `target_scope` | `'self' | 'children' | 'all_descendants'` | no | Which nodes to trigger the transition on. 'self' = ctx.node; 'children' = direct children; 'all_descendants' = entire subtree excluding ctx.node |
| `target_parent_field` | `str | None` | no | When set, only children attached via this parent_field slug are targeted. Use this to restrict 'children'/'all_descendants' to a specific submodel_list (e.g. 'reviews') and avoid hitting unrelated submodels. |

**Example Rego output:**

```json
{
  "type": "trigger_transition",
  "phase": "pre",
  "field_slug": "<field_slug>",
  "transition_name": "<transition_name>",
  "target_scope": "self",
  "target_parent_field": null
}
```

