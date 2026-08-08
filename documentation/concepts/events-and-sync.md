# Concept: Events as UDM Entities, Calendar Element, and Workflow-Driven Sync

Status: draft concept, 2026-08-08. Decisions below were made interactively; open
questions are collected at the end.

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
| Event ↔ proposal link | New `entity_reference` field kind (FK to another UDM entity, allowed target type declared in config) |
| Rego linked-entity access | Declared link expansion; declaration lives **in the policy file** (metadata annotation) |
| Sync framework | New `sync_core` app; `SyncBaseItem.related_entity` = FK to `UserDefinedModelEntity` |
| apiv1 | Freeze, then remove. **No data migration.** |
| Field overrides | Explicit override fields on the event + rego coalesce into an `effective` object |
| Effective-values display | Rego returns structured values; a template (mailtemplate-style) renders markdown into a display field |
| Calendar element | Availability view + date picking + shows UDM events + standalone dashboard variant |
| Sync trigger | Transition action sets per-target pending status; Celery worker pushes everything pending (bulk = same worker) |
| Event creation | Workflow transition action on the proposal creates the linked event |
| Ticketing targets | Pretix (adapted) + a new generic webhook target |
| Per-target sync state | One `SyncItem` row per (entity, target) with status `pending/synced/error`; not workflow lanes, not injected fields |
| Target configuration UI | Per-plugin **type-editor tab**, extensible: backend registry + frontend component registry keyed by tab id |
| Staleness after sync | Rego **post-save action** re-marks affected targets pending when relevant values change |

## 1. Event as a UDM type

An "Event" is an ordinary `UserDefinedModelType` with its own config, fields,
workflow, and policies. Nothing in the engine knows the word "event"; the
behaviors below are generic capabilities that any type can use.

### 1.1 New field kind: `entity_reference`

A new field data type in the type config:

```yaml
- slug: origin
  data_type: entity_reference
  target_type: proposal        # UDM type name the reference must point to
  required: true
  immutable_after_create: true # optional; for origin links usually true
```

Storage: `FieldValue` gains (or reuses, via the typed-value mechanism) a FK/UUID
column referencing `UserDefinedModelEntity`. Validation on save checks the
target exists and has the configured type. Deletion behavior of the target
entity (protect vs. null) is part of the field config (default: protect).

The frontend renders it as an autocomplete picker (reusing
`api_autocomplete`), read-only when `immutable_after_create` and the entity
already exists.

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
creates a new entity of `target_type`, sets its `entity_reference` field
`reference_field` to the triggering entity, and initializes fields. It runs in
the same EditGroup as the transition, consistent with existing action handlers.
Idempotency: if an entity of that type already references this proposal via
that field, the action is a no-op (configurable `allow_multiple: true` to
opt out).

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

## 2. Rego access to linked entities

### 2.1 Declared link expansion

The policy input is extended with `input.linked`, containing pre-resolved
documents for declared link paths. Resolution happens in `policy_input.py`
before evaluation — no OPA callbacks, no lazy fetching, evaluation stays pure
and cacheable.

### 2.2 Declaration in the policy file

Links are declared as rego metadata annotations at package level:

```rego
# METADATA
# custom:
#   linked_inputs:
#     - origin
#     - origin.owner
package udm.types.event
```

The engine parses annotations (OPA exposes them via `ast`/`opa inspect`; the
Python side can parse the YAML metadata block directly) when a policy version
is saved, stores the resolved list on the policy version record, and the input
builder uses that stored list at evaluation time — parsing happens once per
policy save, not per evaluation.

Path semantics:

- Each path segment is an `entity_reference` field slug on the current level's
  type; `origin.owner` means "follow `origin`, then that entity's `owner`".
- A path resolves to a `NodeDocument`-shaped object (fields, workflow state,
  type name, id) — the same shape as `input.entity`, so rules are reusable.
- Broken/unset references resolve to `null`; policies must handle that.
- Depth limit (e.g. 3 segments) and a cap on expanded entities guard against
  pathological configs. To-many expansion (e.g. `origin.reviews`) is a listed
  open question.

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
  apiv1 `Event` model. A new `sync_webhook` app provides the generic
  HTTP target (configurable URL, auth header, payload template over
  `effective`).
