# Policy Engine — Input & Output Reference

Policies are [Rego](https://www.openpolicyagent.org/docs/latest/policy-language/) programs evaluated by the
[regorus](https://github.com/microsoft/regorus) engine. All rules live under the `data.udm` package.

The authoritative, executable contract lives in
`documentation/configuration/policies/_input_schema.rego` (input shape, generated example documents,
`valid_input` predicate) and is mirrored in Python by `userdefinedmodel/policy_input.py`
(`validate_policy_input`, enforced by the engine on every input document it builds). Both are checked
against each other by `documentation/configuration/policies/check_input_schema.py`. This file is the prose
companion; where they disagree, the executable contract wins.

Current `input_version`: **1**.

---

## 1. Evaluation model

- **One evaluation per request, always on the ROOT entity document** — never per node. Submodel data is
  embedded in `entity.children`; per-node work (transition candidates, field grants) flows through maps
  keyed by node id.
- The engine reads exactly **one aggregate rule** per evaluation:
  - `data.udm.result` for entity actions (fixed schema for *every* action),
  - `data.udm.type_result` for `action == "public_type_fields"`.
- Compiled policies are cached: one `regorus.Engine` per `udm_type_id`, validated against a hash of the
  policy sources and replaced when it changes; each evaluation runs on a `clone()` (`engine.RegoSession`).
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
  "overflow_data": {},
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
- **regorus constraints**: modules must never reference `data.udm.*` (the dynamic `modules[name]` scan
  cannot recurse back — cycle); cross-module *function* calls do not resolve — define helpers locally.
- Multiple policies per type compose by set union; any one module's `allow` suffices unless the aggregator
  denies.
