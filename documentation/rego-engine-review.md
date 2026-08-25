# Rego Engine — Input/Output Layout Review

Scope: `backend/userdefinedmodel/engine.py`, `writer.py`, `api.py`, `actions.py`,
`models/node.py`, the policy sources in `documentation/configuration/policies/`,
and the reference doc `backend/userdefinedmodel/POLICY_ENGINE.md`.

---

## 0. Implementation status (updated while working)

| Item | Status | Notes / caveats |
|---|---|---|
| §3.1-1 aggregate `result` / `type_result` rules | Done | engine reads only result/type_result; udm.rego aggregator builds both; per-node grant maps; additional_result carry-over |
| §3.1-2 shared `RegoSession` eval helper | Done | RegoSession in engine.py; introspection endpoint uses it |
| §3.1-3 typed input, `input_version`, `locale` | Done | build_policy_input validates via policy_input.validate_policy_input; locale threaded from requests |
| §3.1-4 message normalization via Pydantic | Done | PolicyMessage in engine.py; malformed messages logged + dropped |
| §3.1-5 per-type compiled-engine cache | Done | thread-LOCAL cache. UPDATE (engine swap, see bottom row): now backed by opa_bindings.OpaEngine, which documents no cross-thread safety either, so the thread-local design carries over unchanged; `clone()` no longer exists — eval_document takes input directly, no per-eval clone step |
| §3.1-6 browse via cache + light input | Done (partial) | cache covers compile cost; input doc still full — caveat: no light browse doc yet |
| §3.2-7 `users`/`groups` lookup maps, drop `_expand_fields` | Done | _expand_fields removed; build_lookup_maps; groups carry member_ids |
| §3.2-8 full-tree expansion; `linked_entities` depth-1 | Done | LINKED_ENTITY_DEPTH=1 constant; linked docs' user/group refs included |
| §3.3-9 framework rego stays in DB | Done | decision only, nothing to implement |
| §3.3-10 `_template.rego` module contract | Done | rewritten to match implementation (udmtree walker, per-node grants, shared transition predicates, regorus constraints). UPDATE: the `data.udm.*` self-reference constraint is a framework design choice and stays; the cross-module-function-call constraint was regorus-only and is lifted since the engine swap (not yet exploited — existing modules still duplicate helpers locally) |
| §3.3-11 `_input_schema.rego` + example generator + check script | Done | committed; keep in lockstep with engine changes. UPDATE: check_input_schema.py ported to opa_bindings, all 110 examples still validate |
| §3.3-12 `schema_id` per node + `schemas` map + validators registry | Done | caveat: regorus cannot dispatch functions via a dynamic registry ref — validators are ordinary modules iterating data.udmtree.tree_nodes_with_path and gating on node.schema_id. This registry-dispatch limitation was regorus-specific too (same root cause as §3.3-10's function-call note) but the module-per-schema_id structure was left as-is — it still works under OPA and reworking it is a separate cleanup |
| §3.3-13 `PROTECT` on entity→type FK | Done | migration 0025 committed |
| §4 `POST /validation-preview/`, remove `validate_only` | Done | endpoint + candidate enumeration + properties column (migration 0026); PATCH/transition validate_only removed; frontend: udmValidationPreview replaces per-button calls, editor + WorkflowFieldWidget rewired, save button gated on save.valid |
| §5 recursive redaction in `serialize_node` (per-node maps) | Done | serialize_node filters whole tree; history per affected node; search preview gated on view grant |
| Framework/instance `.rego` rewrite to new contract | Done | udm.rego aggregator, framework.rego (package udmtree walker), all modules ported to PK+lookup-map contract, per-node grants, shared transition predicates. Caveats: (1) modules must NEVER reference data.udm.* — cycles; protected fields became the static config.PROTECTED_FIELDS constant; (2) cross-module function calls don't resolve in regorus — helpers are defined locally per module; (3) test_udm.rego NOT ported (opa test file; could not run under this regorus wheel before either); (4) policies load from the DB — re-import the bundle for changes to take effect. UPDATE: (2)/(3) are now historical — see bottom row |
| POLICY_ENGINE.md regeneration | Done | rewritten against the implemented contract; defers to _input_schema.rego as the executable source of truth |
| §6 submodel operation grants (create/edit/delete per parent/field/item; new-item field grants) | Done | result keys deletable_nodes / creatable_submodels (field-slug key = may create; value = new-item field grants); per-item edit derives from editable_fields; framework enforcement rules in udm.rego via additional_result carry-over; schema docs carry submodel_schema_id + prospective child schemas; default grants in save.rego, reviews grants in reviews.rego; GUI (SubmodelEditor + grants context) wired. Caveats: (1) author stays EDITABLE in the review new-item grant (client submits it; proposals.rego still enforces attribution=self) — deviation from §6.3's visible-only wording; (2) fixed a pre-existing inversion in proposals.rego (blocked editing when status WAS editable); (3) proposals.rego kept the attribution + author-change blocks (not covered by generic rules) |
| Tests updated & passing | Done | 129 tests (5 new ValidationPreviewTests), only the pre-existing draft-roundtrip failure remains; factories wrap fixtures with a shared result-aggregation suffix (wrap_policy); frontend typechecks (pre-existing errors untouched) |
| Engine swap: regorus → opa_bindings (real OPA) | Done | Project bumped to Python 3.14 (package requirement). RegoSession (engine.py) rewritten against opa_bindings.OpaEngine: no `clone()`, `eval_document` raises `OpaUndefinedError` uniformly (replaces the `"<undefined>"` sentinel AND the `"not a valid rule path"` string-match), print capture moves to `print_handler`/`last_prints`. api_bundle.py's `_extract_bundle_from_rego` and api_types.py's introspection endpoint ported too. UPDATE (opa-golib-python-bindings 0.2.0): coverage reporting was briefly dropped on 0.1.2 (no coverage API existed) and is now restored — `eval_document(..., coverage=True)` populates `engine.last_coverage`, wired back into `PolicyEvalOut.coverage` in api_types.py. 0.2.0 also adds `trace=True`/`engine.last_trace` (full per-step evaluation trace with live variable bindings); not yet surfaced anywhere, available for a future debugging feature. New `documentation/configuration/policies/run_policy_tests.py` runs test_udm.rego (never runnable under regorus) for the first time: 33 tests, 32 pass. The 1 failure (`test_submit_allowed_when_checklist_complete`) is a **pre-existing fixture bug**, unrelated to the engine: the fixture's `photo` field doesn't meet validation_rules.rego's minimum-resolution checklist gate (1440×1080), so the submit transition is correctly denied — the test's expectation was never verified before this run. Not fixed here (test-fixture/business-logic call, out of scope for the engine swap). |

---

## 1. Layout BEFORE the refactor (historical — superseded by the implementation; see POLICY_ENGINE.md for the current contract)

### Input document (`engine.build_policy_input`, engine.py:136)

```json
{
  "action": "view|browse|create|save|delete|transition|public_type_fields",
  "entity": { /* to_policy_document() of the ROOT node */ },
  "user": { /* rich user incl. email, phone_number, is_superuser, groups w/ members, permissions */ },
  "type_id": "<uuid|null>",
  "changed_fields": {},          // save only: {slug: {"value": ...}}
  "transition": null,            // transition only
  "field": null,                 // transition only (workflow field slug)
  "node_id": null,               // transition only (engine.py:452)
  "validate_only": false,        // save only (writer.py:240)
  "old_entity": null,            // save only: pre-write entity doc (writer.py:241)
  "view_was_allowed": false,     // precomputed VIEW pre-check (engine.py:290)
  "old_editable_fields": []      // precomputed VIEW pre-check
}
```

Notes:
- `user_select`/`group_select` field values in `entity.fields` (root + first-level
  children) are expanded in-place from PKs to rich user/group objects
  (`_expand_fields`, engine.py:69), with group members embedded to depth 2.
- Every field defined on the config version appears in `entity.fields`, seeded with
  `value: null` (or `{}` for localized) when no `FieldValue` row exists (node.py:161).

### Output rules read by the engine (engine.py:251–258)

| Rule | Default | Consumer |
|---|---|---|
| `data.udm.allow` | `false` | gate for every action |
| `data.udm.messages` | `[]` | normalized: `field_slug` → `highlight_fields` list (engine.py:122) |
| `data.udm.viewable_fields` | `null` (= unrestricted) | field filtering on view |
| `data.udm.editable_fields` | `[]` | forwarded to frontend |
| `data.udm.dashboard_columns` | `[]` | dashboard endpoint |
| `data.udm.actions` | `[]` | side-effect dispatch (`actions.py` registry) |
| `data.udm.public_type_fields`, `data.udm.TYPE_DESCRIPTION` | `{}` | type-level metadata (engine.py:306) |

Policy-side conventions (not engine-enforced): modules under
`data.udm.udmframeworkv1.modules.*` expose `allow`, `editable_fields`,
`viewable_fields`, `protected_fields`, `success_messages`, `error_messages`;
`udm.rego` aggregates them and implements block semantics (`critical` blocks
everything, `error` blocks transitions) via its `deny` rules.

---

## 2. Verification of POLICY_ENGINE.md against the source

Checked every section; discrepancies found:

| # | POLICY_ENGINE.md claim | Actual code | Severity |
|---|---|---|---|
| 1 | §1: actions are view/browse/save/delete/transition | `create` is also evaluated (api.py:1332, 1347) and `public_type_fields` (engine.py:329); history endpoint uses `view` | doc gap |
| 2 | §2: input has 7 top-level keys | also `view_was_allowed`, `old_editable_fields` (always), `node_id` (transition), `validate_only`, `old_entity` (save) | doc gap |
| 3 | §3.3: "Only fields that have a stored FieldValue row appear in the map. Fields with no saved value are absent." | **Wrong.** `to_policy_document()` seeds *every* defined field with a null value (node.py:161–170) | incorrect |
| 4 | §3.3 table: `user_select` value is UUID string, `group_select` is int PK | true at rest, but `_expand_fields` (engine.py:69) replaces them with rich user/group objects before evaluation (root + first-level children) | incorrect/misleading |
| 5 | §4 UserDocument: id/username/is_active/is_staff/groups/permissions | also `email`, `phone_number`, `is_superuser`; each group also carries `members` (depth-2 user objects) (engine.py:30–54) | doc gap |
| 6 | §7: engine reads 4 rules | engine also reads `dashboard_columns` and `actions` (engine.py:256–258); `PolicyEvaluationOutput` has 6 fields (actions.py:200) | doc gap |
| 7 | §7.1: message shape `{"level", "message": {lang: text}, "field_slug"}` | actual policies emit `{"level", "text": "<string>", "field_slug"}`; the engine rewrites `field_slug` into `highlight_fields: [slug]` (engine.py:122) and the frontend reads `highlight_fields` with dotted paths (apiUdm.ts:167–194). The `message` localization dict is not what the shipped policies or frontend use | incorrect |
| 8 | §7.1 level-effects table implies the *engine* blocks on critical/error | blocking is implemented in the policy layer: `udm.rego` `deny` rules (udm.rego:25–34) flip `allow`; the engine only checks `allow` | misleading |
| 9 | §6: transition input keys = `transition`, `field` | also `node_id` and the view pre-check keys (engine.py:449–455) | doc gap |
| 10 | Not documented at all | `evaluate_view_precheck` (replacement for the in-Rego "time machine"), `protected_fields`, `success_messages`/`error_messages` module convention, the `udmframeworkv1.modules` plugging architecture, multi-node context (submodel evaluation is always run against the root document, engine.py:150–153) | doc gap |

Sections verified as **correct**: §5 `changed_fields` wrapping (`writer.py:225–228`),
default-deny with no policies (engine.py:203–211), `<undefined>` sentinel handling,
§7.2 view→404 / delete→403 / save→422 consumption, §8 multi-policy union semantics.

**Recommendation:** regenerate POLICY_ENGINE.md from the code (or at minimum fix
items 3, 4, 7 — those would mislead a policy author into writing rules that never
fire or emit messages the frontend cannot render).

---

## 3. Refactoring suggestions

### 3.1 Structure / correctness

1. **Single aggregate output rule.** The engine issues 6+ separate
   `eval_rule_as_json` calls. Define `data.udm.result := {"allow": allow,
   "messages": messages, ...}` in `udm.rego` and evaluate only it; delete the
   per-rule evaluation paths. One eval, one parse, and the output contract
   lives in one visible place in Rego. The `result` schema is **identical for
   every entity action** (view, browse, create, save, delete, transition,
   preview) — keys unused by an action are present with their empty defaults.
   `public_type_fields` does **not** feed `result`: it has its own aggregate,
   `data.udm.type_result := {"public_type_fields": ..., "type_description":
   ...}`, so type-metadata keys never leak into (or vary) the entity-action
   result schema. In the new contract `viewable_fields` and
   `editable_fields` are **per-node maps** (`{node_id: [slugs]}`) covering
   the whole model tree, produced in the same single evaluation — the API
   filters every node from this one pass instead of filtering only the root
   (see §5). Never `null`: the `null`-means-unrestricted sentinel
   (engine.py:254, `PolicyEvaluationOutput.viewable_fields`) is dropped —
   a (node, field) pair is visible only if some module explicitly lists it
   (deny-by-default, consistent with `allow`). `protected_fields` is *not*
   in the result: the engine never reads it — it is an internal convention
   between modules and the framework default-grant rules (save.rego:16,
   view.rego:37) and stays that way. The result instead carries a
   policy-defined `additional_result` object: whatever the VIEW pre-check
   pass emits there is passed back verbatim as `input.additional_result` to
   the save/transition/preview evaluation, replacing the hardcoded
   `view_was_allowed` / `old_editable_fields` input keys.
2. **Deduplicate the eval helpers.** `_eval_bool`/`_eval_list` and the
   `<undefined>` sentinel logic exist twice (engine.py:225–248 and
   api.py:768–788, `eval_policy_for_type`). Extract a small
   `RegoSession` wrapper (load policies, set input, typed eval with defaults,
   prints/coverage) used by both, so the introspection endpoint can never drift
   from real engine behavior.
3. **Type the input document.** `build_policy_input` assembles a plain dict with
   `**kwargs` passthrough — typos in a kwarg silently become new input keys.
   Define a Pydantic `PolicyInput` model (mirroring `PolicyEvaluationOutput`) and
   add an explicit `"input_version": 1` key so policies can assert compatibility.
   The input also gains `locale`: the requesting user's locale (e.g. `"de"`),
   used by policies to pick message languages; it is `null` exactly when the
   system calls itself (policy actions, background tasks) — no human locale
   exists there, and policies must fall back to a default language.
4. **Normalize messages in one schema.** `text` vs `message`-dict, `field_slug`
   vs `highlight_fields` is currently resolved by ad-hoc rewriting
   (engine.py:122). Validate messages with a Pydantic model at the engine
   boundary and reject/log malformed ones instead of passing raw dicts through.
5. **Cache compiled policies.** Every evaluation re-adds and re-compiles all
   policy sources (engine.py:215–218) — and a single save/transition triggers at
   least two evaluations (view pre-check + main). Cache **one** compiled
   `regorus.Engine` per `udm_type_id` — keyed by the type, validated against
   the current policy-versions hash, and replaced (not accumulated) when the
   hash changes — and use `engine.clone()` per evaluation. One entry per type
   bounds the cache at the number of UDM types, so stale policy versions never
   pile up.
6. **Browse is O(n) engine builds.** The entity-search path evaluates
   `browse` per candidate entity (api.py:1727), rebuilding the engine and the
   full input document each time. With the cache from (5), only the input swap
   remains; consider also a lighter entity document for browse (no group-member
   expansion).

### 3.2 Input-document size

7. **Flatten user/group expansion.** `_expand_fields` embeds full user objects
   (with depth-2 group members) into every `user_select` value, duplicating the
   same users many times in one document and inflating serialization cost.
   Prefer top-level lookup maps — `input.users: {id: UserDoc}`,
   `input.groups: {id: GroupDoc}` — and keep PKs in field values; policies
   resolve via `input.users[input.entity.fields.owner.value]`. This is a
   breaking change for policies: update all policy sources in the same change
   and remove `_expand_fields` entirely.
8. **Expand the submodel tree recursively; flatten entity links to depth 1.**
   Expansion currently covers the root and first-level children only
   (engine.py:162–165); grandchild `user_select` fields still hold raw PKs — an
   inconsistency policies can trip over. Fix by expanding through the **entire
   submodel tree** — submodels form a strict tree, cycles are impossible, so
   full recursion is safe and bounded. `entity_select`/`entity_select_multi`
   links are different: they can form cycles across entities, so do **not**
   recurse through them. Instead resolve linked entities into a flat top-level
   lookup map — `input.linked_entities: {id: EntityDoc}` — expanded exactly
   **one entity deep** (the linked entity's own fields and submodel tree, but
   its outgoing entity links stay raw PKs). Make the depth a configurable
   constant so it can be raised later if policies need it.

### 3.3 Policy sources

9. **Framework policies stay in the DB (deliberate).** `udm.rego`,
   `utils.rego`, and the module-aggregation convention are framework code
   living in the DB alongside instance policies. Moving them into the repo was
   considered and rejected: keeping them as `Policy` rows allows upgrading the
   framework layer independently of code deploys. Accepted trade-off: the
   engine↔aggregator contract is not enforced by the repo, so framework
   upgrades must be applied deliberately per deployment.
10. **Document the module contract in Rego.** Add a `_template.rego` showing the
    exported names a module may define (`allow`, `error_messages`, …), since the
    aggregator silently ignores anything else.
11. **Document the input contract in Rego too.** Add an `_input_schema.rego`
    (shipped alongside the framework policies) that describes every
    `input.*` key the engine provides — action values, entity/user document
    shape, the precomputed keys (`additional_result`,
    `candidate_transitions`, …) — as commented example structures and, where
    practical, as helper predicates (e.g. `valid_input if { ... }`) that
    policies and tests can assert against. Keep it next to the
    `"input_version"` key from §3.1-3 so a framework upgrade that changes the
    input shape is visible in one place, in the same language policy authors
    read.
12. **One evaluation for the whole tree — never per node.** The policy is
    always invoked exactly once per request, on the **root** entity document
    with the complete submodel tree embedded in `children` (the engine
    already builds root context even when a submodel triggered the
    evaluation, engine.py:150–153 — keep and strengthen this into an explicit
    contract). Instead of the `type: "submodel:<slug>"` string, annotate
    **every** node document (root and submodels alike) with its model schema
    UUID, and provide a flat top-level lookup of schema properties:

    ```json
    "entity":  { "id": "...", "schema_id": "<uuid>", "fields": {...}, "children": {...} },
    "schemas": {
      "<uuid>": { "slug": "review", "fields": { "vote": {"data_type": "select_single", ...} }, "properties": {...} }
    }
    ```

    Validator modules then register themselves per schema UUID using the
    existing dynamic-registry pattern — e.g. a framework walker collects
    every node in the tree and dispatches
    `udmframeworkv1.validators[node.schema_id]` rules against it — so a
    (sub)model-specific validator is written once against its schema,
    loaded dynamically via the registry, and fires for every node of that
    schema wherever it appears in the tree. No rule ever branches on node
    type or position; messages still use dotted `highlight_fields` paths
    (`"reviews.vote"`). Any future feature (including the §4 preview) feeds
    per-node work through input maps keyed by node id
    (`candidate_transitions`, `node_id`), not through repeated evaluations.
13. **Type deletion orphaned entities (fixed: PROTECT).** An entity belongs
    to at most one UDM type (`UserDefinedModelEntity.user_defined_model_type`,
    node.py:284, a nullable FK; submodels carry no type). It was declared
    `on_delete=SET_NULL`, so deleting a type silently set its entities
    typeless — and since policies attach to the type, typeless entities are
    default-denied for everyone (engine.py:203–204): permanently inaccessible
    data. Changed to `on_delete=PROTECT` so a type with entities cannot be
    deleted; entities must be migrated to another type first. (The `type_id`
    never-null input contract is unaffected either way — typeless entities
    never reach Rego.)

---

## 4. Single validation-preview endpoint (one pass, no per-button calls)

### Today

Validation previews are spread over two `validate_only=true` variants:
`PATCH /entities/{id}/?validate_only=true` for the save button
(`udmValidateEntity`, apiUdm.ts:196) and
`POST /entities/{id}/transition/?validate_only=true` **once per transition
button** (`udmValidateTransition`, apiUdm.ts:214). Each call takes the root
lock, applies a throwaway patch, runs the VIEW pre-check evaluation *and* the
main evaluation, then rolls back — i.e. 2 policy compiles + 2 evals + 1
transaction per button, times every workflow field, times every (sub)model node.

### Proposal: `POST /entities/{entity_id}/validation-preview/`

**One** endpoint replacing both `validate_only=true` variants (which are
removed, along with `udmValidateEntity`/`udmValidateTransition`). It takes the
frontend's pending edits (`{"changed_fields": {...}}`, same shape as the
PATCH/transition payloads) and returns everything validation-related in one
response: all policy messages, the save-button status, and the per-node
per-workflow-field transition-button statuses. Computation, in a single pass:

