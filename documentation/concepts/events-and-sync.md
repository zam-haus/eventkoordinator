# Concept: Events as UDM Entities, Calendar Element, and Workflow-Driven Sync

Status: concept finalized 2026-08-08. All decisions below were made
interactively; no open questions remain.

## Goals

1. Events become a **UDM type** (not a hardcoded model), linked to the proposal
   they originate from, with access to the proposal's values and the option to
   override individual fields.
2. A **calendar form element** shows imported iCal calendars, CalDAV calendars,
   and other UDM events; it supports picking dates and is also usable as a
   standalone dashboard calendar.
3. Pressing a workflow **transition button** can mark an event as
   to-be-synced; a worker then pushes it (individually or in bulk) to CalDAV
   calendars and ticketing systems (Pretix, generic webhook).
4. **apiv1 is deprecated**: frozen now, removed once events + sync run on UDM.
   No data migration of legacy apiv1 events.
5. The sync framework moves out of apiv1 into a new **`sync_core`** app and is
   retargeted at UDM entities.
6. Rego policies get access to **linked entities** without receiving the whole
   database as input.

## Decisions at a glance

| Topic | Decision |
|---|---|
| Event ↔ proposal link | Existing `entity_select` field kind (`EntitySelectTypeConfig` with `limit_to_type_ids`), extended where needed |
| Rego linked-entity access | Dynamic link requests: any rego file contributes to `linked_inputs` / `backlink_inputs` set rules, evaluated in a request phase before the main evaluation |
| Events per proposal | Multiple (`allow_multiple: true` default for event creation) |
| Referenced-entity deletion | Protect (application-level, values are raw ids) + `delete.rego` override; dangling ids read as `null` |
| Proposal → events navigation | New `backlink_list` form element (filterable by originating type + field slug) |
| Entity links in markdown | `entity_url` Jinja filter (entity id → clickable frontend URL) in markdown + mail templates |
| Sync framework | New `sync_core` app; `SyncBaseItem.related_entity` = FK to `UserDefinedModelEntity` |
| apiv1 | Freeze, then remove. **No data migration.** |
| Field overrides | Explicit override fields on the event + rego coalesce into an `effective` object |
| Effective-values display | Rego returns structured values; a template (mailtemplate-style) renders markdown into a display field |
| Calendar element | Availability view + date picking + shows UDM events + standalone dashboard variant |
| Sync trigger | Transition action sets per-target pending status; Celery worker pushes everything pending (bulk = same worker) |
| Event creation | Workflow transition action on the proposal creates the linked event |
| Ticketing targets | Pretix (adapted) + a new generic webhook target |
| Per-target sync state | One `SyncItem` row per (entity, target); base statuses `pending/synced/error`, extensible per item class; not workflow lanes, not injected fields |
| Sync-state visibility | Single computed `derived_state` (pending/error/synced/stale/target_unavailable) exposed as `input.sync` (rego), `sync` (jinja), and a `sync_status` form element; staleness stored post-save; targets soft-deleted |
| Target configuration UI | Per-plugin **type-editor tab**, extensible: backend registry + frontend component registry keyed by tab id |
| Staleness after sync | Rego **post-save action** re-marks affected targets pending when relevant values change |

## 1. Event as a UDM type

An "Event" is an ordinary `UserDefinedModelType` with its own config, fields,
workflow, and policies. Nothing in the engine knows the word "event"; the
behaviors below are generic capabilities that any type can use.

### 1.1 Reuse the existing `entity_select` field kind

The link is an ordinary field of the existing `DataType.ENTITY_SELECT`
(`schemas.py:62`), configured via `EntitySelectTypeConfig` with
`limit_to_type_ids` restricting it to the proposal type:

```yaml
- slug: origin
  data_type: entity_select
  type_config: {limit_to_type_ids: [<proposal type id>]}
```

Gaps to close on top of what exists today:

- **Immutability:** an origin link set by the creation action should normally
  not be editable afterwards. Either an `immutable_after_create` flag on
  `EntitySelectTypeConfig`, or simply a rego save rule forbidding the change
  (preferred: no schema change, policies already gate field edits).
- **Deletion behavior — protect + policy override.** `entity_select` stores a
  real FK (`TypedValue.value_node`, `on_delete=SET_NULL`); only
  `entity_select_multi` stores raw id strings (`value_json`), so the reverse
  lookup has two query paths — an FK filter for singles, a JSON-containment
  filter for multi. Enforcement is application-level: the entity delete path
  runs the backlink reverse lookup (same query as the `backlink_list` element,
  1.5) and refuses deletion while backlinks exist, listing the referencing
  entities. The delete-policy input gains a backlink summary (count, per
  referencing type + field slug) so `delete.rego` can grant a separate
  `force_delete` rule (OR-ed like `allow`, alongside the normal `allow` for
  the same "delete" action) to override the block — e.g. for sudo users. A
  forced delete on a single `entity_select` clears the reference automatically
  (`SET_NULL`); on `entity_select_multi` the id is left dangling in the JSON
  list. Any dangling id — forced delete, races, historic data — uniformly
  resolves to `null` in `input.linked`, templates, and the UI (deleted-entity
  placeholder), so readers must always handle `null` and never break on
  missing targets.
- **Link expansion** (section 2) follows `entity_select` field slugs; the
  `_MULTI` variant naturally yields a list of linked documents.

### 1.2 Creation via transition action

A new registered policy action (via the existing `policy_action` registry in
`actions.py`):

```json
{
  "type": "create_linked_entity",
  "target_type": "event",
  "reference_field": "origin",
  "initial_fields": {"title_override": null},
  "phase": "post"
}
```