- Existing sync item rows referencing apiv1 events are **not migrated**
  (consistent with the no-migration decision); targets (server credentials,
  URLs) are worth carrying over manually or via a small one-off command.

### 3.1 Per-target sync state

The `status` on `SyncBaseItem` is the "workflow status mark per sync target":

- `pending` — marked for push; the worker will pick it up.
- `synced` — remote is up to date with `synced_payload`.
- `error` — last push failed; `last_error` holds the reason; worker retries
  with backoff, UI shows the error badge.

The entity's own workflow state remains single-valued; per-target sync state
lives only in these rows. The entity API embeds
`sync_items: [{target, status, remote_uid, last_error}]` so the frontend can
show per-target badges on forms and dashboards.

## 4. Workflow-driven sync

### 4.1 Marking for sync (transition)

A new registered policy action:

```json
{"type": "mark_sync_pending", "targets": ["caldav:main-calendar", "pretix:prod"], "phase": "post"}
```

Emitted by transition policies (e.g. on "publish"). The handler creates or
flips the matching `SyncBaseItem` rows to `pending`, recording the effective
values snapshot to be pushed. Which targets a policy may name is constrained
to the targets enabled for the type (see 5); unknown targets are a dispatch
error logged to history like other failed actions.

### 4.2 The worker

One Celery task (beat-scheduled + triggerable): fetch all `pending` items,
group by target, push, set `synced`/`error`. Properties pushed come from the
stored effective-values snapshot rendered through the target's binding config
(see 5). Bulk sync after the fact is the same task — transitions only ever set
`pending`; nothing pushes synchronously in the request cycle. A manual
"sync now" admin/bulk button enqueues the same task.

### 4.3 Staleness: rego post-save action

When a proposal or event is saved, its post-save policy can re-mark targets:

```rego
actions contains {"type": "mark_sync_pending", "targets": ["caldav:main-calendar"], "phase": "post"} if {
    input.entity.workflow_state == "published"
    # optionally: only when sync-relevant fields changed
}
```

For proposal edits to re-mark *events that reference the proposal*, the action
handler supports a reverse direction:

```json
{"type": "mark_sync_pending", "via_referencing": {"type": "event", "field": "origin"}, "targets": [...]}
```

meaning "for every event whose `origin` points at me, mark these targets
pending". This keeps the fan-out in the Python handler (a simple reverse FK
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
- plugin-specific options (e.g. webhook payload template).

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

Frontend: use an established calendar component (evaluate PrimeReact
compatibility; likely FullCalendar) wrapped as `CalendarPreview` /
`CalendarField` in `src/udm-editors/`.

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

## 8. Suggested implementation order

1. `entity_reference` field kind + validation + frontend picker (1.1)
2. `input.linked` + policy-metadata link declaration + schema updates (2)
3. `create_linked_entity` action (1.2)
4. Effective-values pattern: rego output convention + markdown template
   display field (1.3–1.4)
5. `sync_core` app + migrate `sync_ical` / `sync_caldav` / `sync_pretix`,
   add `sync_webhook` (3)
6. `mark_sync_pending` action (incl. `via_referencing`) + worker (4)
7. Type-editor tab registries (backend + frontend) + per-plugin binding
   configs (5)
8. Calendar element + aggregation endpoint + dashboard page (6)
9. apiv1 removal (7)

Steps 1–4 are independent of 5–7 and can proceed in parallel.

## 9. Open questions

- **To-many link expansion:** should `linked_inputs` support reverse/list
  paths like `origin.reviews` (lists of NodeDocuments), and with what caps?
- **Effective-values snapshot vs. live:** worker pushes the snapshot taken at
  marking time (current design). Should it instead re-evaluate the policy at
  push time so the very latest values win even without a re-mark?
- **Which calendar component** (FullCalendar vs. alternatives) fits the
  PrimeReact-based frontend and licensing constraints?
- **Webhook target contract:** payload template language (Jinja over
  `effective`?), retry/backoff policy, signature header for receivers?
- **Change detection for staleness:** should `mark_sync_pending` post-save
  rules get a diff of changed field slugs in input (e.g.
  `input.changed_fields`) so re-marking can be limited to sync-relevant
  fields?
- **Target credentials handoff:** one-off management command to copy existing
  sync target rows (server URLs, credentials) from the apiv1-era tables into
  `sync_core`, or manual re-entry?