1. Open a transaction, take the root lock (as in the `validate_only` path,
   api.py:1473–1479), load the subtree, and snapshot the **persisted** state
   first: build the pre-patch entity document and run the VIEW pre-check
   against it, exactly as the save path does (writer.py:235,
   `evaluate_view_precheck`). Then `apply_patch` the pending
   `changed_fields` — the preview evaluates the same post-save state a real
   transition would see. Roll the transaction back at the end.
2. Enumerate candidates without Rego: for every node in the subtree, for every
   `workflow` field definition, collect the transitions of its workflow
   version whose `from_state`/`from_undefined_only` matches the field's
   current state (this mirrors the state checks in `execute_transition`,
   engine.py:427–439). Transitions that fail the state check are dropped
   without any policy evaluation. Each surviving candidate is passed as a
   full descriptor, not just a name, so the policy can assemble the allowed
   list dynamically from transition properties instead of hard-coding names:

   ```json
   "candidate_transitions": {
     "<node_id>": {
       "<workflow_field_slug>": {
         "current_state": "submitted",
         "transitions": {
           "accept": {
             "from_state": "submitted",
             "to_state": "accepted",
             "from_undefined_only": false,
             "properties": { "requires_all_reviews": true }
           },
           "reject": {
             "from_state": "submitted",
             "to_state": "rejected",
             "from_undefined_only": false,
             "properties": { "moderator_only": true }
           }
         }
       }
     }
   }
   ```

   `properties` is an optional free-form JSON object configured per
   `WorkflowTransition` (new column) and per workflow version (merged, with
   the transition's own properties winning). Policies then write generic
   rules like "allow every candidate whose `properties.moderator_only` is
   true when the user is a moderator" and automatically cover transitions
   added later to the workflow, without a policy edit.
3. Run **one** policy evaluation (compiled-engine cache from §3.1-5, root
   policy document built once from the patched state) with
   `action = "preview"`, `input.changed_fields` (as in a save evaluation),
   the candidate descriptor map above as `input.candidate_transitions`, **and
   the persisted-state context**: `input.old_entity` (pre-patch document) and
   `input.additional_result` — the VIEW pre-check's policy-defined
   carry-over object (§3.1-1), same as the save evaluation receives.
   Policies can therefore validate the *new, non-persisted* state while
   checking against what was persisted before that no unauthorized field
   changed (e.g. a changed field not in the carried-over editable grant
   invalidates every candidate).
4. From that same evaluation, read the save verdict and the messages:
   `data.udm.allow` plus absence of critical errors gives `save_valid`, and
   `data.udm.messages` (normalized to `highlight_fields`) carries every
   warning/error to display — the preview treats the pending edits exactly
   like a save evaluation would, so the save button and the field highlights
   come for free.
5. The policy exports the transition matrix itself: modules iterate
   `input.candidate_transitions` and define
   `valid_transitions contains {"node": node_id, "field": slug, "name": name}`
   for the candidates they permit — matching on the descriptor's
   `properties`/`to_state` rather than hard-coded names where possible. The
   `udm.rego` aggregator unions them into `data.udm.valid_transitions`. This
   requires rewriting the existing per-transition `allow` rules
   (transitions.rego) into predicates parameterized by the candidate
   descriptor; the `input.transition`-branching rules for the `transition`
   action then call the same predicates (the executing transition's
   descriptor is passed as `input.transition_descriptor` there), so the
   preview and the actual authorization cannot diverge.
6. Return everything in one response:

```json
{
  "save": {"valid": true, "errors": {}},
  "messages": [ {"level": "warning", "text": "...", "highlight_fields": ["status"]} ],
  "nodes": {
    "<node_id>": {
      "<workflow_field_slug>": {
        "current_state": "submitted",
        "valid_transitions": ["reject", "request-revision"]
      }
    }
  }
}
```

`save.errors` carries the `_validate_subtree` / writer `ValidationError`
field errors (same shape as today's `ValidationResult.errors`). The frontend
enables the save button from `save.valid`, enables exactly the listed
transition buttons, and renders all messages/highlights from `messages` —
one request per debounce/preview, regardless of button count. The actual
`PATCH` and `POST /transition/` endpoints still evaluate authoritatively at
execution time; only their `validate_only=true` modes are deleted.

### Why this stays cheap

- Exactly **one** policy evaluation for the whole matrix — no batch loop over
  candidates, no repeated input serialization or per-candidate evals.
- Policy compilation happens zero times with the engine cache (§3.1-5).
- The entity document and `_expand_fields` user/group queries — the expensive
  parts — are built once for that single evaluation.
- The state-machine filter (step 2) shrinks `candidate_transitions` before
  Rego ever sees it.

### Semantic differences vs the removed `validate_only=true` calls

- Pending edits **are** applied (`apply_patch` inside the rolled-back
  transaction), matching what `transition_entity` does on real execution
  (api.py:1517–1519) — so the preview evaluates the same post-save state and,
  given the policy convention that transition-allow usually implies
  save-allow, matches the real outcome.
- `_validate_subtree` (the save-rule floor) is run once after the patch and
  feeds `save.errors` — it gates the **save button only**, not the transition
  matrix. It must not be used to drop transition candidates: in real
  execution the floor is checked *after* the transition's pre-phase policy
  actions have run (engine.py:488–491), and those actions (e.g.
  `set_field_value`) may change data so that it becomes save-valid for that
  particular transition. A per-candidate floor check would require executing
  each candidate's actions in the preview, which is exactly the batch
  computation this design avoids — so transitions whose validity depends on
  their own actions are surfaced as enabled by policy and, if the floor still
  fails at execution, rejected authoritatively there (HTTP 422 with field
  errors, transaction rolled back).
- The preview remains advisory: `execute_transition` re-evaluates
  authoritatively at execution time, so a stale preview can never authorize
  anything.

### Execution semantics: save only if the transition validates

On real execution the same order must hold: the pending edits are applied
*inside* the open transaction first, then the transition policy is evaluated
against that non-persisted state (with the persisted-state context keys), and
only if it allows does anything commit. A policy denial or `TransitionError`
must roll back the applied patch along with the transition — the edits are
never persisted on their own. This is the current behavior of
`transition_entity` (patch and `execute_transition` share one
`transaction.atomic()` block, api.py:1506–1519, and denials raise out of it);
the refactoring must preserve it, and the preview endpoint's
evaluate-nonsaved-state-first order (§4 step 1) intentionally mirrors it.


## 5. Gaps: non-viewable fields can still leave the API

Audit of every path that serializes entity data (2026-07-12):

1. **Submodel fields are never filtered.** `_entity_out_for_user`
   (api.py:129–150) filters top-level `field_values` and drops whole
   non-viewable `children` lists — but once a child list slug is viewable,
   `serialize_node` recurses and **every field of every submodel node is
   returned unfiltered**. `viewable_fields` has no dotted-path semantics on
   output, so a policy cannot hide `reviews.internal-comment`.
2. **Non-root responses skip filtering entirely.** The filter only runs when
   `is_root` (api.py:141–145). `PATCH` and `POST /transition/` on a submodel
   node return `_entity_out_for_user(node, ...)` with the node's full
   `field_values`, regardless of `viewable_fields`.
3. **`viewable_fields = None` means everything.** The unrestricted sentinel
   exposes all fields and children; removed by the new always-a-list contract
   (§3.1-1), which turns "policy forgot to define viewable_fields" from
   full exposure into full redaction.
4. **Entity search preview strings bypass the view policy.** A `browse`-allowed
   user gets `_entity_preview_display` (api.py:1746) built from all
   `is_preview` fields without consulting `viewable_fields` — preview field
   content leaks even where the view policy would hide those fields.
5. **History over-redacts but fails closed** (api.py:1544–1568): edits are
   dropped when the slug is not in the top-level `viewable` list, which
   silently hides all submodel field edits whenever any restriction exists;
   transition state names and structural edits are always visible.
6. Verified safe: migration-preview exposes only slugs/data types (gated on
   save-allow); the eval-policy introspection endpoint deliberately exposes
   everything but requires both `view_policy` and `change_policy` permissions.
   Not audited here: media/file serving for `FileAttachment`s — no download
   endpoint exists in this API module; wherever files are served must enforce
   the same view policy.

Fix direction: make the writer filter recursively using the schema-annotated
tree (§3.3-12) — `viewable_fields` entries as dotted paths or per-schema field
sets — and apply it in `serialize_node` itself so every caller (view, save,
transition, search preview, history) goes through one redaction point instead
of each endpoint reimplementing it.

## 6. Submodel operation grants (planned — extends the contract)

Requirement: control **per parent model, per submodel_list field, per submodel
item** who may edit or delete an existing item (the buttons in the list) and
who may create a new one (the button below the list) — and, for a **newly
created, not-yet-saved item**, which of its fields are visible/editable in the
client-side form.

### 6.1 Output extension (`data.udm.result`)

No separate per-item *edit* grant: the existing per-node `editable_fields`
map already expresses it — an item is editable iff its node id has a
non-empty entry; non-editable nodes simply have **no key** (never an empty
`{}` placeholder). The list's edit button enables when
`editable_fields[child_id]` is non-empty.

Two new fixed keys, present for every entity action with empty defaults:

```json
"deletable_nodes": ["<child_node_id>"],
"creatable_submodels": {
  "<parent_node_id>": {
    "<submodel_list_slug>": { "viewable": ["<slug>"], "editable": ["<slug>"] }
  }
}
```

The PRESENCE of a field-slug key grants creation; its value is the field
grant for the not-yet-saved item form (visible fields / enabled inputs).

Module exports (unioned by the aggregator like the other grants; all
deny-by-default):

```rego
deletable_nodes     contains child_id if { ... }
creatable_submodels contains {"node": parent_id, "field": slug,
                              "viewable": [...], "editable": [...]} if { ... }
```

Keying still allows per-parent-model (via `input.schemas[parent.schema_id]`),
per-field, and per-item decisions ("reviewers may delete only their own
review" iterates the child nodes when granting `deletable_nodes`). The
prospective child schema is known statically from the field definition, so
modules can grant per target schema without a node id existing yet; multiple
modules granting the same (parent, field) merge by unioning their lists.

### 6.2 Enforcement (not only cosmetics)

- **GUI**: per-item edit button ⇐ `editable_fields[child_id]` non-empty;
  per-item delete button ⇐ `child_id in deletable_nodes`; create button below
  the list ⇐ field-slug key present in `creatable_submodels[parent_id]`; the
  unsaved-item form builds its field set from that entry's `viewable` list and
  enables inputs from its `editable` list.
- **Writer**: the framework carries these keys in `additional_result`
  (computed by the VIEW pre-check on the persisted state). A
  framework-provided save rule turns unauthorized ops in
  `input.changed_fields[<slug>].value` into critical errors:
  `create` requires the creatable grant, `delete` requires the child in
  `deletable_nodes`, `update` requires the changed child fields ⊆ the
  carried-over `editable_fields[child_id]`. This replaces today's
  hand-written per-bundle denial blocks in proposals.rego (review-modify /
  review-attribution / not-reviewer) with instance-specific *grants*.
- **Preview**: the keys ride along in the §4 preview response so all list
  buttons come from the same single evaluation.

### 6.3 Migration of the demo bundle

reviews.rego expresses its current rules as grants:
- `creatable_submodels`: reviewers while `submitted`.
- `deletable_nodes`: the review's author only (per item).
- per-item edit: already covered — reviews.rego grants `editable_fields`
  only on the author's own review node.
- `creatable_submodels` entry grants: `vote`/`comment` editable, `author`
  visible-only (it is auto-set server-side).
proposals.rego's critical-message blocks are then redundant and removed.
