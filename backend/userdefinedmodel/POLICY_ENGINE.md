# Policy Engine — Input & Output Reference

Policies are [Rego](https://www.openpolicyagent.org/docs/latest/policy-language/) programs evaluated by the
real [OPA](https://www.openpolicyagent.org/) engine, embedded via
[opa-golib-python-bindings](https://github.com/phi1010/opa-golib-python-bindings) (`opa_bindings`). All rules
live under the `data.udm` package.

The authoritative, executable contract lives in
`documentation/configuration/policies/_input_schema.rego` (input shape, generated example documents,
`valid_input` predicate) and is mirrored in Python by `userdefinedmodel/policy_input.py`
(`validate_policy_input`, enforced by the engine on every input document it builds). Both are checked
against each other by `documentation/configuration/policies/check_input_schema.py`. This file is the prose
companion; where they disagree, the executable contract wins.

Current `input_version`: **1**.

> **Form tree / data field split (2026-08):** `FieldDefinition` was split into
> `DataField` (storage semantics) + `FormElement` (form tree / widget) +
> `FormElementBinding` (M:N). The Rego input contract is **unchanged**
> (shape-compatible, `input_version` stays 1): structural `FormElement`s are
> still emitted into `entity.fields` with `element_type` as `data_type`, so
> `structural.rego` / `config.STRUCTURAL_TYPES` keep working. A `DataField`\> with zero bindings is a hidden field; a `FormElement` may bind multiple
> data fields (e.g. `date_range`). `FieldDefinition` remains as a Python alias
> for `DataField` for backward compatibility. See
> `PLAN_split_form_tree_and_data_fields.md`.

---

## 1. Evaluation model

- **One evaluation per request, always on the ROOT entity document** — never per node. Submodel data is
  embedded in `entity.children`; per-node work (transition candidates, field grants) flows through maps
  keyed by node id.
- The engine reads exactly **one aggregate rule** per evaluation:
  - `data.udm.result` for entity actions (fixed schema for *every* action),
  - `data.udm.type_result` for `action == "public_type_fields"`.
- Compiled policies are cached: one `opa_bindings.OpaEngine` per `udm_type_id`, validated against a hash of
  the policy sources and replaced when it changes; the engine has no `clone()` — each evaluation calls
  `eval_document` directly on the shared, cached instance (`engine.RegoSession`).
- Default-deny: no UDMType, no attached policies, an undefined `result`, a malformed input document, or an
  evaluation error all yield the deny output.

| Action | Triggered by | Extra input keys |
|---|---|---|
| `view` | `GET /entities/{id}/`, history, entity responses | — |
| `browse` | `GET /entity-search/` (per candidate) | — |
| `create` | `POST /entities/` (after materializing defaults, inside the transaction) | — |
| `save` | `PATCH /entities/{id}/` (post-write, pre-commit) | `changed_fields`, `old_entity`, `additional_result` |
| `delete` | `DELETE /entities/{id}/` | — |
| `transition` | `POST /entities/{id}/transition/` (post-patch, pre-commit) | `transition`, `field`, `node_id`, `transition_descriptor`, `old_entity`, `additional_result` |
| `preview` | `POST /entities/{id}/validation-preview/` | `changed_fields`, `candidate_transitions`, `old_entity`, `additional_result` |
| `public_type_fields` | `GET /types/{id}/public-fields/` | minimal input: `input_version`, `action`, `locale`, `type_id`, `user` |

---

## 2. Input document

```json
{
  "input_version": 1,
  "action": "<one of the actions above>",
  "locale": "de",
  "type_id": "<uuid>",
  "entity":          { /* root NodeDocument, full tree in children */ },
  "old_entity":      { /* persisted pre-patch NodeDocument or null */ },
  "schemas":         { "<uuid>": { "slug", "fields", "properties" } },
  "users":           { "<uuid>": { /* UserDocument */ } },
  "groups":          { "<int-as-string>": { "id", "name", "member_ids" } },
  "linked_entities": { "<uuid>": { /* NodeDocument, one entity deep */ } },
  "files":           { "<uuid>": { "id", "original_name", "mime_type", "size_bytes", "image_width", "image_height" } },
  "user":            { /* requesting user, incl. "permissions" */ },
  "changed_fields":  { "<slug>": { "value": <scalar> } },
  "additional_result": { /* VIEW pre-check carry-over, {} when none ran */ }
}
```

Key rules:

- `locale` — the requesting user's locale for message-language selection; `null` **exactly** when the
  system calls itself (policy actions, background tasks). Policies must fall back to a default language.
- `type_id` — **never null**: policies attach to the type, so typeless entities are denied before Rego runs.
- `old_entity` — `null` exactly for `view`/`browse`/`delete`/`create`; **always** a NodeDocument for
  `save`/`transition`/`preview` (equal to the current state when nothing was patched). It always reflects
  **persisted** database state: callers that write inside the open transaction snapshot it *before* their
  writes; it is never derived from client-supplied data.
- Field values hold **raw PKs**; resolve details via the lookup maps:
  `input.users[input.entity.fields.owner.value]`, `input.groups[sprintf("%v", [gid])].member_ids`.
  There is no in-place expansion of user/group objects anywhere in the tree.
- `files` — metadata of `image`/`file` attachments referenced anywhere in the tree (and in linked
  entities), keyed by attachment PK: `input.files[input.entity.fields.photo.value].image_width`.
  `image_width`/`image_height` are `null` for non-images and files whose dimensions are unknown.
- `linked_entities` — `entity_select`/`entity_select_multi` targets resolved exactly **one entity deep**
  (`engine.LINKED_ENTITY_DEPTH`): the linked entity's own fields and submodel tree are included, its
  outgoing entity links stay raw PKs. User/group references inside linked entities are in the lookup maps.

### 2.1 NodeDocument

Root and submodel nodes have the **identical** shape; every node carries the UUID of its model schema:

```json
{
  "id": "<uuid>",
  "schema_id": "<uuid>",              // == config_version_id; key into input.schemas
  "config_version_id": "<uuid>",
  "config_id": "<uuid>",
  "type_id": "<uuid|null>",           // set on the root only
  "parent_field_slug": "<slug|null>",
  "fields":   { "<slug>": { "data_type", "localized", "value" } },
  "children": { "<slug>": [ <NodeDocument> ] },
  "created_at": "<ISO-8601>", "updated_at": "<ISO-8601>"
}
```

**Every** field defined on the node's config version appears in `fields` — unset fields have
`value: null` (`{}` when localized). Localized values are `{"<lang>": <scalar>}` dicts. Scalar encodings
are unchanged (dates/times ISO-8601, selects as configured choice strings, file/image as attachment UUIDs,
`workflow` as the current state name, `submodel_list` has no scalar — nodes live in `children`).

### 2.2 `changed_fields` (save / preview)

The raw submitted payload with each value wrapped as `{"value": <scalar>}` — same encoding as entity field
values. Includes `submodel_list` op arrays and `_`-prefixed control keys; `workflow` slugs never appear
(the writer rejects them before evaluation). All writes are flushed inside the open transaction before
evaluation, so `input.entity` reflects the **post-write** state and `input.old_entity` the persisted state.

### 2.3 Transition keys (`transition` action only)

`transition` (name), `field` (workflow field slug), `node_id` (node owning the field — may be a submodel),
and `transition_descriptor`:

```json
{ "from_state": "<state|null>", "to_state": "<state>", "from_undefined_only": false, "properties": { } }
```

`properties` is free-form JSON configured on `WorkflowTransition.properties`, merged over
`WorkflowVersion.properties` (transition wins). Match rules on properties/`to_state` instead of hard-coded
names so new transitions are covered without policy edits.

### 2.4 `candidate_transitions` (`preview` action only)

State-valid candidates for **every** node/workflow field in the tree, enumerated by the engine without
Rego (`engine.build_candidate_transitions`, mirroring `execute_transition`'s state checks):

```json
{ "<node_id>": { "<workflow_field_slug>": {
      "current_state": "<state|null>",
      "transitions": { "<name>": <TransitionDescriptor> } } } }
```

### 2.5 `additional_result`

The policy-defined carry-over from the VIEW pre-check pass: before a save/transition/preview evaluation the
engine evaluates `view` against `old_entity` and passes that result's `additional_result` object back
verbatim. The framework aggregator provides `{"view_allowed": <bool>, "editable": [{"node","field"}, …]}`;
modules may add keys. This replaces the former `view_was_allowed` / `old_editable_fields` input keys and
lets policies validate the new, non-persisted state against what was persisted before (e.g. reject a
changed field that was not in the carried-over editable grant).

---

## 3. Output

### 3.1 `data.udm.result` (entity actions — fixed schema)

```json
{
  "allow": false,
  "messages": [ { "level", "text", "field_slug" } ],
  "viewable_fields": { "<node_id>": ["<slug>"] },
  "editable_fields": { "<node_id>": ["<slug>"] },
  "valid_transitions": [ { "node", "field", "name" } ],
  "actions": [ { "type": "...", ... } ],
  "dashboard_columns": [ { ... } ],
  "additional_result": { ... }
}
```

- Field grants are **per-node maps covering the whole tree**, produced in this one evaluation. Empty/missing
  means fully redacted — there is **no** `null`-means-unrestricted sentinel. `serialize_node` is the single
  redaction point: every API response (view, save, transition, search preview, history) filters every node
  recursively through `viewable_fields`.
- `protected_fields` is **not** part of the result: it is the static `config.PROTECTED_FIELDS` constant that
  the default-grant modules subtract (owning modules re-grant explicitly).
- `valid_transitions` is the preview matrix (see §4).
- Messages: `level ∈ critical|error|warning|info|debug`, `text` is a plain string, `field_slug` is a slug,
  a dotted path (`"reviews.vote"`), or `null`. The engine validates messages (Pydantic `PolicyMessage`),
  drops malformed ones with a log entry, and rewrites `field_slug` into `highlight_fields: [<path>]` for
  the frontend. Blocking semantics live in the udm.rego aggregator: any `critical` ⇒ deny; any
  `critical|error` ⇒ deny transitions.

### 3.2 `data.udm.type_result` (`public_type_fields` only)

```json
{ "public_type_fields": { "<slug>": <value> }, "type_description": { "<lang>": "<markdown>" } }
```

Kept separate so type metadata never alters the entity-action result schema.

### 3.3 Consumption

- **view/browse**: `allow == false` ⇒ 404 (existence hidden; 403 with messages when the policy explains).
  `viewable_fields` filters the response tree; `editable_fields` is forwarded to the frontend.
- **save**: patch + evaluation share one transaction; deny/critical rolls everything back (HTTP 422).
- **transition**: pending edits are applied first, then the policy evaluates the patched state with the
  persisted-state context; a denial rolls back the edits together with the transition — the edits are never
  persisted on their own. The save-rule floor runs **after** the transition's pre-phase actions.
- **delete**: only `allow` (403 on denial).
- **preview**: read-only (transaction rolled back); returns `{save: {valid, errors}, messages, nodes}` in one
  response — see §4.

---

## 4. Validation preview (`POST /entities/{id}/validation-preview/`)

Replaces the removed `validate_only=true` modes of PATCH and transition. One request:

1. Snapshot the persisted root document; run the VIEW pre-check against it (`additional_result`).
2. Apply the pending `changed_fields` (writes only — no per-node policy, no side effects; staging files are
   not promoted; the transaction is rolled back at the end).
3. Enumerate state-valid transition candidates for the whole tree (no Rego).
4. Run **one** `preview` evaluation. Modules compute `valid_transitions` from `input.candidate_transitions`
   using the same predicates that authorize the real `transition` action, so preview and authorization
   cannot diverge.
5. Respond:

```json
{
  "save": { "valid": true, "errors": { } },
  "messages": [ { "level", "text", "highlight_fields" } ],
  "nodes": { "<node_id>": { "<wf_field_slug>": {
        "current_state": "submitted", "valid_transitions": ["reject"] } } }
}
```

`save.errors` carries the save-rule-floor field errors (`_validate_subtree`) — it gates the save button
only, never the transition matrix (transition pre-actions may repair data at execution time). The preview
is advisory: execution re-evaluates authoritatively.

---

## 5. Framework / module architecture

All policy sources are `Policy` rows loaded from the **database** (repo files under
`documentation/configuration/policies/` are the reference bundle — re-import after editing them). The
framework layer (`udm.rego` aggregator, `framework.rego` tree walker in `package udmtree`,
`modules/config.rego`) is deliberately DB-hosted so it can be upgraded independently of code deploys.

- Modules live under `data.udm.udmframeworkv1.modules.<name>` and export: `allow`, `error_messages`,
  `success_messages`, `viewable_fields`/`editable_fields` (sets of `{"node","field"}`),
  `valid_transitions`, `actions`, `dashboard_columns`, `additional_result`,
  `public_type_fields`/`TYPE_DESCRIPTION`. See `_template.rego`.
- `data.udmtree.tree_nodes` / `tree_nodes_with_path` walk the whole tree (node + dotted path prefix);
  schema-specific validators are ordinary modules gating on `node.schema_id`.
- **Constraint (framework design, not engine-specific)**: modules must never reference `data.udm.*` (the
  dynamic `modules[name]` scan cannot recurse back — cycle).
- **Historical constraint (regorus-only, lifted since the OPA engine swap)**: cross-module *function* calls
  used to not resolve, so helpers were defined locally per module instead of shared. The real OPA engine
  supports this; existing modules were left as-is (each still defines its own copies) since deduplicating
  them is a follow-up cleanup, not required for anything to work.
- Multiple policies per type compose by set union; any one module's `allow` suffices unless the aggregator
  denies.

---

## 6. Mail templates (`send_notification`)

Mail bodies are `MailTemplate` rows, keyed by a human slug and edited in **UDM Admin → UDM
Templating** (live preview; the HTML side renders in a `sandbox=""` iframe). Like policies, they
travel in the UDM bundle — as files under `templates/<slug>.{txt,html}.j2` plus a `<slug>.json`
holding subject/description/example input. The versioned source of truth is
`documentation/configuration/templates/`; `manage.py import_bundle` zips that folder and runs it
through the normal bundle import.

`send_notification` resolves `template_name` to a slug. A missing slug raises
`MailTemplateNotFound`, which `dispatch_actions` records as `_error` on the `FieldEdit` — the
surrounding save or transition still succeeds under the default `on_error="log"`.

### Rendering environment

Templates are staff-editable, so they render in a `jinja2.sandbox.SandboxedEnvironment`
(`userdefinedmodel/mailtemplates.py`) — **not** the environment in `project/jinja2.py`. Consequences:

- The Django `settings` object is *not* a global. Use `{{ frontend_base_url }}`; also available are
  `site_name`, `default_from_email` and `now()`.
- The context is round-tripped through JSON, so templates see plain data and cannot traverse ORM
  relations. Callers must pass everything a template needs (see `apiv1/mailcontext.py`).
- Autoescaping is on for the HTML body and off for the plaintext body.
- Undefined names render empty (`ChainableUndefined`) rather than raising, so an optional key can
  never break a transition. The `<slug>.json` example inputs are what catch typos.

### Filters

| Filter | Purpose |
|---|---|
| `timezone(tz="Europe/Berlin")` | Converts a datetime or ISO string to a tz-aware datetime. Returns a datetime, so it composes: `{{ v \| timezone("Europe/Berlin") \| isoformat() }}`. Naive values are read as `settings.TIME_ZONE`. |
| `isoformat(timespec="seconds", sep=" ")` | ISO 8601; `""` for None, idempotent on strings. |
| `userinput(prefix="    ")` | Marks user-supplied text in **plaintext** mails by indenting every line. Empty input becomes an indented placeholder. |
| `htmlquote` | HTML counterpart: escape + `<br>`, for use inside `<blockquote class="user-input">`. |

All four are also registered in `project/jinja2.py`, so the same syntax works in ordinary templates.

### Template context

`build_notification_context` (`userdefinedmodel/actions.py`) is the contract the bundled templates
depend on. The policy's own `context` object is applied first and its keys are also exposed at the
top level; the engine-provided keys are applied afterwards and therefore cannot be shadowed:

```
context            the policy's JSON, verbatim
input              the full policy input document
entity             input.entity
fields             {slug: value} of the node the action fired on
node               {id, schema_id}
user               input.user (the actor)
trigger  phase     lifecycle event and dispatch phase
action  transition  field  locale  type_id      from the input document
additional_result  the policy's VIEW carry-over
decision           {allow, messages, valid_transitions, additional_result}
recipients         resolved recipient addresses
frontend_base_url  also a Jinja global
```

A policy therefore passes calculated values like this:

```rego
actions contains {
    "type": "send_notification",
    "phase": "post",
    "template_name": "proposal-accepted",
    "recipient_field": "owner",
    "context": {"proposal": proposal_context},
} if { ... }
```

See `documentation/configuration/policies/proposals-actions.rego` for the worked example.