Emitted by the proposal's transition policy (e.g. on "accept"). The handler
creates a new entity of `target_type`, sets its `entity_select` field
`reference_field` to the triggering entity, and initializes fields. It runs in
the same EditGroup as the transition, consistent with existing action handlers.

**Multiple events per proposal are a first-class requirement** (e.g. repeated
sessions, workshop + talk). `allow_multiple: true` is therefore the default
for this use case: every firing of the action creates a new linked event.
`allow_multiple: false` remains available for links that must be unique
(no-op if an entity of that type already references the proposal via that
field). Navigation back from the proposal to its events is provided by the
backlinks form field (1.5).

A policy-gated manual creation path is possible later but is not part of this
concept (decision: transition action is the standard flow).

### 1.3 Overrides and effective values

The event type declares its own optional fields for anything overridable
(e.g. `title_override`, `start_override`). The **effective** value of each
property is computed in rego by coalescing:

```rego
effective["title"] := v if { v := input.entity.fields.title_override; v != null }
effective["title"] := input.linked.origin.fields.title if {
    input.entity.fields.title_override == null
}
```

The policy exposes `effective` as part of its output document. This object is:

- rendered into a **markdown display field** on the event form (see 1.4),
- the **payload source for sync targets** (see 4), so what is displayed is
  exactly what gets pushed.

### 1.4 Effective-values markdown display

Rego stays presentation-free: it returns the structured `effective` object.
A template (same engine as `mailtemplates.py`, Jinja) declared in the type
config renders it to markdown; the result is shown in a read-only display
field on the form. The template receives `effective`, `entity`, and `linked`
contexts. Rendering happens server-side when the form document is built, so
the frontend just displays markdown as it already does for other display
fields.

### 1.6 Jinja filter for entity links

The markdown-template environment gains an `entity_url` filter that converts a
UDM entity id into the frontend URL of that entity's form, so templates can
render clickable backlink/link lists:

```jinja
{% for ev in backlinks.events %}
- [{{ ev.fields.title }}]({{ ev.id | entity_url }}) — {{ ev.workflow_state }}
{% endfor %}
```

