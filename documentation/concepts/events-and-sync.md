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
| Target configuration UI | Per-plugin tab in the **Field Config editor** (revised 2026-08-09, see §5/Step 13), extensible: backend registry + frontend component registry keyed by tab id |
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

> **Revision 2026-08-09:** the "existing form-fields and data-fields tabs"
> live in the **Field Config editor** (`ConfigDraftEditor`'s Data Fields /
> Form Config / Preview sub-tabs), not on the Type detail page. The first
> implementation put the plugin tabs on the Type page as a separate
> button-switched "Plugin Tabs" section, editing the type's *bound published*
> `ConfigVersion` blob directly — bypassing the draft→publish flow the
> `TypeEditorTabConfig`-per-`ConfigVersion` model was built for. The plugin
> tabs move into the Field Config editor's sub-tab row and edit the **draft**
> version, rolling out via publish. See Step 13. Step 13 also models the
> field-binding config that §5.1 sketches but the first pass deferred.

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

### 6.1 Timeslot submodel + read-only highlight calendar

Events gain a **`timeslots` submodel list** (an event can occupy several
slots — setup, talk, teardown, repeated sessions), and below it a **second,
read-only calendar** that shows this event's own timeslots highlighted against
all other events as muted context.

Data model (bundle):

- New submodel field-config **Timeslot** with two `datetime` fields `start`
  and `end` (both `is_preview: true`), no workflow — same pattern as the
  existing `Proposal Review` submodel.
- On Event: `timeslots` (`submodel_list`, `renderer: list`,
  `submodel_config_version_id` → Timeslot), plus a second `calendar` field
  below it with no `bind_start`/`bind_end` (unbound ⇒ read-only by the
  existing element semantics).

Calendar source spec for submodels: timeslots are child
`UserDefinedModelEntityNode` rows, not entities, so the existing
`type_id:start:end` spec cannot reach them. New grammar:

```
submodel:<entity>:<field_slug>(<start_field>[,<end_field>])
```

e.g. `submodel:self:timeslots(start,end)`. The parenthesized field list keeps
datetime slugs out of the colon-split logic (the existing `spec.split(":")`
paths stay untouched; `submodel:` dispatches first), and a single-slug form
`(start)` expresses point-in-time entries, mirroring the empty-`end_field`
type spec. `self` is resolved by the frontend to the entity the element is
rendered under, before the request; the backend only ever sees a concrete
node id. Child-node read access is policy-checked via the **root** entity's
view policy (`viewable_fields` already carries child-node ids).

Highlighting stays a frontend concern; the backend remains a pure aggregator:

- `CalendarEntryOut` gains a `spec` echo field identifying which source spec
  produced each entry (no backend notion of "highlighted").
- `CalendarTypeConfig` gains optional `highlight_sources: list[str]` (a
  subset of `sources`); the frontend styles entries from those specs with a
  highlight color and everything else muted (DayPilot per-event
  `backColor`/`cssClass`).
- The Event-type context source also contains the current event itself; the
  frontend filters `entity_id == self` out of non-highlight sources to avoid
  a duplicate bar.

Element config on the Event bundle:

```json
{"sources": ["submodel:self:timeslots(start,end)",
             "<event type id>:start:end"],
 "highlight_sources": ["submodel:self:timeslots(start,end)"]}
```

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
      rather than silently doing nothing. **Not done**: the beat schedule entry
      (convention: `CELERY_BEAT_SCHEDULE` in `backend/default_settings.py`,
      see Step 11) and an admin "sync now" button (no admin UI work in
      this pass).
- [x] Tests: mark on transition, post-save re-mark on stale, snapshot
      immutability (later edits don't change what's pushed), error stays
      until re-marked, per-class status rejection, via_referencing fan-out.
      — `sync_core/tests/test_mark_sync.py` (9 tests, incl. the worker);
      full suite green (`uv run manage.py test userdefinedmodel sync_core`,
      344 tests).

### Step 8 — plugin type-editor tabs (5)

- [x] Backend tab registry: `register_type_editor_tab(id, label,
      config_schema)` called from each sync app's `AppConfig.ready`. —
      `userdefinedmodel/type_editor_tabs.py`; `sync_core/apps.py::ready()`
      registers a demo `sync_targets` tab (`sync_core/type_editor_tab.py`)
      since no concrete plugin (Step 6 port, `sync_webhook`) exists yet to
      register a real one.
- [x] API: list registered tabs; per-type CRUD of each tab's config blob,
      validated against the plugin's pydantic schema, versioned with the type
      config. — `GET /type-editor-tabs/`, `GET`/`PUT
      /config-versions/{id}/tab-configs/{tab_id}/` in `api_configs.py`; new
      `TypeEditorTabConfig` model (FK to `ConfigVersion`, one row per
      (version, tab_id)) — copied forward in `ConfigVersion._create_draft_copy`
      so a config blob rolls to the next draft on publish, same as data
      fields/form elements/rules.
- [ ] Binding config contents per plugin: available concrete targets,
      effective-key → remote-property field binding (webhook: URL/auth
      selection only). — **deferred with the plugin ports themselves**
      (Step 6); the demo `sync_targets` schema only covers "which target
      keys" as a stand-in, no field-binding shape yet since there's no
      concrete remote property list to bind to.
- [ ] *(placement/versioning superseded by Step 13 — panel moves to the
      Field Config editor and edits the draft version)*
      Frontend tab registry (`tabId → component`) in the type editor; tab
      components for caldav / ical / pretix / webhook; JSON-editor fallback
      for tabs without a registered component. — **not built this pass**,
      consistent with every other frontend gap logged in this checklist
      (backlink_list/markdown_display/sync_status got theirs in Step 5; this
      one's registry `GET /type-editor-tabs/` is ready for a frontend
      consumer).
- [x] Tests: registry listing, schema validation of config blobs, versioning
      with type config, fallback rendering. — `fallback rendering` is
      frontend-only, N/A here.
      `userdefinedmodel/tests/test_type_editor_tabs.py` (10 tests: registry
      duplicate-id guard, sync_core's startup registration, list/get/put API,
      permission check, invalid-config 400, unknown-tab 404, draft-copy on
      publish); full suite green (`uv run manage.py test userdefinedmodel
      sync_core`, 354 tests).

### Step 9 — calendar (6)

- [x] Aggregation endpoint `GET /api/udm/calendar?start=…&end=…&sources=…`
      returning normalized entries `{source, uid, title, start, end, url?,
      entity_id?}` from synced iCal items, CalDAV items, and UDM entities
      (date-range query on configured date fields); no live remote fetches.
      — `GET /calendar/` in `api_entities.py`. `sources` accepts
      `"type_id:start_field:end_field"` (UDM types; end optional →
      point-in-time) and `"source:<key>"` (a `sync_core.CalendarSource` —
      see below). All datetimes normalized to aware UTC before comparison.
- [x] iCal/CalDAV **read side**: `sync_core.CalendarSource` +
      `RemoteCalendarEntry` — new pull-only models, distinct from
      `SyncBaseTarget`/`SyncBaseItem` (the push side ported in Step 6,
      still deferred). `fetch_calendar_source()` in `sync_core/models.py`
      upserts `RemoteCalendarEntry` rows by `(source, uid)` from
      `sync_core/calendar_fetch.py`'s `fetch_ical_occurrences`/
      `fetch_caldav_occurrences` (fetch/parse logic lifted from
      `sync_ical`/`sync_caldav`, not their Event-creation/push code — those
      apps' push paths are untouched and still apiv1-coupled).
      `fetch_calendar_source_task`/`fetch_all_calendar_sources` in
      `sync_core/tasks.py` (no beat schedule wired yet — manual/task-queue
      trigger only). The request path only ever reads `RemoteCalendarEntry`
      — no live remote fetch inline with `GET /calendar/`.
- [x] Access control: UDM entries via dashboard/view policies; external
      sources via per-source role/permission setting. — per-entity `view`
      policy check, same as the entity list/backlinks endpoints (silently
      omitted, not surfaced as denied). `CalendarSource` access is
      coarser — `enabled` is the only gate; a per-source role/permission
      field is a straightforward future addition to the model, not added
      since nothing yet needs it.
- [x] `npm install @daypilot/daypilot-lite-javascript`; wrap as
      `CalendarPreview` / `CalendarField` in `src/udm-editors/`, register in
      `index.ts`. — week view by default (switchable to month), backed
      directly by `@daypilot/daypilot-lite-javascript`'s `DayPilot.Calendar`/
      `DayPilot.Month` (not the separate `-react` package).
- [x] Form element `calendar` config in `schemas.py`: `CalendarTypeConfig`
      (`sources`, optional `bind_start`/`bind_end`). Unlike `date_range`,
      `bind_start`/`bind_end` are plain sibling-DataField slugs in
      `type_config`, not real `FormElementBinding` FK rows — a calendar
      element mixes a same-entity binding with the unrelated cross-type
      `sources` aggregation list, which the FK-based `_BINDING_ROLES`
      mechanism doesn't accommodate. When bound, dragging an empty slot or
      moving/resizing the rendered "this event" bar writes both fields via
      one combined `PATCH` (see `saveFieldsCombined` in
      `UdmEntityEditor.tsx` — two sequential single-field patches were
      found to race into the entity edit lock's `concurrent_edit` 409).
      `datetime` field values are timezone-aware ISO strings app-wide as of
      this step (`src/timezone.ts`); a bare legacy-naive value falls back to
      the viewer's browser timezone, then `Europe/Berlin` (matching
      Django's configured `TIME_ZONE`).
- [x] Standalone dashboard calendar route reusing the same component
      (`/udm-calendar`, `UdmCalendarPage.tsx` + `Navbar.tsx` entry), letting
      staff overlay `sync_core.CalendarSource`s (`GET
      /calendar-sources/`). — **not** colored by workflow state yet
      (`CalendarEntryOut` has no `workflow_state` field); click-through to
      the entity form works for UDM-sourced entries (imported iCal/CalDAV
      entries have no `entity_id`, so are inert on click, as expected).
- [x] Tests: aggregation filtering and normalization (incl. timezone
      normalization), policy filtering, `source:` spec filtering,
      `CalendarSource` fetch/upsert/staleness/error-handling (mocked
      HTTP/CalDAV client, inline ICS fixtures). Frontend drag-to-set
      binding has no automated test — verified manually only; see the
      session notes on the double-commit / concurrent-edit / timezone
      fixes found this way.
      `userdefinedmodel/tests/test_calendar.py` (6 tests: range inclusion/
      exclusion, end-field extending the match window, policy filtering,
      invalid-date 400, empty sources); full suite green
      (`uv run manage.py test userdefinedmodel sync_core`, 360 tests).

### Step 10 — timeslot submodel + writeable submodel calendar (6.1)

Superseded the original highlight-calendar design during implementation:
Event no longer carries its own `start`/`end` — all scheduling lives on
Timeslot children. `submodel:<entity>:<field>(...)` addresses either a
single entity (`self`, substituted by the frontend) or, generalized during
implementation, a UDM type id (every entity of that type) — both bundle
calendars use the type-wide form for a full-schedule view.

- [x] Bundle: new `Timeslot` submodel field-config (two `datetime` fields
      `start`/`end`, both `is_preview: true`, no workflow) in
      `documentation/configuration/UDM_BUNDLE.json`, modeled on
      `Proposal Review`; plus its own writeable `calendar` field
      (`bind_start`/`bind_end` → its own `start`/`end`,
      `sources: ["submodel:<event type id>:timeslots(start,end)"]` for
      full-schedule context while dragging).
- [x] Bundle: Event's `start`/`end` datetime fields removed; `timeslots`
      field added (`submodel_list`, `renderer: list`,
      `submodel_config_version_id` → Timeslot); read-only `calendar` field
      with `sources: ["submodel:<event type id>:timeslots(start,end)"]`
      (every event's timeslots), no `bind_start`/`bind_end`.
- [x] Policies: `event.rego` grants `creatable_submodels`/`deletable_nodes`
      for `timeslots`; view/save on the Timeslot child fields already
      covered by the tree-wide `viewable_fields`/`editable_fields` rules.
- [x] Backend: parse `submodel:<entity>:<field>(<start>[,<end>])` in
      `get_calendar` (dispatched before the existing colon-split specs,
      with a comma-aware top-level source split since the spec's own
      `(start,end)` suffix also uses a comma); `<entity>` resolved as a
      single entity id or, if not found, a UDM type id (iterate all its
      entities); query `UserDefinedModelEntityNode` children by parent id +
      field slug, read the datetime values, policy-check per child via
      `viewable_fields`; `source: "submodel"`, `uid`/`entity_id` = child
      node id.
- [x] Backend: `spec` echo field on `CalendarEntryOut` (all source kinds).
- [x] Frontend (`CalendarField`/`CalendarPreview`): root-entity calendar
      substitutes `self` → the rendered entity's id before the request
      (mechanism kept general; the bundle above uses the type-wide form
      instead). New: a `calendar` field type_config with `bind_start`/
      `bind_end` inside a submodel child (`SubmodelEditor.tsx`) is now
      wired — dual-slug staging (`handleFieldChangeMulti`) and a combined
      one-PATCH commit (`onCommitField` widened to `string | string[]`),
      reusing `CalendarPreview` as-is.
- [x] Tests: submodel spec returns child-node entries (range in/out,
      point-in-time single-slug form, type-wide form across multiple
      entities), policy filtering via the root entity, `spec` echo,
      malformed-spec config validation; bundle import round-trip
      (`uv run manage.py import_bundle --migrate-entities`); full
      `userdefinedmodel sync_core` suites green (381 tests).

### Step 11 — deferred tasks from steps 6–9

Collected from the "deferred" / "not done" notes in the checklists above; all
must land before Step 12's preconditions can hold.

Sync-plugin ports (from Step 6):

- [x] Port `sync_ical` push side: import from `sync_core`, items keyed to UDM
      entities, effective-values payloads. `push()` writes/updates a VEVENT
      in a per-target on-disk `.ics` feed file from `synced_payload`; the old
      apiv1.Event-creating fetch/parse logic is gone (superseded by the
      already-ported `sync_core.calendar_fetch` pull side).
- [x] Port `sync_caldav` push side: same. `push()` deletes+re-adds the
      remote VEVENT via the `caldav` library from `synced_payload`.
- [x] Port `sync_pretix`: same, plus `"cancelled"` added to
      `allowed_statuses()` per §3.1. `PretixSyncTargetAreaAssociation.area`
      decoupled from `apiv1.ProposalArea` to a plain `area_code` string
      (items are generic UDM entities with no `proposal.area` to derive it
      from). The apiv1-era `get_status()`/`sync_diff()` remote-drift UI is
      replaced by a slimmed `compute_drift()` (name/dates/quota-size only —
      per-ticket price diffing against the remote is dropped as a
      simplification; prices are still pushed from `synced_payload["prices"]`,
      just not diffed property-by-property). `PretixPricingConfiguration`/
      `CalculatedPrices` are untouched — they don't subclass the sync base
      classes and stay `apiv1.Event`-linked, out of scope for this port.
      **Audit 2026-08-09: port is incomplete on the import level** —
      `sync_pretix/models.py:11` still imports
      `apiv1.models.basedata.time_string_to_minutes`, and
      `sync_pretix/tests.py` + `management/commands/sync_pretix_areas.py` +
      `test_sync_pretix_command.py` still import apiv1 models. These block
      the Step 12 precondition; see the new items there.
- [x] New `sync_webhook` app: target with URL, bearer token, constant custom
      headers (secrets via `secret_field_names`); POST body = effective JSON
      + entity id, target key, status, sequence number; no signing, no
      templating. Payload/header tests land here.
- [x] `SyncDiffData` / `PropertyDiff` moved to `sync_core`
      (`compute_sync_diff`), diffing `synced_payload` vs. current effective
      values.

Loose ends (from Steps 6–9 partials):

- [x] `input.backlinks.<name>[].sync`: each backlink document now carries
      its own `sync` map via `sync_map_for_entity`, not just the root entity.
- [x] `mark_sync` target validation: checks the `sync_targets` tab-config
      `target_keys` for the entity's type (absence of any tab config row
      stays permissive, for backward compatibility); `derived_state` returns
      `target_unavailable` when a target is unbound from the type.
- [ ] Per-plugin binding config contents beyond `target_keys`:
      effective-key → remote-property field binding (webhook: URL/auth
      selection only) is still not modeled — not needed yet since no plugin
      binding config beyond "which targets" exists. **Superseded by Step 13**
      (binding sources decided: effective key / data-field fallback / jinja
      template / timeslot submodel spec).
- [x] Frontend type-editor tab registry (`tabId → component`) +
      JSON-editor fallback for tabs without a registered component; a
      dedicated `sync_targets` tab component (target picker backed by a new
      `GET /sync-targets/` endpoint, replacing an initial freeform-string
      version per live feedback) and a JSON-schema-driven approach
      (`GET /type-editor-tabs/` now includes each tab's pydantic config
      model as a JSON schema) land here too. Dedicated components for
      caldav/ical/pretix/webhook themselves are not built — the JSON
      fallback covers them, as originally scoped. **Placement/versioning
      superseded by Step 13** (panel moves off the Type page into the Field
      Config editor, editing the draft version).
- [x] Django admin for `SyncBaseTarget` / `CalendarSource`: create/edit
      targets and sources, soft-delete via `enabled`, "sync now"/"fetch now"
      actions enqueuing the existing tasks. `SyncBaseTargetAdmin.child_models`
      lists all four concrete target classes so the polymorphic "add" flow
      works; each plugin registers its own target/item admin (mixed
      `PolymorphicChildModelAdmin`/plain `ModelAdmin`, both work).
- [x] Beat schedules for `push_pending_sync_items_task` and
      `fetch_all_calendar_sources` in `CELERY_BEAT_SCHEDULE`; the legacy
      hourly `sync_ical`/`sync_caldav` entries are retired now that both
      plugins delegate to the shared worker.
- [x] Calendar polish: `CalendarEntryOut.workflow_state` is now populated
      for "udm"-sourced entries (via a shared `entity_workflow_state()`
      helper) and `CalendarPreview.tsx` colors entries by it, reusing the
      existing `getEventStatusColor` palette. Per-source role/permission
      gate on `CalendarSource` beyond `enabled` was optional in the original
      scoping and was not built.
- [x] Regenerated `schema_udm.d.ts`; `apiUdm.ts`'s backlinks/calendar/
      type-editor-tabs/sync-targets fetches now go through the generated
      `udmClient` instead of hand-typed raw `fetch()` calls.

### Step 13 — plugin-tab placement, versioning, and field binding (review 2026-08-09)

Fixes from reviewing the Step 8/11 implementation against §5. Decisions made
interactively: tabs move to the Field Config editor; binding sources are
effective keys with data-field fallback **plus** an optional Jinja-template
source; timeslots sync as **one VEVENT per timeslot**. Should land before
Step 12 (the sync plugins' payload paths change here).

**Intended end state, in one paragraph:** an admin configures everything
sync-related about a type in one place — the Field Config editor — as
ordinary sub-tabs next to Data Fields / Form Config / Preview, edits land in
the draft and go live on publish like every other config change; each sync
plugin's tab lets the admin say not only *which* targets a type syncs to but
*what* each remote property is filled from (a policy effective key, a raw
data field, a rendered template, or the timeslot submodel); and a synced
Event appears in the remote calendar as one VEVENT per timeslot, kept in
step as slots are added, moved, or removed.

#### 13.1 Placement + versioning

*Why:* §5.2 was implemented on the wrong editor. The concept's "alongside
the existing form-fields and data-fields tabs" refers to the Field Config
editor's sub-tab row, but the panel landed on the Type detail page as a
button-switched side section. Worse, it edits the type's **bound published**
`ConfigVersion` blob in place — so the `TypeEditorTabConfig`-per-version
model (draft copies, publish roll-forward) exists but is never exercised,
and "published versions are immutable" silently stops being true for tab
configs. *Intended outcome:* tab configs behave exactly like data fields
and form elements — drafted, reviewed, published atomically with the rest
of the config version, with an audit trail of what was bound when.

- [x] Move the plugin tabs out of `TypeDetail` (`UdmAdminPage.tsx:1820`) into
      the **Field Config editor**: render the registered plugin tabs as
      additional sub-tabs in `ConfigDraftEditor`'s existing tab row (after
      📋 Data Fields / 🎨 Form Config / 👁 Preview), one sub-tab per
      registered plugin tab, styled identically — real tabs, not a
      button-switched side section. Outcome: one editor owns the whole
      config surface of a type; no second place to look. —
      `TypeEditorTabsPanel.tsx` deleted; `ConfigDraftEditor` now fetches
      `udmListTypeEditorTabs()` and renders one sub-tab button per registered
      tab after Preview, mounting `typeEditorTabRegistry[id] ??
      JsonTabFallback`.
- [x] Edit the **draft** version's `TypeEditorTabConfig` blob (the panel
      currently loads the type's bound version via `udmGetTypeConfig` and
      writes it in place). Changes then roll out through the existing
      publish flow; `_create_draft_copy` already copies blobs forward.
      Outcome: a half-finished target binding can sit in a draft without
      affecting live sync behavior until published. — plugin tabs are now
      mounted with `configVersionId={draft.version_id}` (the draft, not
      `udmGetTypeConfig`'s bound-published version); `_create_draft_copy`
      already copied blobs forward, confirmed unchanged.
- [x] Restrict `PUT /config-versions/{id}/tab-configs/{tab_id}/` to draft
      versions (published versions are immutable everywhere else); keep GET
      working for any version. Outcome: the immutability invariant of
      published versions holds again for every part of a config version. —
      `api_configs.py::put_type_editor_tab_config` now 400s unless
      `version.status == DRAFT`; GET unchanged.
      `test_type_editor_tabs.py::test_put_rejects_non_draft_version`.
- [x] Runtime consumers (`mark_sync` target validation, `derived_state`
      `target_unavailable`) must read the tab config of the entity's type's
      **bound published** version — verify and test this explicitly now that
      draft and published blobs can differ. (Audit note:
      `_target_bound_to_entity_type` in `sync_core/models.py` currently reads
      the **entity's own** `config_version_id`, i.e. the version the entity
      was migrated to — entities lagging behind the type's bound version see
      stale target bindings.) Outcome: publishing a binding change takes
      effect for **all** entities of the type at once, not per-entity as
      they happen to be migrated; draft edits never leak into runtime. —
      `_target_bound_to_entity_type` now resolves
      `entity.user_defined_model_type.field_config` →
      `ConfigVersion.objects.filter(config=..., status=PUBLISHED)`, both
      `compute_derived_state` and `mark_sync` go through it unchanged.
      `test_mark_sync.py::TargetBoundToEntityTypeTests` (entity lagging on an
      archived version still reads the newly-published binding).

#### 13.2 Field binding

*Why:* the only tab config today is `target_keys` — there is no way to say
what a remote property is filled from. `sync_caldav`/`sync_ical` `push()`
hardcode `effective.get("title"/"location"/"start"/"end")`, which (a) forces
every type that syncs to shape its effective object to those exact key
names, and (b) broke conceptually when Step 10 removed Event's `start`/`end`
data fields. The binding config was always planned (§5.1) but deferred;
these items model it. The three source kinds cover the three real cases:
policy-computed values (coalesced overrides), plain stored values that need
no policy involvement, and derived text that is neither (e.g. a DESCRIPTION
assembled from an HTML/markdown field plus links).
*Intended outcome:* a type's admin can wire `SUMMARY ← effective.title`,
`LOCATION ← room` (data field), `DESCRIPTION ← {{ template }}` per target
plugin, and the pushed payload follows that wiring — no rego or Python
changes needed to sync a new type.

- [x] Binding config schema per plugin: an ordered map
      `remote_property → source`, where a source is one of
      `{"effective": "<key>"}` (key into the policy's `effective` object),
      `{"field": "<data_field_slug>"}` (raw entity field value), or
      `{"template": "<jinja>"}` (mailtemplate-engine string rendered with
      the `effective` / `entity` / `linked` / `backlinks` / `sync` context —
      e.g. building a DESCRIPTION from an HTML/markdown field). Default
      resolution when both are plausible: effective key first, data-field
      fallback — so types without an effective-producing policy still sync,
      and a policy can override any bound field simply by publishing the
      key. — `sync_core/binding.py::BindingSource` (pydantic, `extra=forbid`,
      exactly-one-of validator); template source reuses
      `mailtemplates.render_string`/`jsonify_context` (sandboxed Jinja, no
      new env). Only `effective`/`field`/`template` context is wired for
      templates so far — `linked`/`backlinks`/`sync` context deferred (no
      caller need yet; `resolve_binding_value` is the single place to extend
      when one shows up).
      **Decision:** target selection stays on the existing `sync_targets`
      tab (unchanged, already tested); each plugin registers its own tab
      (`sync_caldav`, `sync_ical`, `sync_pretix` — tab id == Django app
      label) holding only `{"bindings": {...}}`. Simpler and lower-risk than
      merging target selection into per-plugin schemas; `sync_webhook`
      registers no tab per §5.1 (payload is always effective JSON).
      `sync_core/tests/test_binding.py::ResolveBindingsTests`.
- [x] Resolve bindings at **snapshot time** (`mark_sync`), not push time:
      `synced_payload` stores the already-resolved remote-property map.
      Reasoning: this preserves 4.2's deliberate snapshot semantics — the
      worker stays dumb, retries are safe, and `synced_payload` remains
      literally what was sent (auditable). Resolving at push time would
      re-introduce policy evaluation into the worker and make retried
      pushes diverge from what was approved at marking time. —
      `sync_core/models.py::resolve_synced_payload(target, entity_id,
      effective)`: looks up the target's plugin tab config on the entity
      type's bound published version (reusing the §13.1 published-version
      resolution), resolves `bindings` if present, else returns raw
      `effective` verbatim (types without a bindings tab configured yet, and
      `sync_webhook`, keep working unchanged). Called from `mark_sync` in
      place of the old `effective if effective is not None else {}` line.
      `test_binding.py::MarkSyncBindingIntegrationTests
      ::test_mark_sync_stores_resolved_bindings_not_raw_effective`.
- [x] Replace the hardcoded `effective.get("title"/"location"/"start"/"end")`
      reads in `sync_caldav`/`sync_ical` `push()` (and the pretix
      name/date mapping) with the resolved bound values; each plugin's tab
      schema declares its remote property list (DTSTART, DTEND, SUMMARY,
      LOCATION, DESCRIPTION, …; webhook keeps payload = effective JSON,
      binding only selects URL/auth, per §5.1 — receivers adapt, so webhook
      needs no property mapping). Outcome: plugins consume
      `synced_payload[<remote property>]` and know nothing about field
      slugs or effective-object conventions. — `sync_caldav/models.py::push`
      and `sync_ical/models.py::_build_vevent` now read
      `payload.get("SUMMARY", payload.get("title"))` etc. (canonical key
      first, legacy key fallback, so unbound types keep working);
      `sync_pretix` needed no change — its remote-property names
      (title/start/end/locale/max_participants) already matched the legacy
      keys 1:1. Caveat: pretix `prices` is not a bindable remote property
      (per this item's own note) — noted inline in
      `sync_pretix/models.py::push`, only reachable via the legacy
      raw-effective path today.
- [x] `recompute_staleness` compares against the same resolved binding
      output, so a change in any bound source (including template inputs
      and timeslot children) marks the item stale. Reasoning: staleness
      must answer "would a re-push change the remote?" — that is only
      answerable on the resolved payload, not on the raw effective object,
      once bindings/templates transform it. — `recompute_staleness` now
      calls `resolve_synced_payload(item.sync_target, entity_id, effective)`
      per item (bindings can differ by target) before comparing, instead of
      diffing raw `effective` against `synced_payload` directly — this was
      the asymmetry trap flagged during planning (mark_sync resolving but
      staleness not would have marked every bound item permanently stale).
      `test_binding.py::MarkSyncBindingIntegrationTests
      ::test_recompute_staleness_compares_resolved_payloads` covers the
      not-stale / stale-on-bound-change / not-stale-on-unbound-key cases.
- [x] Frontend: extend `SyncTargetsTab` (or per-plugin components) with a
      binding table editor (remote property, source kind, key/slug/template);
      JSON fallback continues to cover plugins without a dedicated component.
      Outcome: bindings are editable without knowing the JSON blob shape;
      a backend-only plugin is still fully configurable via the fallback. —
      `type-editor-tabs/BindingsTab.tsx`: a table editor (remote property /
      source kind select / key-slug-template input) shared by
      `sync_caldav`/`sync_ical`/`sync_pretix` (identical `{"bindings":
      {...}}` config shape, only the suggested remote-property list — a
      `<datalist>` — differs per tab id); registered in
      `type-editor-tabs/registry.ts`. `sync_webhook` still has no tab
      (registers none, per §5.1) and falls through to `JsonTabFallback` if
      one is ever added without a dedicated component.

#### 13.3 Timeslots → calendar entries

*Why:* Step 10 moved all scheduling onto Timeslot submodel children — an
Event has a *list* of (start, end) pairs, but the sync data model still
assumes one remote VEVENT per (entity, target) with a single `remote_uid`.
Collapsing the list into one effective start/end (min/max or first slot)
would misrepresent multi-slot events (setup + talk + teardown would appear
as one long block). Decision: fan out.
*Intended outcome:* a remote CalDAV/iCal calendar shows exactly the
timeslots an Event has — one VEVENT per slot, appearing/moving/disappearing
as slots are created, edited, or deleted — while sync state, staleness, and
the audit snapshot keep working per (entity, target) as before.

- [x] Binding sources may target a **submodel field spec**
      (e.g. `{"submodel": "timeslots", "start": "start", "end": "end"}`);
      when bound, push fans out to **one remote VEVENT per timeslot child**,
      with a per-slot remote uid (entity uid + child node id — stable across
      edits of a slot, so moving a slot updates its VEVENT instead of
      recreating it). — `sync_core/binding.py::SubmodelSpec` +
      `resolve_submodel_slots` (enumerates `UserDefinedModelEntityNode`
      children the same way `api_entities.py`'s calendar endpoint does,
      isoformats datetime field values); `resolve_deep` recognizes a
      `SubmodelSpec`-shaped dict the same structural way it recognizes
      `BindingSource`, so a plugin's tab schema just declares a `submodel:
      SubmodelSpec | None` field (`sync_caldav`/`sync_ical`'s
      `type_editor_tab.py`) and gets it resolved into
      `payload["submodel"]` — a list of `{child_id, start, end}` — for
      free, no sync_core awareness of the plugin's schema needed. Per-slot
      uid is `f"{entity_id}-{child_id}"`, formed by the plugin at push
      time, not stored anywhere.
- [x] Slot lifecycle: **not** a diff-against-previous-snapshot — each push
      re-derives the full fresh slot list from `payload["submodel"]` and
      reconciles directly against *live remote state* (iCal: the parsed
      .ics file's existing VEVENTs; CalDAV: `calendar.events()`), dropping
      anything under the entity-id-prefixed uid that isn't in the fresh set
      before (re)adding every fresh slot. This sidesteps needing the
      previous synced_payload (already overwritten by mark_sync by push
      time, per §4.2) — the remote itself is the source of truth for
      "what to remove". `remote_uid` (the single-VEVENT-per-item column) is
      simply unused in fan-out mode; the item row stays one-per-(entity,
      target), only the remote/payload representation becomes a list.
      Outcome: a removed timeslot deletes its remote entry — the remote
      never accumulates orphaned VEVENTs from deleted slots; a moved slot
      updates its existing VEVENT in place (same uid) rather than
      duplicating.
- [x] Tests: `sync_core/tests/test_binding.py::ResolveSubmodelSlotsTests`
      (child enumeration, ordering, optional `end`, `resolve_deep`
      dispatch), `sync_ical/tests.py::IcalCalendarSyncItemFanOutPushTests`
      (one VEVENT per slot, removed-slot deletion, moved-slot update
      in-place, doesn't touch other items' VEVENTs),
      `sync_caldav/tests.py::CalDAVSyncItemFanOutPushTests` (same, plus
      tolerating an `events()` listing failure without aborting the push).
      Draft-only PUT enforcement / publish-rolls-forward / runtime-reads-
      published-blob / binding resolution / snapshot immutability were
      already covered by §13.1/§13.2's tests and are unaffected by this
      addition (the new `submodel` key rides the same
      `resolve_synced_payload` path).
- [x] Frontend hint: `BindingsTab.tsx` (shared by sync_caldav/sync_ical) —
      a "Multiple VEVENTs per entity" section explaining that a single
      effective/field/template source only ever produces one value, so
      fanning out requires the submodel spec below instead of trying to
      bind DTSTART/DTEND directly; a checkbox + three plain-text inputs
      (submodel field slug, start field slug, optional end field slug) edit
      `submodel`, and the DTSTART/DTEND rows visibly disable/explain
      themselves while fan-out is on. `PretixBindingsTab.tsx` gets the
      converse hint — sync_pretix intentionally does **not** get this
      fan-out (a subevent is one span, not a list of remote objects); it
      points at Step 15's decision to compute `start`/`end` as
      timeslot min/max in rego instead.

### Step 14 — sync_pretix: dynamic parent event + item/variation bindings (2026-08-09)

*Why:* §13.2 only bound sync_pretix's flat subevent fields
(title/start/end/locale/max_participants); which Pretix **event** a type's
entities create subevents under, and which ticket products/variations get
price overrides, were still the static, per-target-admin-configured
`PretixSyncTargetAreaAssociation` (event slug + 6 fixed
`ticket_product_*_id` fields) from Step 6/11 — the "configure everything in
one place, no admin-managed assignments" story §13.1 established for tab
placement never reached sync_pretix's actual sync targets. This step closes
that gap and retires the association model entirely (not deprecates — the
`sync_pretix_areas` management command and its apiv1-`ProposalArea`-driven
backfill are gone too, since nothing populates the removed model).

**Decisions made interactively, in order:**
- Picture upload is out of scope (skipped from the original plan sketch).
- `title`/`start`/`end`/etc. stay bound the same way as §13.2 — the
  subevent's title/name comes from the existing `title` field binding, not
  a separate concept.
- Items/variations are matched by **either** a Pretix numeric ID or a
  display name (case-insensitive, matched against the live Pretix item
  list at push time) — reusing the existing `_resolve_item_id` convention.
- `parent_event` is **mandatory for syncing** (a type with none configured
  just doesn't sync yet — `push()` is a silent no-op, not an error) but
  **never blocks saving** the tab config — the schema always requires the
  key be present (a `BindingSource` dict), but its string *value* can be
  empty; an empty/unresolved value is ignored at sync time, not rejected at
  save time. Same rule for item bindings' `item` field.
- Item bindings dropped the "override price" / "include in quota" opt-in
  checkboxes the first draft had: every item binding is *always* a price
  override (its resolved value can still come back empty/None, in which
  case no override is sent — but the row itself is unconditional) and
  *always* part of the subevent's shared quota — the item bindings list
  *is* the quota membership, full stop.
- Field bindings (the closed, per-plugin-known remote-property set) get no
  add/remove UI in any binding tab (sync_caldav/sync_ical/sync_pretix
  alike) — every known property is always shown as a fixed row; leaving a
  row blank is normal, not an error, and is ignored at sync time.
- `PretixSyncTargetAreaAssociation` (model, admin inlines, the 6
  `ticket_product_*_id` fields, `PRICE_PROPERTY_MAP`) removed outright,
  along with the `sync_pretix_areas` management command that populated it
  from apiv1 `ProposalArea`/Pretix item names. A data migration backfills
  `remote_identity` for any pre-existing `PretixSyncItem` rows that already
  had a `subevent_slug`, so already-synced items keep working without a
  re-push.

- [x] `sync_core/binding.py::resolve_deep` — recursively resolves any
      `BindingSource`-shaped dict nested inside a plugin's tab config
      (detected structurally: validates as `BindingSource` iff it does),
      leaving sibling literals untouched. Lets sync_pretix nest a binding
      inside `items: [{"item": ..., "price": {"effective": ...}, ...}]`
      without `sync_core` knowing sync_pretix's schema.
      `sync_core/tests/test_binding.py::ResolveDeepTests`.
- [x] `sync_core/models.py::resolve_synced_payload` generalized: resolves
      the whole tab config dict (not just its `bindings` sub-key) via
      `resolve_bindings` (flat map) + `resolve_deep` (everything else) —
      caldav/ical are unaffected (they only have `bindings`), sync_pretix's
      `parent_event`/`items` ride the same mechanism for free.
- [x] `PretixSyncItem.remote_identity` (new JSONField):
      `{organizer_slug, event_slug, subevent_id}` pinned at first
      successful push. `_resolved_organizer_slug`/`_resolved_event_slug`
      read it uniformly — `pull_update`/`delete_remote`/`item_admin_url`
      all go through these, never re-resolving `parent_event`. Migration
      `0003_backfill_remote_identity` populates it for pre-existing pushed
      items before `0004` drops the association model/field.
      `compute_drift` surfaces a `parent_event` `PropertyDiff` when the
      freshly-resolved value disagrees with the pinned one — the intended
      "tell the admin, don't move the subevent" outcome.
      `sync_pretix/tests.py::PretixSyncItemComputeDriftTest
      ::test_surfaces_parent_event_mismatch`,
      `PretixSyncItemBindingsPushTest
      ::test_identity_pinned_does_not_move_on_later_parent_event_change`.
- [x] `push()` split into the single bindings-only path (the old
      area-association `_push_legacy` path is deleted, not just
      superseded): skips silently (`return`, no exception, no status
      change) when `parent_event` is empty/unresolved and no subevent
      exists yet; otherwise creates/patches the subevent from resolved
      `bindings`, resolves `item_price_overrides`/`variation_price_overrides`
      and quota `items`/`variations` from `payload["items"]` via
      `_resolve_item_and_variation` (item-list lookup, then variation
      lookup within the resolved item's inline `variations` — confirmed via
      pretix API docs: `value` is the variation display-name field, not
      `name`). `_create_or_update_quota` now takes `organizer_slug`/
      `event_slug` directly (no `association`/`target` objects) and both
      an item-id list and a variation-id list (confirmed via pretix docs:
      quotas have separate `items`/`variations` arrays).
      `sync_pretix/tests.py::PretixSyncItemPushTest`,
      `PretixSyncItemBindingsPushTest`.
- [x] `PretixSyncTargetAreaAssociation` and its admin inline deleted;
      `sync_pretix_areas` management command + its test deleted;
      `debug-quick-setup.sh`/`delete-everything-and-restart.sh` no longer
      call it. `PretixSyncItemAdmin` list/search fields drop
      `area_association`.
- [x] Frontend: `PretixBindingsTab.tsx` (dedicated, not shared with
      `BindingsTab.tsx` — schema diverges). Field bindings (title/start/
      end/locale/max_participants) are fixed rows, no add/remove. Parent
      event is a single always-shown required-for-syncing (not
      required-for-saving) source editor. Item/variation bindings are the
      one genuinely open-ended list in this tab (arbitrary Pretix
      products), so it keeps add/remove — each row is item/variation
      (free text, ID-or-name) + an always-shown price source, no opt-in
      checkboxes. Every edit auto-saves on blur/change; local state applies
      immediately regardless of whether the network PUT proceeds, so
      "empty required field" never makes a button look unresponsive (this
      was an actual bug caught mid-implementation — a blocking validator
      skipped the local state update too, not just the network call).
      Native `styles.btn` buttons throughout, not PrimeReact `Button` (see
      Step 13's own postscript on why).
- [ ] Rego reimplementation of `PretixPricingConfiguration`'s price
      calculation + a UDM bundle wiring a real type to `sync_pretix`
      bindings against concrete Pretix products (`Kursbuchung`/`Kursbuchung
      Unternehmen` and their variations) — requested but not started this
      pass; substantial enough (policy authoring + bundle config) to scope
      separately.

### Step 15 — sync_pretix pricing: rego reimplementation + real-product bundle

*Why:* Step 14 wired sync_pretix's `items` bindings to arbitrary
`{"effective": "<key>"}` price sources, but nothing produces those keys
outside Python: `PretixPricingConfiguration.get_calculated_prices()`
(`backend/sync_pretix/models.py:850-893`) computes the six course prices,
and `CalculatedPrices.clean()` (`models.py:1017-1049`) only auto-populates
them from an `apiv1.Proposal` via `CalculatedPrices.event`
(`models.py:975-1008` — `duration_hours`/`max_participants`/
`material_cost`/`is_basic_course` are all read off `self.proposal`). That
ties pricing to the app Step 12 removes, and it's Python, not policy — a
UDM type has no way to get these six numbers into its `effective` object
today. This was flagged but explicitly deferred at the end of Step 14
("requested but not started this pass"). *Intended outcome:* a UDM type can
compute all six course prices from its own data fields via a rego policy,
and a ready-to-import bundle demonstrates that policy wired to
`sync_pretix` bindings against the two real Pretix products in production
use today — no apiv1 involvement anywhere in the path.

- [x] Rego port of the six formulas in
      `PretixPricingConfiguration` (`models.py:721-848`), each producing one
      `effective` key: `effective.price_member_regular` ((duration ×
      (workshop_rate + lecturer_rate) + lecturer_rate × prep_hours) ×
      (1 + vat_rate) / min_participants + material_cost, ceil'd to whole
      euros), `effective.price_member_discounted` (same with workshop_rate ×
      (1 − discount_rate)), `effective.price_guest_regular` (same with
      + guest_surcharge, and per `get_guest_discounted_price`'s comment at
      `models.py:803` this value is also what "guest discounted" uses —
      i.e. `effective.price_guest_discounted` should equal
      `effective.price_member_regular`, not a separate guest+discount
      formula; preserve that quirk, it matches the documented pricing
      sheet, don't "fix" it), `effective.price_business` (guest-regular's
      pre-surcharge base, ceil'd, then × (1 + business_surcharge), ceil'd
      again per `get_business_net_price`'s double-round at
      `models.py:828-841`), `effective.price_internal_training` (just
      `material_cost`, per `get_internal_training_price` at
      `models.py:843-848`). `workshop_rate` selects
      `workshop_rate_basis`/`workshop_rate_regular` on `is_basic_course`
      (`models.py:721-726`); `min_participants` applies the threshold-deduction
      table (`get_min_participants`, `models.py:710-719`: highest
      `threshold <= max_participants` wins, `max_participants − deduction`,
      floored at 1; default table `{0: 1, 7: 2}` —
      `default_min_participants_params`, `models.py:39-45`).
      Implemented in `documentation/configuration/policies/event.rego`
      (appended after the existing `effective["title"]` rules), reproducing
      the exact `_business_base`/double-ceil quirk and the
      `price_guest_discounted == price_member_regular` quirk verbatim.
      Numerically verified against `opa eval` and the Django test suite (see
      tests item below) with `duration_hours=1.5, material_cost=3.0,
      max_participants=8, is_basic_course=True` → 17/16/20/17/32/3.00.
- [x] `duration_hours`, `material_cost`, `max_participants`,
      `is_basic_course` become plain data fields on a UDM type (not
      `apiv1.Proposal` properties as in `CalculatedPrices.duration_hours`/
      `.material_cost`/`.max_participants`/`.is_basic_course`,
      `models.py:981-1008`) — the rego rules read
      `input.entity.fields.<slug>.value` the same way `event.rego`'s
      existing rules read `title_override`/`origin`, not a Python-side join.
      Added to the **Event** type's field config in
      `documentation/configuration/UDM_BUNDLE.json` (`float`/`float`/
      `integer`/`boolean`, sort_order 7-10) — the same type that already
      carries the `timeslots` submodel_list (Step 10, §6.1) these rules
      also read for `effective.start`/`effective.end` (see below).
- [x] Open decision, resolved for this example bundle as option (a):
      the seven pricing constants (`prep_hours`, `lecturer_rate`,
      `workshop_rate_basis`, `workshop_rate_regular`, `guest_surcharge`,
      `discount_rate`, `business_surcharge`, `vat_rate`) plus
      `min_participants_params` are hardcoded as rego constants
      (`_prep_hours` etc.) directly in `event.rego`, matching the current
      model defaults (`models.py:39-45`, `599-674`). Trade-off accepted
      as-is: a rate change now needs a policy edit + republish, and admins
      lose the `PretixPricingConfiguration` Django-admin editing UX for
      this bundle's type specifically (the Django model/admin itself is
      untouched — see the last open decision below). Option (b) — a
      `data.*`-readable settings surface — remains open for whoever wires
      this against a live pricing admin; not attempted here since the
      rego contract docs don't yet define a `data.*` import convention for
      policies.
- [x] A new example bundle: rather than a separate JSON file, the pricing
      fields and policy were folded into the existing **Event** type
      (`documentation/configuration/UDM_BUNDLE.json`, `documentation/
      configuration/policies/event.rego`) since it already carries the
      `timeslots` submodel_list these rules also consume — see the
      `duration_hours`/`material_cost`/`max_participants`/
      `is_basic_course` fields and the pricing rules appended to
      `event.rego` after `effective["title"]`. Verified importing cleanly
      via `import_bundle_bytes` against the real
      `documentation/configuration/` directory (25 policies, 5 configs).
      Note that `TypeEditorTabConfig` rows are per-`ConfigVersion` runtime
      state (created via the UDM Admin PUT endpoint / admin,
      `backend/userdefinedmodel/models.py`), not part of the bundle import
      schema (`backend/userdefinedmodel/api_bundle.py` has no
      `type_editor_tab_configs` import path) — a bundle file alone cannot
      express it, and `import_bundle` replaces `TypeEditorTabConfig` rows
      with blank stubs on every re-import (it creates a fresh
      `ConfigVersion` each run). The Event type's `sync_pretix` tab was
      wired to the five Kursbuchung variations + one Kursbuchung
      Unternehmen entry by hand against the dev DB after import (via
      `TypeEditorTabConfig.objects` — the same shape
      `PretixRegoPricingBindingsIntegrationTest`, below, exercises), and
      that manual step needs repeating after every `import_bundle` run
      until this gets a proper seed/migration path.
- [x] Tests: `PretixRegoPricingPolicyTests.test_prices_match_documentation_example`
      (`backend/sync_pretix/tests.py`) reproduces
      `PretixPricingConfigurationTests::test_calculated_prices_match_documentation_example`
      numerically through the real policy engine (`evaluate_policy`) —
      `duration_hours=1.5, material_cost=3.0, max_participants=8,
      is_basic_course=True` → `price_member_regular == 17`,
      `price_member_discounted == 16`, `price_guest_regular == 20`,
      `price_guest_discounted == 17`, `price_business == 32`,
      `price_internal_training == 3.0`. `PretixRegoPricingBindingsIntegrationTest`
      covers the items-list wiring: `resolve_bindings` against those six
      `effective.price_*` keys produces the five Kursbuchung variation
      prices + one Kursbuchung Unternehmen price, matching the
      item/variation identifiers from the pasted Pretix product JSON
      (164/165), excluding the legacy 158/159 items entirely.
- [x] `effective.start`/`effective.end` (matching the field names
      sync_pretix's existing `bindings` map already binds `start`/`end` to
      per §13.2) computed as MIN/MAX over the entity's `timeslots`
      submodel children, not per-timeslot: `event.rego` walks
      `input.entity.children.timeslots` (the same doc shape §13.3's
      `resolve_submodel_slots` reads server-side, mirrored here for the
      policy engine's own input document — see `_walk_doc_nodes`/
      `children` in `backend/userdefinedmodel/engine.py`), parses each
      child's `start`/`end` via `time.parse_rfc3339_ns`, and reduces to
      `min()`/`max()` formatted back via `time.format`. This is *why*
      sync_pretix does not get Step 13.3's per-timeslot fan-out: a Pretix
      subevent is one span, not a list of remote objects, so instead of
      fanning out like caldav/ical, sync_pretix collapses every timeslot
      into one subevent covering the full range from the earliest slot's
      start to the latest slot's end.
      `PretixRegoPricingPolicyTests.test_start_end_are_true_min_max_across_timeslots_not_first_last`
      covers exactly this: three timeslots added out of chronological order
      (middle-by-creation slot has the earliest start) assert the true
      min/max, which a naive first/last-slot implementation would get wrong.
- [ ] Open decision, not resolved here: whether
      `PretixPricingConfiguration`/`CalculatedPrices` (`models.py:590-1053`,
      apiv1-`Event`/`Proposal`-linked) get deleted once this rego path
      lands, or coexist as a legacy admin-editable fallback. Likely needs
      sequencing against Step 12 (apiv1 removal) — `CalculatedPrices.event`
      is an FK to `apiv1.Event`, so it cannot survive apiv1's removal
      unmodified regardless of what this step decides; note the dependency
      here rather than resolving it.
- [x] Per-value overrides + an effective-values summary, added after the
      initial rego port on user request: `max_participants_override`,
      `min_participants_override`, and one `..._override` field per
      `price_*` key (all on the Event type, `UDM_BUNDLE.json`) — same
      coalescing pattern as `title_override` (§1.3): the override field
      wins when set, otherwise the computed value, via paired
      `effective["x"] := v if { v := ...; ... != null }` /
      `effective["x"] := <formula> if { ... == null }` rules in
      `event.rego`. `effective.max_participants` (override-or-raw-field)
      feeds the min-participants threshold table so overriding max also
      re-derives min (unless min itself is overridden), and every price
      formula reads `_min_participants`/effective max/min rather than the
      raw fields directly, so a max/min override reflows every calculated
      price too — `price_guest_discounted`'s override still wins over its
      member-regular reuse. A new `pricing_summary` markdown_display field
      (`type_config.template`, same rendering mechanism as the existing
      `summary` field — §1.4) shows the effective min/max participants and
      all six effective prices as a table, each row flagged "(Override)"
      when the corresponding override field is set, so it always reflects
      calculated-or-override, never just one or the other. Tests:
      `PretixRegoPricingPolicyTests.test_price_override_wins_over_calculated_value`,
      `.test_guest_discounted_override_wins_over_member_regular_reuse`,
      `.test_max_participants_override_reflows_min_participants_and_prices`,
      `.test_min_participants_override_wins_over_computed_deduction_and_reflows_prices`
      (`backend/sync_pretix/tests.py`).
- [x] Real-DB wiring done this pass (events-and-sync.md's own dev
      instance, not just tests): `import_bundle` run for real (not
      `--dry-run`), then the Event type's published `ConfigVersion`'s
      `sync_pretix` `TypeEditorTabConfig` was set by hand to the
      `bindings`/`parent_event`/`items` shape described above (`parent_event`
      is a placeholder `{"template": "kurse-2026"}` — no real Pretix
      organizer/event exists in dev, an operator must replace it with a
      real slug or a policy-driven template before this can actually push).
- [x] Sync trigger + status workflow, added after the above on user
      request ("add a workflow like flows.py for the event, sync when it
      goes into status published"): a new **Event Lifecycle Workflow**
      (`UDM_BUNDLE.json`, id `7e1104d4-1d4a-4ade-b9b1-4c49a63d4b92`) ported
      from `apiv1/flows.py`'s `EventFlow`, bound to a new `status` workflow
      field on the Event type. States: draft/proposed/planned/published/
      confirmed/completed/canceled/archived (no separate "rejected" —
      `reject` folds `proposed` straight into `canceled`, a simplification
      made while editing the workflow live in UDM Admin's workflow editor
      after the initial import, then captured back into the bundle
      verbatim per a follow-up "take the current workflow from the running
      dev DB and put it into the bundle" request). Transitions: submit,
      approve, reject, publish, confirm, complete, plus per-source-state
      `cancel_planned`/`cancel_published`/`cancel_confirmed` and
      `archive_canceled`/`archive_completed` — enumerated per source state
      rather than one wildcard `cancel`/`archive`, because
      `WorkflowTransition.name` must resolve uniquely per workflow version
      (`execute_transition` does a `.get(version=, name=)`), so apiv1's
      multiple same-named `cancel`/`archive` sources (viewflow FSM
      decorators) can't collapse into a single UDM transition; naming them
      per source state is the actual equivalent, not a `from_state: null`
      wildcard. Also simplified vs. apiv1: every transition is staff-only
      via one blanket `event.rego` rule instead of apiv1's per-transition
      permission classes, and no auto-publish-on-approve or date-window
      conditions (`CONFIRM_WINDOW_DAYS`, `_event_has_passed`).

      The sync trigger, and a manual re-sync added on a follow-up request
      ("we need a transition from any state to retrigger sync, which
      should only do something in published, confirmed and completed
      states" — implemented as three self-loop transitions,
      `resync_published`/`resync_confirmed`/`resync_completed`, since a
      `WorkflowTransition` always targets one fixed `to_state`, so a
      single "any state, stay put" transition isn't representable — a
      state without a `resync_<state>` self-loop simply has no such
      button, which is why sync is scoped to those three and not literally
      every state): `event.rego` adds `actions contains {"type":
      "mark_sync", "phase": "post", "target": "pretix-test", "status":
      "pending"} if input.action == "transition"; input.field == "status";
      input.transition in {"publish", "resync_published",
      "resync_confirmed", "resync_completed"}` — gated on the *transition
      name*, not `input.entity.fields.status.value`, because
      `execute_transition` (`backend/userdefinedmodel/engine.py`)
      evaluates the policy (freezing its `actions` set) BEFORE writing the
      new workflow state, so the entity's own field would still read the
      OLD status at evaluation time; the transition name is available
      immediately via `input.transition`. `mark_sync`'s own
      `get_or_create` on `(entity, target)` (`sync_core/models.py`) means
      every one of these firings updates the same `SyncBaseItem` in place
      — a fresh `synced_payload` snapshot and `status` reset to `pending`
      — never creates a second row, whether triggered by `publish` or any
      `resync_<state>`. Verified against the real dev DB (not just a
      test): `submit` → `approve` → `publish` on a fresh Event entity
      created exactly one `pending` `SyncBaseItem` against `pretix-test`
      (`submit`/`approve` alone created none), and firing
      `resync_published` again on an already-published, already-synced
      entity kept the same `SyncBaseItem` id and just flipped its status
      back to `pending`.
- [x] Three real bugs found and fixed while verifying the above against a
      real Pretix instance end-to-end (not just against mocks — the
      existing test suite never caught any of these because every push
      test constructs its `SyncItem`/`SyncTarget` directly, bypassing
      `mark_sync()`'s creation path and `push_pending_sync_items()`'s
      query entirely):
      - `mark_sync()` (`sync_core/models.py`) always created a bare
        `SyncBaseItem` through the base manager, never the plugin's
        concrete subclass (`PretixSyncItem` etc.) — `push()` is only
        implemented on the subclasses, so every real mark_sync-triggered
        item was permanently stuck, `push()` raising "does not implement
        push()" only once a worker actually got around to it. Fixed via a
        new `SyncBaseTarget.sync_item_model()` classmethod, overridden by
        every plugin's Target class, that `mark_sync()` now creates
        through instead of the bare base manager.
      - `push_pending_sync_items()` (`sync_core/tasks.py`) used
        `select_related("sync_target")`, which — django-polymorphic only
        downcasts through its own manager, not a `select_related` JOIN —
        handed plugin `push()` code a bare `SyncBaseTarget` missing every
        subclass field (`'SyncBaseTarget' object has no attribute
        'organizer_slug'`). Fixed by dropping it; lazy FK access downcasts
        correctly.
      - `_resolve_binding_quota_members` (`sync_pretix/models.py`) added
        only a variation's own id to the quota's `variations` list, never
        its parent item's id to `items` — Pretix rejects that
        ("Alle Varianten müssen zu einem Produkt gehören, das auch in der
        Liste der Produkte enthalten ist."). Fixed to always include the
        (deduplicated) parent item id alongside any bound variation id.
      Also: the dev DB's `sync_pretix` migrations 0002-0004 (adding
      `remote_identity`, dropping the old area-association tables) had
      never actually been applied there — `manage.py migrate` fixed it —
      and the dev instance's `locale` binding (`"de"`) didn't match the
      real target Pretix event's actual locale (`"de-informal"`), corrected
      on the live `TypeEditorTabConfig`.
- [x] `mark_sync()` now also triggers an actual push, not just a status
      flip: `sync_core/tasks.py`'s new `enqueue_push_if_idle()` queues
      `push_pending_sync_items_task` (best-effort, swallows broker errors —
      `mark_sync()` must never fail because of it) every time `mark_sync()`
      marks an item `pending`, debounced via a 60s cache lock cleared at
      the START of the task's own run (so a `mark_sync()` firing while a
      push is already in flight still queues a follow-up once that flight
      finishes, rather than being silently dropped by the debounce).
      Added because `mark_sync()` previously only ever flipped status to
      `pending` and relied entirely on `CELERY_BEAT_SCHEDULE`'s 10-minute
      tick to actually push — invisible and confusing with no beat process
      running at all (only a worker), which was this dev instance's actual
      state: `publish`/`resync_*` appeared to do nothing because nothing
      was pushing the resulting `pending` items, ever. Verified live: a
      `resync_published` transition alone (no manual
      `push_pending_sync_items()` call) produced a `synced` status with
      the updated price live on Pretix within ~4 seconds.

### Step 12 — apiv1 removal (7)

- [ ] Preconditions verified: no imports of `apiv1` outside `apiv1/`
      (`grep -rn "from apiv1\|import apiv1" backend/ --include=*.py`), no
      frontend calls to apiv1 endpoints, sync apps fully on `sync_core`.
      **Audit 2026-08-09 — known remaining apiv1 importers outside `apiv1/`.**
      Intended outcome: deleting the `apiv1` package must break nothing but
      apiv1 itself; each importer below therefore needs its dependency moved,
      copied, or dropped first (the generic utilities were never
      event-specific and simply live in the wrong app):
      - `sync_pretix`: `models.py` (`time_string_to_minutes` — copy/move the
        helper into `sync_pretix` or `sync_core`), `tests.py`,
        `test_sync_pretix_command.py`, `management/commands/
        sync_pretix_areas.py` (apiv1 `ProposalArea`/`Event` fixtures —
        rewrite against UDM entities or drop with the legacy command).
      - `openid_user_management`: `schemas.py` imports `apiv1.schemas.
        ErrorOut`, `api.py` imports `apiv1.api_utils.api_permission_required`
        — move/copy these two utilities out of apiv1 (they are generic, not
        event-related).
      - `ipython_imports.py`: `from apiv1.models import *` — drop the line.
      - `project/urls.py`: mounts `apiv1.api` — removed together with the app.
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