The template context for markdown display fields therefore includes
`backlinks` (from the policy's requested `backlink_inputs`) alongside
`effective`, `entity`, and `linked`. The filter resolves ids to routes
server-side (base URL from settings), and is also available in mail templates
so notifications can deep-link to entities. Rendered markdown links to
same-origin entity routes are permitted by the frontend markdown renderer.

### 1.5 Backlinks form field

A new display element `backlink_list` shows, on an entity's form, the entities
that reference it via an `entity_select` field — e.g. on the proposal form,
all events whose `origin` points at it. Element config:

```yaml
- slug: linked_events
  data_type: backlink_list
  type_config:
    source_type_ids: [<event type id>]   # filter: referencing entity type(s)
    source_field_slug: origin            # filter: which entity_select field
```

Each backlink is rendered using the referencing type's **existing preview
mechanism** — the summary built from that type's `is_preview` form elements
(`summaries.py`), as already used by `EntitySelectPreview` and the submodel
history preview. There is deliberately no per-element display-field
configuration: how an event previews is defined once, on the event type.

Backend: a query endpoint on `api_entities` resolving backlinks (reverse
lookup over entity_select field values, filterable by originating type and
field slug) and returning the preview summaries, policy-filtered so users only
see backlinks they may view. Frontend: renders the previews as clickable
entries navigating to the referencing entity's form, with workflow-state
badge. This is the primary navigation from a proposal to its events.

## 2. Rego access to linked entities

### 2.1 Link expansion

The engine already resolves `entity_select` targets into
`input.linked_entities`, a flat id→document map, exactly
`LINKED_ENTITY_DEPTH = 1` deep (`engine.py`, contract §3.2-8). This concept
extends that mechanism rather than replacing it: requested paths
(`linked_inputs`) allow **deeper** expansion along named routes and add the
convenience of path-shaped access (`input.linked.origin` next to the flat
map), and `backlink_inputs` adds reverse lookups. Resolution stays where it is
today — computed in Python before evaluation, no OPA callbacks, no lazy
fetching, evaluation pure and cacheable. Whether `input.linked` is a new
structure or sugar resolved from `linked_entities` ids is an implementation
detail; the flat map remains for backward compatibility.

### 2.2 Dynamic link requests from rego

Required links are not statically declared — they are **evaluated**. Every
rego file can contribute to two well-known set rules (accumulating via
`contains`, in the shared policy package alongside the existing framework
rules):

```rego
# any policy file may contribute:
linked_inputs contains "origin"
linked_inputs contains "origin.owner" if {
    input.entity.workflow_state == "published"   # requests may be conditional
}

backlink_inputs contains {
    "name": "events",          # key under input.backlinks
    "source_type": "event",    # referencing type
    "source_field": "origin",  # entity_select slug on that type
}
```

Evaluation becomes two-phase:

1. **Request phase:** the engine evaluates `linked_inputs` /
   `backlink_inputs` against the base input (entity, schemas, flat
   `linked_entities` map — everything except the expanded structures). Because
   these are ordinary rules, requests can depend on input (type, workflow
   state, field values), and every loaded policy file contributes to the same
   sets.
2. **Expansion + main phase:** the engine resolves the requested forward paths
   into `input.linked` and the requested reverse lookups into
   `input.backlinks`, then runs the actual evaluation.

If a request in turn depends on data that only becomes available after
expansion (e.g. a path conditional on a linked entity's field), the request
phase is re-run with the expanded input until the requested set is stable,
bounded by a small fixed iteration limit (e.g. 3); requests still unstable at
the limit are an evaluation error. The compiled engine is cloned per phase as
today, so the extra pass costs one cheap evaluation, not a recompile — but
resolution now happens per request rather than once per policy save.

`linked_inputs` entries are forward paths (following `entity_select` values);
each `backlink_inputs` entry makes `input.backlinks.<name>` a **list** of
NodeDocuments for all entities of `source_type` whose `source_field`
references the current entity. This is how a proposal's policy can coalesce or
aggregate over its events, and how backlink lists reach markdown templates
(1.6). Expansion is uncapped: every requested path and every matching backlink
is resolved in full.

Path semantics:

- Each path segment is an `entity_select` field slug on the current level's
  type; `origin.owner` means "follow `origin`, then that entity's `owner`".
- A path resolves to a `NodeDocument`-shaped object (fields, workflow state,
  type name, id) — the same shape as `input.entity`, so rules are reusable.
- Broken/unset references resolve to `null`; policies must handle that.
- No depth or count caps: requested expansion always resolves in full.
  Policy authors are responsible for not requesting pathological amounts of
  data; the request-phase rules being conditional on input makes that
  manageable.

The pydantic mirror of the input schema (`policy_input.py`) gains a
`linked: dict[str, NodeDocument | None]` member, and
`_input_schema.rego` / `check_input_schema.py` are updated accordingly (see
the rego contract refactor plan).

## 3. sync_core: the relocated sync framework

New Django app `sync_core` replacing `apiv1.models.sync.syncbasedata`:

```python
class SyncBaseTarget(PolymorphicMetaBase):
    # unchanged in spirit: name, secret_field_names, enabled flag, ...

class SyncBaseItem(PolymorphicMetaBase):
    related_entity = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntity",
        on_delete=models.CASCADE, related_name="sync_items")
    sync_target = ...           # FK on the concrete subclass, as today
    status = models.CharField(  # per-target sync state
        choices=["pending", "synced", "error"])
    last_error = models.TextField(blank=True, default="")
    synced_payload = models.JSONField(null=True)  # snapshot of last pushed effective values

    class Meta:
        constraints = [UniqueConstraint(fields=["related_entity", "sync_target"], ...)]
```

- Dependency direction: `sync_core` → `userdefinedmodel`, one-way.
- The FK targets *any* UDM entity; whether a type participates in sync is pure
  configuration (see 5). No "event" special-casing in the schema.
- `SyncDiffData` / `PropertyDiff` move along and diff against
  `synced_payload` vs. current effective values.
- `sync_ical`, `sync_caldav`, `sync_pretix` are migrated to import from
  `sync_core` and to read event data from UDM effective values instead of the
  apiv1 `Event` model. A new `sync_webhook` app provides the generic HTTP
  target: it POSTs the **effective-values snapshot as JSON** verbatim — no
  payload templating; receivers adapt to the effective object's shape.
  Authentication is a configurable `Authorization: Bearer <token>` header plus
  arbitrary constant custom headers on the target (token and header values are
  `secret_field_names`); no request signing. The body also carries entity id, target key,
  status, and a monotonically increasing sequence so receivers can order and
  deduplicate deliveries.
- Nothing is migrated from the apiv1-era tables: neither sync item rows nor
  target configurations. Targets (server URLs, credentials) are re-entered
  manually in the new `sync_core` admin.

### 3.1 Per-target sync state

The `status` on `SyncBaseItem` is the "workflow status mark per sync target":

- `pending` — marked for push; the worker will pick it up.
- `synced` — remote is up to date with `synced_payload`.
- `error` — last push failed; `last_error` holds the reason; the UI shows the
  error badge. By default the worker does **not** retry: one attempt per
  marking, and the item stays in `error` until something sets it `pending`
  again (a policy re-mark or a manual bulk sync). A concrete item class may
  instead define its own retry/backoff behavior where the remote warrants it.

These three are the base contract every item class supports.
Sync state is exposed to policies, templates, and forms via a single derived
representation — see 3.2. A concrete
`SyncBaseItem` subclass may define **additional statuses** with their own
worker semantics, and declares which statuses the `mark_sync` action (4.1) may
set from policy — e.g. a Pretix item adding `cancelled` (push a cancellation
instead of an update) or a CalDAV item adding `delete_pending` (remove the
remote VEVENT). The `status` column is therefore a plain CharField validated
against the item class's declared status set, not a global choices enum.

The entity's own workflow state remains single-valued; per-target sync state
lives only in these rows. The entity API embeds
`sync_items: [{target, status, remote_uid, last_error}]` so the frontend can
show per-target badges on forms and dashboards.

### 3.2 Exposing sync state to rego, jinja, and forms

For each item, `sync_core` computes a **`derived_state`** in one place, and
every surface consumes that same value:

- `pending` — push queued.
- `error` — last push failed (`last_error` available).
- `synced` — remote matches `synced_payload`.
- `stale` — status is synced, but current effective values differ from
  `synced_payload` and nothing is pending ("target is stale but no sync
  pending").
- `target_unavailable` — the item row exists but its target was soft-deleted
  / disabled, or is no longer bound to the type in the plugin tab config.

Surfaces:

1. **Policy input:** `input.sync`, a map keyed by target key:
   `{"caldav:main-calendar": {"status": ..., "derived_state": ...,
   "last_error": ..., "synced_at": ..., "remote_uid": ...}}`. Present for the
   root entity and on backlink documents, so e.g. a proposal policy can
   aggregate over its events' sync states.
2. **Templates:** the same `sync` map is part of the markdown- and
   mail-template context alongside `effective` / `entity` / `linked` /
   `backlinks`.
3. **Forms:** the entity API embeds the map (per 3.1); a new `sync_status`
   display element renders per-target badges (state, error message,
   last-synced time); markdown display fields can render custom views via the
   template context.

Two consequences for the data model:

- **Staleness is a stored flag, not computed in-input.** Effective values are
  a policy *output*, so `input.sync` cannot compare them live (circular).
  Instead, after each save/transition evaluation, action dispatch compares the
  freshly computed `effective` object against each item's `synced_payload` and
  stores `is_stale` on the item row; subsequent evaluations, templates, and
  API reads see it as plain data. A post-save rule can therefore react to
  `derived_state == "stale"` and decide whether to emit `mark_sync` — or
  deliberately leave the item stale for human review.
- **Targets are soft-deleted.** `SyncBaseItem.sync_target` must not cascade
  away the evidence: targets get an `enabled`/soft-delete flag (hard delete
  only when no items reference them), so `target_unavailable` remains
  observable on the items.

## 4. Workflow-driven sync

### 4.1 Marking for sync (transition)

A new registered policy action:

```json
{"type": "mark_sync", "target": "caldav:main-calendar", "status": "pending", "phase": "post"}
```

Each action names exactly **one** target and sets exactly one status; marking
several targets means emitting several actions (rego set rules naturally
produce one action per target). The valid statuses depend on the concrete
`SyncBaseItem` implementation of the addressed target: the base set is
`pending`/`synced`/`error` plus item-class-defined additions (a subclass
declares which statuses policies may set — e.g. a ticketing item might add
`cancelled` to trigger remote cancellation instead of an update push). The
handler creates or flips the matching item row to the requested status,
recording the effective values snapshot when the status implies a push. Which
target a policy may name is constrained to the targets enabled for the type
(see 5); an unknown target or a status the item class does not allow from
policy is a dispatch error logged to history like other failed actions.

### 4.2 The worker

One Celery task (beat-scheduled + triggerable): fetch all `pending` items,
group by target, push, set `synced`/`error`. Properties pushed come from the
stored effective-values snapshot rendered through the target's binding config
(see 5). **Snapshot semantics are deliberate:** the worker pushes exactly
what was current when `mark_sync` fired — it never re-evaluates the policy at
push time. Later edits reach the remote only through a new marking (post-save
staleness rules, 4.3), which keeps pushes deterministic, auditable
(`synced_payload` is literally what was sent), and safe to retry. Bulk sync after the fact is the same task — transitions only ever set
`pending`; nothing pushes synchronously in the request cycle. A manual
"sync now" admin/bulk button enqueues the same task.

### 4.3 Staleness: rego post-save action

When a proposal or event is saved, its post-save policy can re-mark targets:

```rego
actions contains {"type": "mark_sync", "target": "caldav:main-calendar", "status": "pending", "phase": "post"} if {
    input.entity.workflow_state == "published"
}
```

There is **no field-level change detection** in the input (no
`input.changed_fields`): whether a re-mark is warranted is decided from the
stored per-target state — `derived_state == "stale"` (3.2) already means the
effective values differ from what was pushed, which is the only change that
matters for sync.

For proposal edits to re-mark *events that reference the proposal*, the action
handler supports a reverse direction:

```json
{"type": "mark_sync", "via_referencing": {"type": "event", "field": "origin"}, "target": "caldav:main-calendar", "status": "pending"}
```

meaning "for every event whose `origin` points at me, set this target's item
to the given status". This keeps the fan-out in the Python handler (a simple reverse FK
query) rather than requiring the proposal's policy input to contain all its
events.

## 5. Per-plugin type-editor tabs (extensible target configuration)

Each sync plugin contributes its own tab to the UDM type editor, alongside the
existing form-fields and data-fields tabs.

### 5.1 Backend registry

Each sync app registers a tab descriptor at startup (AppConfig.ready):

```python
register_type_editor_tab(
    id="sync_caldav",
    label="CalDAV Sync",
    config_schema=CalDAVTypeBindingSchema,   # pydantic
)
```

The type-config API exposes: the list of registered tabs, and per-type
CRUD for each tab's config blob (validated against the plugin's pydantic
schema, versioned together with the type config so bindings roll with config
versions). A plugin's binding config typically declares:

- which concrete targets (e.g. which `CalDAVSyncTarget` rows) are available
  for this type,
- how effective-value keys map to remote properties (field binding, e.g.
  `effective.start → DTSTART`, `effective.title → SUMMARY`),
- plugin-specific options (e.g. webhook URL/auth header selection; the
  webhook payload itself is always the effective JSON, not templated).

### 5.2 Frontend registry

The frontend keeps a registry `tabId → React component`, shipped in the main
bundle. The type editor asks the backend which tabs exist and renders the
registered component for each, passing the type id and the tab's config
CRUD endpoints. A new plugin therefore ships: a Django app (models, tasks,
tab descriptor, schema) + one React component registered by id. Tabs whose id
has no registered component render a fallback JSON editor so a backend-only
plugin is still configurable.

## 6. Calendar form element

A new form element `calendar` (config-declared like other display elements)
plus a standalone dashboard page using the same component.

Capabilities (all selected):

1. **Availability view** — renders entries from configured iCal imports
   (`sync_ical` items) and CalDAV calendars as busy/context blocks.
2. **Date picking** — clicking/dragging a slot writes the entity's configured
   start/end field slugs (declared in the element config:
   `binds: {start: start_override, end: end_override}`).
3. **UDM events** — entities of configured type(s) with date fields are
   rendered as calendar entries (colored by workflow state), fetched via a new
   date-range query endpoint on `api_entities`.
4. **Standalone dashboard calendar** — a route-level page showing the same
   merged view over all events, with click-through to the entity form.

Backend: one aggregation endpoint
`GET /api/udm/calendar?start=…&end=…&sources=…` returning normalized entries
`{source, uid, title, start, end, url?, entity_id?}` from the three source
kinds. iCal/CalDAV reads come from the already-synced local items (no live
remote fetch in the request path). Read access is policy-filtered per source:
UDM entries via the existing dashboard/view policies; external calendar
sources via a per-source role/permission setting.

Frontend: **DayPilot Lite** (`npm install
@daypilot/daypilot-lite-javascript`, Apache-2.0) wrapped as `CalendarPreview`
/ `CalendarField` in `src/udm-editors/`; its month/week/day components cover
the availability view, click/drag date picking, and the standalone dashboard
calendar.

## 7. apiv1 deprecation

Phased, no data migration:

1. **Freeze (now):** mark apiv1 deprecated in docs/README; no new code may
   import from `apiv1` except the sync base classes until 3 lands; frontend
   stops adding apiv1 calls.
2. **Detach:** land `sync_core` + migrated sync apps (3), the calendar element
   reading from synced items (6), and events-as-UDM (1–5). At this point
   nothing outside apiv1 imports apiv1.
3. **Remove:** delete the `apiv1` app, its endpoints, models, and migrations;
   drop its tables. Legacy event data disappears with it (accepted).
   The apiv1 Playwright UX tests are removed with the app.

## 8. Implementation checklist

Steps 1–5 are independent of 6–8 and can proceed in parallel. Each step
should land with its tests (`uv run manage.py test userdefinedmodel` plus the
new apps' suites; never the apiv1 suite).

### Step 1 — `entity_select` gap-closing (1.1)

- [x] Backlink reverse-lookup query helper: given an entity id, find all
      `FieldValue` rows on `entity_select` / `entity_select_multi` fields
      containing that id, returning (entity, type, field slug). Shared by
      delete protection (step 1), the backlink endpoint (step 5), and
      `backlink_inputs` (step 2). — `userdefinedmodel/backlinks.py`
      (`find_backlinks`, `backlink_summary`).
- [x] Delete protection: entity delete path refuses deletion while backlinks
      exist, error message listing referencing entities. — `api_entities.py`
      `delete_entity` returns 409 with the backlink summary.
- [x] Delete-policy input: add backlink summary (count, per referencing
      type + field slug) to the delete evaluation input; extend
      `policy_input.py` and `_input_schema.rego` accordingly. — added
      `backlink_summary` to `EntityActionInput`/`valid_input_doc`; a new
      `force_delete` result key (OR-ed like `allow`) added to `udm.rego`,
      `PolicyEvaluationOutput`, and the test-framework `RESULT_SUFFIX`.
- [x] Forced-delete path: when `delete.rego` allows despite backlinks, delete
      and leave referencing ids dangling. — example in `delete.rego`
      (`force_delete` gated on `input.user.sudo`).
- [x] Dangling-id semantics: verify every reader (engine `linked_entities`
      resolution, API serialization, previews) resolves a missing entity id
      to `null` / placeholder instead of erroring; add tests. — confirmed
      existing behavior (`build_lookup_maps` simply omits unresolved ids);
      `entity_select` additionally self-heals via `SET_NULL` on forced delete.
- [x] Immutability: document the rego save-rule pattern forbidding changes to
      an origin-type `entity_select` field once set (policy example in
      `documentation/configuration/policies/`); no schema change. — pattern
      documented at the end of `save.rego`.
- [x] Tests: protect blocks delete, policy override force-deletes, dangling
      id reads as null, immutability rule rejects edits. —
      `userdefinedmodel/tests/test_backlinks.py` (7 tests); full suite green
      (`uv run manage.py test userdefinedmodel`, 292 tests).

### Step 2 — dynamic link expansion (2)

- [x] Request-phase evaluation in `engine.py`: evaluate `linked_inputs` /
      `backlink_inputs` set rules against the base input (clone of compiled
      session). — `_eval_request_rules` / `_eval_optional_set_rule` read
      `data.udm.linked_inputs` / `data.udm.backlink_inputs` (aggregated in
      `udm.rego`, unioned across modules like `allow`); tolerates policies
      that never define them (regorus "not a valid rule path").
- [x] Path resolver: forward paths over `entity_select` slugs (any depth,
      `_MULTI` segments yield lists), producing `input.linked` shaped as
      NodeDocuments; keep the flat `linked_entities` map unchanged. —
      `_resolve_forward_path` / `_lookup_node_doc`.
- [x] Backlink resolver: `backlink_inputs` entries → `input.backlinks.<name>`
      lists (reuses the step-1 query helper). — `_resolve_backlink`, built on
      `backlinks.find_backlinks`.
- [x] Fixpoint loop: re-run the request phase with expanded input until the
      requested sets are stable; fixed iteration limit (e.g. 3); instability
      at the limit is an evaluation error. — `resolve_linked_and_backlinks`,
      `LINK_FIXPOINT_LIMIT = 3`, raises `LinkResolutionError`.
- [x] Schema updates: `linked` and `backlinks` members in `policy_input.py`,
      `_input_schema.rego`, `check_input_schema.py`; align with the rego
      contract refactor documents. — added to `EntityActionInput`,
      `valid_input_doc`, and the generated example documents (all 110
      examples still pass `check_input_schema.py`).
- [x] Tests: unconditional and input-conditional requests, deep paths,
      `_MULTI` lists, backlinks, fixpoint convergence and the instability
      error, null for dangling/missing targets. —
      `userdefinedmodel/tests/test_link_expansion.py` (7 tests); full suite
      green (`uv run manage.py test userdefinedmodel`, 299 tests).

### Step 3 — `create_linked_entity` action (1.2)

- [x] Register action via `policy_action` with pydantic schema:
      `target_type`, `reference_field`, `initial_fields`, `allow_multiple`
      (default true), `phase`. — `CreateLinkedEntityOutput` in `actions.py`.
- [x] Handler: create entity of `target_type`, set the `entity_select`
      `reference_field` to the triggering entity, apply `initial_fields`,
      run in the triggering EditGroup; respect the target type's workflow
      initial state. — `_handle_create_linked_entity`; new entity gets
      `materialize_defaults`/`materialize_user_defaults` (workflow initial
      state included) before `apply_patch(..., skip_policy=True)` seeds
      `initial_fields` + the reference; `target_type` is the UDMType id
      (UUID string, consistent with `limit_to_type_ids`).
- [x] `allow_multiple: false` no-op when a referencing entity already exists.
      — checked via `backlinks.find_backlinks` before creating.
- [x] Failure handling: unknown type/field or field-validation failure logged
      to history like other failed actions. — raises `ValueError`, caught by
      `dispatch_actions`' existing `on_error="log"` path.
- [x] Tests: creation on transition, multiple events per proposal, unique
      mode no-op, initial fields applied, failure logging. —
      `userdefinedmodel/tests/test_create_linked_entity.py` (6 tests); full
      suite green (`uv run manage.py test userdefinedmodel`, 305 tests).
- [x] **Demo bundle refinement**: the demo initially fired
      `create_linked_entity` on the main "accept" transition (fires once).
      Replaced with a **separate single-state workflow** ("Add Event
      Workflow": one state `ready`, one self-loop transition `add-event`
      `ready -> ready`) bound to a new `add_event` workflow field on
      Proposal. `transitions.rego` permits `add-event` only while the
      **main** `status` field reads `accepted` (`workflow.current_status`,
      unrelated to `add_event`'s own — constant — state);
      `proposals-actions.rego` fires `create_linked_entity` on that
      transition instead. Net effect: a moderator can click "Add event"
      repeatedly on an accepted proposal, each click creating one more
      linked Event — verified end-to-end (3 events from one proposal via
      2× `add-event` after accept). Event also gained an `event-id`
      `slug_id` field (prefix `EVENT-`), mirroring Proposal's `proposal-id`.

### Step 4 — effective values + markdown display + `entity_url` (1.3, 1.4, 1.6)

- [x] Output convention: `effective` object in `PolicyEvaluationOutput`;
      document the coalesce pattern in the policy docs/templates. —
      `effective` field added, unioned across modules in `udm.rego` like
      `additional_result`.
- [x] Markdown display field: type-config declares a Jinja template
      (mailtemplate engine) rendered server-side into a read-only display
      field; context = `effective`, `entity`, `linked`, `backlinks`, `sync`.
      — new `FormElement.ElementType.MARKDOWN_DISPLAY`, rendered by
      `display_templates.render_markdown_displays_for_entity` (reuses
      `mailtemplates.get_environment`/`jsonify_context`) into
      `EntityOut.markdown_displays`. **`sync` context key deferred** — added
      once `sync_core` (Step 6/7) exists to populate it. **Frontend
      component/registration for the new element type is not yet wired** —
      backend renders and serializes the markdown, but no React component
      consumes `markdown_displays` in `src/udm-editors/` yet.
- [x] `entity_url` Jinja filter: entity id → frontend form URL (base URL from
      settings); register in both markdown-display and mail template
      environments; frontend markdown renderer permits same-origin entity
      routes. — `project/jinja_filters.py::entity_url`, added to
      `SAFE_FILTERS`/`ALL_FILTERS` (used by both environments already); no
      frontend sanitizer change needed — `rehype-sanitize`'s default schema
      already permits `<a href>` on http(s) urls for ordinary markdown links.
- [x] Tests: coalesce example policy produces expected `effective`, template
      renders with links, filter output format, null-origin handling. —
      `userdefinedmodel/tests/test_effective_display.py` (10 tests); full
      suite green (`uv run manage.py test userdefinedmodel`, 315 tests).

### Step 5 — backlinks in the UI (1.5)

- [x] Endpoint on `api_entities`: backlinks of an entity, filterable by
      `source_type_ids` / `source_field_slug`, returning preview summaries
      (`summaries.py`) + workflow state; policy-filtered (view policy per
      referencing entity). — `GET /entities/{id}/backlinks/` in
      `api_entities.py`; a denied backlink is silently omitted, never
      surfaced as an error (matches the view-endpoint's not-found-not-403
      convention for existence-hiding).
- [x] `backlink_list` element type in `schemas.py` (`type_config`:
      `source_type_ids`, `source_field_slug`) + config validation. —
      `FormElement.ElementType.BACKLINK_LIST`, `BacklinkListTypeConfig`,
      validated via the new `_ELEMENT_TYPE_CONFIG_CLS` registry in
      `FormElementIn.validate_element` (also covers `markdown_display` from
      Step 4, previously unvalidated).
- [x] Frontend `BacklinkListPreview` in `src/udm-editors/`: renders preview
      summaries as clickable entries with workflow badge; register in
      `index.ts`. — `BacklinkListPreview.tsx` (fetches
      `udmGetEntityBacklinks`, navigates to `/udm-entity/:id` on click,
      colored workflow-state badge); exported from `udm-editors/index.ts`.
      Wired into `UdmEntityEditor.tsx`'s structural-field renderer alongside
      a `markdown_display` renderer (`UdfMarkdown`) and a minimal
      `sync_status` badge row (`entity.sync_items`) — both needed once
      `backlink_list`/`markdown_display`/`sync_status` started flowing
      through `viewable_fields` (see below). `apiUdm.ts` gained
      `udmGetEntityBacklinks`/`BacklinkOut` via a hand-typed `fetch` call,
      not the generated `openapi-fetch` client — `schema_udm.d.ts` is
      generated from a running backend and doesn't cover this endpoint or
      `EntityOut.markdown_displays`/`sync_items` yet. A `refreshToken`
      prop (bumped by `UdmEntityEditor`'s `load()` on every reload —
      transitions/actions on THIS entity can change backlink data that
      lives on an unrelated entity, so `entityId` alone doesn't change)
      forces a refetch after any transition, e.g. the repeatable
      `add-event` transition below.
- [x] Visibility of `backlink_list`/`markdown_display`/`sync_status` is an
      ordinary policy decision, not "always shown". — Added
      `FormElement.NO_VALUE_DISPLAY_TYPES` (`models/config.py`) and included
      it in `to_policy_document()`'s shape-compat emission (`models/node.py`)
      alongside `STRUCTURAL_TYPES`, so these three element types land in
      `entity.fields` with a null value and flow through the ordinary
      `viewable_fields` mechanism — a policy grants or withholds them exactly
      like any other field. Kept as a separate constant from
      `STRUCTURAL_TYPES` (rather than folding in) since rego's
      `config.STRUCTURAL_TYPES` static list (the save-grant exclusion) is a
      distinct, hand-maintained concept that this doesn't touch.
- [x] Tests: filtering, policy filtering hides unviewable entities, preview
      summary content. — `userdefinedmodel/tests/test_backlinks_endpoint.py`
      (5 tests); full suite green (`uv run manage.py test userdefinedmodel
      sync_core`, 334 tests); `npx tsc -b --noEmit` clean.

### Step 6 — `sync_core` + target migration (3)

- [x] New `sync_core` app: `SyncBaseTarget` (with `enabled`/soft-delete flag,
      `secret_field_names`) and `SyncBaseItem` (`related_entity` FK to
      `UserDefinedModelEntity`, `status` CharField validated against the item
      class's declared set, `last_error`, `synced_payload`, `is_stale`,
      `synced_at`, unique (entity, target)). — `sync_core/models.py`;
      `SyncBaseTarget.key` (unique slug) added beyond the original sketch as
      the rego-facing identifier for `input.sync`; `remote_uid` added to
      match the API shape described in §3.1. Registered in
      `default_settings.py` INSTALLED_APPS; `sync_core/migrations/0001_initial.py`.
- [ ] `SyncDiffData` / `PropertyDiff` moved to `sync_core`, diffing
      `synced_payload` vs. current effective values. — **deferred**: no
      concrete item class exists yet to diff against (lands with Step 7's
      worker / a ported plugin).
- [x] `derived_state` computation (pending / error / synced / stale /
      target_unavailable) as the single shared helper (3.2). —
      `sync_core.models.compute_derived_state`; a subclass-defined status
      outside the base three (e.g. `cancelled`) passes through as-is.
      `target_unavailable` covers target soft-delete; the "no longer bound to
      the type in the plugin tab config" case is deferred to Step 8 (no tab
      registry exists yet).
- [ ] Port `sync_ical`: import from `sync_core`, items keyed to UDM entities.
      — **deferred**, not attempted this pass (existing app, ~4000 lines
      across the three sync apps combined; a real port needs its own
      dedicated review, not a drive-by rewrite alongside the rest of this
      checklist).
- [ ] Port `sync_caldav`: same. — **deferred**, same reason.
- [ ] Port `sync_pretix`: same. — **deferred**, same reason.
- [ ] New `sync_webhook` app: target with URL, bearer token, constant custom
      headers (secrets via `secret_field_names`); POST body = effective JSON
      + entity id, target key, status, sequence number; no signing, no
      templating. — **deferred**; natural next slice once a first real
      target exists to validate the payload shape against (Step 7).
- [x] Soft-delete admin behavior for targets; hard delete only without items.
      — `SyncBaseTarget.delete()` raises `ProtectedError` while `items`
      exist; `enabled=False` is the intended soft-delete path (no Django
      admin registration yet — no admin app wiring done this pass).
- [x] No data/credential migration from apiv1 tables (deliberate). — new
      app, no migration data operations at all.
- [x] Expose `input.sync` map + template `sync` context + entity-API
      `sync_items` embedding (3.2); schema updates in `policy_input.py` /
      `_input_schema.rego`. — `engine.py::_sync_map_for_node` (lazy import,
      the one place `userdefinedmodel` reads back from `sync_core`, keeping
      the declared one-way dependency at the model-FK level only);
      `EntityOut.sync_items`; `sync` added to both the markdown-display
      context (`display_templates.py`) and the mail-template context
      (`actions.build_notification_context`). **Not yet done:** enriching
      backlink documents (`input.backlinks.<name>[].sync`) — only the root
      entity's own `input.sync` is populated so far.
- [x] `sync_status` display element (badges: state, error, last-synced) in
      `schemas.py` + frontend component. — `FormElement.ElementType.SYNC_STATUS`
      + `SyncStatusTypeConfig` registered in `_ELEMENT_TYPE_CONFIG_CLS`.
      **Frontend component not built** (same gap as `backlink_list` in Step 5).
- [x] Tests: derived_state matrix (incl. stale and target_unavailable),
      soft-delete visibility, webhook payload shape and headers, per-class
      status validation. — `sync_core/tests/test_derived_state.py` (11
      tests covering the derived_state matrix, hard-delete protection,
      summary shape) + `userdefinedmodel/tests/test_sync_input.py` (3 tests
      for `input.sync` / `EntityOut.sync_items`). Webhook payload/header
      tests are N/A until `sync_webhook` exists. Full suite green
      (`uv run manage.py test userdefinedmodel sync_core`, 334 tests).

### Step 7 — `mark_sync` action + worker (4)

- [x] Register `mark_sync` action: single `target`, single `status`, optional
      `via_referencing {type, field}`, `phase`. — `MarkSyncOutput` +
      `ViaReferencingSpec` in `actions.py`.
- [x] Handler: create/flip the item row; snapshot `effective` when the status
      implies a push; validate target enabled for the type and status allowed
      by the item class; dispatch error otherwise. — `_handle_mark_sync` +
      `sync_core.models.mark_sync`; "status implies a push" = `status ==
      "pending"` (the base contract's only push-triggering status; a
      subclass-defined status doesn't imply one). **Partial**: validates the
      target is `enabled`, not yet "enabled for the type" — that needs the
      Step 8 plugin-tab registry, which doesn't exist.
- [x] `via_referencing` fan-out via the reverse-lookup helper. — reuses
      `backlinks.find_backlinks`, filtered by `type`/`field`.
- [x] Post-save staleness: after evaluation, compare fresh `effective`
      against each item's `synced_payload`, store `is_stale`. No
      `input.changed_fields` (deliberate). — `sync_core.models.
      recompute_staleness`, called after POST-phase action dispatch in both
      `writer.apply_patch` (save) and `engine.execute_transition`
      (transition); lazy import, `except Exception: pass` guarded like the
      other `sync_core` read-back in `engine.py`.
- [x] Celery worker (beat + manually triggerable): process `pending` items
      grouped by target, push the stored snapshot, set `synced`/`error`;
      one attempt per marking, no default retry (item classes may override
      with their own backoff); manual bulk-sync trigger enqueues the same
      task. — `sync_core/tasks.py`: `push_pending_sync_items()` (plain
      function, synchronous, same convention as
      `userdefinedmodel.tasks.run_bulk_migration`) + `push_pending_sync_items_task`
      (`@shared_task` wrapper). `SyncBaseItem.push()` raises `NotImplementedError`
      in the base class — no concrete item class exists yet (Step 6 port
      deferred), so every push currently fails with a clear, catchable error
      rather than silently doing nothing. **Not done**: an actual
      `django_celery_beat` `PeriodicTask` row (no existing convention in
      this codebase to follow — beat schedules appear to be configured via
      the admin UI at deploy time, not in code) and an admin "sync now"
      button (no admin UI work in this pass).
- [x] Tests: mark on transition, post-save re-mark on stale, snapshot
      immutability (later edits don't change what's pushed), error stays
      until re-marked, per-class status rejection, via_referencing fan-out.
      — `sync_core/tests/test_mark_sync.py` (9 tests, incl. the worker);
      full suite green (`uv run manage.py test userdefinedmodel sync_core`,
      344 tests).

### Step 8 — plugin type-editor tabs (5)

- [ ] Backend tab registry: `register_type_editor_tab(id, label,
      config_schema)` called from each sync app's `AppConfig.ready`.
- [ ] API: list registered tabs; per-type CRUD of each tab's config blob,
      validated against the plugin's pydantic schema, versioned with the type
      config.
- [ ] Binding config contents per plugin: available concrete targets,
      effective-key → remote-property field binding (webhook: URL/auth
      selection only).
- [ ] Frontend tab registry (`tabId → component`) in the type editor; tab
      components for caldav / ical / pretix / webhook; JSON-editor fallback
      for tabs without a registered component.
- [ ] Tests: registry listing, schema validation of config blobs, versioning
      with type config, fallback rendering.

### Step 9 — calendar (6)

- [ ] Aggregation endpoint `GET /api/udm/calendar?start=…&end=…&sources=…`
      returning normalized entries `{source, uid, title, start, end, url?,
      entity_id?}` from synced iCal items, CalDAV items, and UDM entities
      (date-range query on configured date fields); no live remote fetches.
- [ ] Access control: UDM entries via dashboard/view policies; external
      sources via per-source role/permission setting.
- [ ] `npm install @daypilot/daypilot-lite-javascript`; wrap as
      `CalendarPreview` / `CalendarField` in `src/udm-editors/`, register in
      `index.ts`.
- [ ] Form element `calendar` config in `schemas.py`: sources, entity types
      to show, `binds: {start, end}` field slugs; click/drag writes the bound
      fields.
- [ ] Standalone dashboard calendar route reusing the same component, colored
      by workflow state, click-through to entity forms.
- [ ] Tests: aggregation filtering and normalization, policy filtering,
      date-write binding.

### Step 10 — apiv1 removal (7)

- [ ] Preconditions verified: no imports of `apiv1` outside `apiv1/`
      (`grep -rn "from apiv1\|import apiv1" backend/ --include=*.py`), no
      frontend calls to apiv1 endpoints, sync apps fully on `sync_core`.
- [ ] Delete the `apiv1` app: code, URLs, admin, Playwright UX tests.
- [ ] Migrations to drop apiv1 tables (legacy event data disappears —
      accepted).
- [ ] Remove apiv1 references from settings, docs, and OpenWiki sources; let
      OpenWiki regenerate.
- [ ] Full test run of remaining suites; smoke-test sync round trip against
      a real CalDAV target.

## 9. Open questions

None — all questions raised during drafting have been decided and folded into
the sections above.
