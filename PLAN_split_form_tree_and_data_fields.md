# Plan: Separate Form Tree Definition from Database Field Definition

> Status: **DRAFT — awaiting decisions on the checklists in §0.**
> Author: pi coding agent
> Scope: `backend/userdefinedmodel/` + `src/` + `documentation/`

---

## 0. Decision Checklists (tick these first)

Suggested answers are pre-marked with **`(suggested: ✅)`**. Edit freely —
change ✅/❌ or write your own answer under each item. These steer the
implementation in §1–§8.

### Checklist A — Data model shape

- [x] **A1. Rename `FieldDefinition` → `DataField`** (strip form-tree columns).
      *(suggested: ✅ — keeps the table & PKs, so `FieldValue.field` etc. stay valid)*
- [x] **A2. Add `FormElement` model** holding `parent`, `sort_order`, `is_preview`,
      `element_type`, `slug`, `type_config` (widget config), `translations`.
      *(suggested: ✅)*
- [x] **A3. Add `FormElementBinding` (M:N)**: `form_element` ↔ `data_field` + `role`.
      *(suggested: ✅ — this is what enables hidden fields & multi-field widgets)*
- [x] **A4. Add `FormElementTranslation`** (label, help_text, per language).
      *(suggested: ✅ if labels live on the element — see B1)*
- [x] **A5. Move `STRUCTURAL_TYPES` from `FieldDefinition` to `FormElement.element_type`**;
      remove structural choices from `DataField.DataType`.
      *(suggested: ✅)*
- [x] **A6. Replace `parent_slug` (string) with a real `parent` FK** on `FormElement`.
      *(suggested: ✅ — cleaner tree; migration derives the FK from the old string)*

### Checklist B — Labels & help_text (the one real fork)

- [x] **B1. Labels live on `FormElement`** (a hidden field has no label; one field
      shown in two places can have two labels). Needs `FormElementTranslation`.
      *(suggested: ✅ — aligns with the stated goal)*
- [ ] **B2. Labels live on `DataField`** (canonical name; form elements inherit).
      Simpler, but a hidden field carries an unused label and one field can't have
      two labels.
      *(suggested: ❌)*
- [ ] **B3. Canonical on `DataField` + optional override on `FormElement`.**
      Most flexible, two translation tables + merge logic.
      *(suggested: ❌)*

### Checklist C — Rego policy contract (high risk)

- [x] **C1. Keep `input_version=1`, shape-compatible.** Emit structural elements
      into `entity.fields` with `element_type` as `data_type`, exactly as today;
      `input.schemas` built from `DataField` only. No Rego rewrite.
      *(suggested: ✅ — smallest blast radius)*
- [ ] **C2. Bump to `input_version=2`**, drop structural elements from the input
      entirely. Requires rewriting `structural.rego`, `view.rego`, `config.rego`,
      `_input_schema.rego`, `policy_input.py`, `check_input_schema.py`, all examples.
      *(suggested: ❌ — defer to a later change)*

### Checklist D — Migration mechanics

- [x] **D1. Rename + add:** `RenameModel FieldDefinition→DataField`, drop the 3
      form columns, create new tables, RunPython to move form rows → `FormElement`.
      FKs to the field table stay valid (same table/PKs).
      *(suggested: ✅ — lowest risk)*
- [ ] **D2. New `DataField` table + copy.** Cleaner but every FK
      (`FieldValue`, rules, `FieldEdit`, `StagingFile`, `SubmodelInstance.parent_field`)
      must be re-pointed in the same migration.
      *(suggested: ❌)*

### Checklist E — Scope

- [x] **E1. Full split in one change set** (models + migration + API + frontend + tests + docs).
      *(suggested: ✅)*
- [ ] **E2. Additive layer first:** add `FormElement` 1:1 with `FieldDefinition`,
      move only `parent_slug`/`sort_order`/`is_preview`; defer multi-binding & hidden
      fields to phase 2. Two migrations, a transitional model.
      *(suggested: ❌)*

### Checklist F — Multi-field widget (proves the M:N binding)

- [x] **F1. Implement a `date_range` form element** bound to two `date` data fields
      (`role="from"` / `role="to"`) as the first concrete multi-binding widget.
      *(suggested: ✅ — validates the design end-to-end)*
- [ ] **F2. Only stub the binding model + API; build the widget later.**
      *(suggested: ❌)*

---

## 1. Problem Statement

A single Django model, **`FieldDefinition`** (`backend/userdefinedmodel/models/config.py`),
conflates two orthogonal concerns:

1. **Database/storage semantics** — `data_type`, `is_localized`, `type_config`,
   `submodel_config`, `workflow_version`, `defaults`, validation rules. *What a
   field is and how its value is stored.*
2. **Form tree structure** — `parent_slug`, `sort_order`, `is_preview`, and the
   `STRUCTURAL_TYPES` (`tab_container`, `tab`, `hstack`, `hstack_group`,
   `save_button`, `tab_prev`, `tab_next`). *Where and how a field is rendered.*

Because they're one table, you cannot:
- Have a **hidden** data field (exists in schema, holds values, never shown/edited).
- Have **one form element bind to multiple data fields** (e.g. a from-to date
  range picker reading/writing two `date` columns).
- Have **multiple form elements bind to the same data field** (e.g. a preview chip
  and a full editor for the same value, possibly with different labels).
- Reuse the same data field under different labels in different tabs.

Structural types (`tab`, `hstack`, …) are pure layout — they carry no value and
currently abuse `FieldDefinition` rows just to get a slug into the tree.

## 2. Target Data Model

### 2a. `DataField` (storage semantics only) — renamed `FieldDefinition`
- `version` → `ConfigVersion` (FK)
- `slug` (unique per version — unchanged)
- `data_type` — **only value-bearing types** (`text_short` … `workflow`,
  `submodel_*`, `entity_*`, `slug_id`). Structural types removed → moved to
  `FormElement.element_type`.
- `is_localized`, `type_config`, `submodel_config`, `workflow_version` — unchanged.
- `defaults` (`FieldDefaultValue`), validation rules — unchanged, FKs point here.
- `FieldValue.field` — unchanged, points here.
- **Removed:** `parent_slug`, `sort_order`, `is_preview`, `STRUCTURAL_TYPES`.
- **No** label/help_text (if B1 chosen) — or canonical label (if B2 chosen).

### 2b. `FormElement` (form tree + widget) — new
- `version` → `ConfigVersion` (FK, `related_name="form_elements"`)
- `slug` (unique per version; auto-gen for structural, user-chosen for bound)
- `element_type` — new `TextChoices`: the current `STRUCTURAL_TYPES` **plus** a
  generic `field` type (and `date_range`, etc. for multi-field widgets).
- `parent` → `self` (FK, nullable, `related_name="children"`) — replaces `parent_slug`.
- `sort_order`, `is_preview` — moved from `FieldDefinition`.
- `type_config` — **widget** config (e.g. `date_range` needs `from`/`to`; distinct
  from `DataField.type_config` which is **storage** config).
- `translations` → `FormElementTranslation(language, label, help_text)` (if B1).

### 2c. `FormElementBinding` (M:N link) — new
- `form_element` → `FormElement` (FK, CASCADE)
- `data_field` → `DataField` (FK, PROTECT)
- `role` — optional string (`"from"`, `"to"`, `""` for single-binding)
- `Meta.unique_constraints = [("form_element", "data_field", "role")]`

This delivers:
- **Hidden field** = `DataField` with zero bindings.
- **One element → many fields** = multiple bindings (e.g. `date_range`).
- **Many elements → one field** = multiple bindings for one `DataField`.

## 3. Labels decision — see Checklist B

Recommended: **B1 (labels on `FormElement`)**. Hidden fields need no label; a field
shown in two places can have two labels. The `public_type_fields` Rego action
(returns field descriptions) resolves a label via any bound element (fallback to
slug). Trade-off table:

| | A: labels on FormElement | B: labels on DataField |
|---|---|---|
| Hidden field | No orphan label ✓ | Carries unused label |
| Same field, two labels | Native ✓ | Impossible w/o 2nd table |
| `public_type_fields` | Resolve via binding | Trivial |
| Migration | 1:1 copy translations→element | 1:1 copy→field |

## 4. Policy Contract Impact (highest risk)

The Rego input is a **versioned, checked contract** (`input_version=1`),
validated by `policy_input.py` + `check_input_schema.py`, executable contract in
`_input_schema.rego`.

**Recommended (C1, shape-compatible):** the split is *invisible* to policies:
- `schema_document_for_version()` (`models/node.py`) rebuilt from `DataField`
  only — structural elements already aren't in `input.schemas` as value entries.
- `to_policy_document()` still emits structural `FormElement`s into
  `entity.fields` with `element_type` as `data_type`, so `structural.rego`,
  `config.STRUCTURAL_TYPES`, `view.rego` keep working unchanged.
- `viewable_fields`/`editable_fields` slug-keyed grants stay key-compatible:
  structural element slug (clickability) + data field slug (value). Both slugs
  are preserved by the migration.
- **No Rego rewrite, no checker rewrite, no input_version bump.**

Subtlety to verify during implementation: after the split, a data field with two
bound form elements has one slug (the data field's) for *value* grants, while each
form element has its own slug for *clickability* grants. Confirm no Rego module
assumes 1:1 slug↔element. `structural.rego` keys on `entry.data_type in
STRUCTURAL_TYPES` and iterates `node.fields` — still works if we keep emitting
structural elements into `node.fields`.

## 5. Blast Radius

**Backend models**
- `models/config.py` — rename `FieldDefinition`→`DataField`; strip form columns;
  add `FormElement`, `FormElementTranslation`, `FormElementBinding`.
- `models/__init__.py` — export new names.
- `models/node.py` — `schema_document_for_version`,
  `collect_version_schema_documents`, `to_policy_document`,
  `materialize_defaults`, `materialize_user_defaults`,
  `StagingFile.intended_field` FK (rename). `FieldValue.field` → `DataField`.
- `models/rules.py` — rule FKs → `DataField`.
- `models/migration.py` — `MigrationFieldMapping`/`BulkMigrationFieldMapping`
  use slug strings (likely unchanged); submodel mappings need review.
- `models/history.py` — `FieldEdit.field` → `DataField`.
- `models/config.py` `ConfigVersion._create_draft_copy()` — copy `DataField`s
  **and** `FormElement`+bindings+translations.

**Backend API**
- `schemas.py` — split `FieldDefinitionIn/Out/DraftOut` into
  `DataFieldIn/Out` + `FormElementIn/Out`; `ConfigDraftIn.fields` →
  `data_fields` + `form_elements` (or a combined tree). `STRUCTURAL_DATA_TYPES`
  → element types.
- `api_configs.py` `replace_draft` — rebuild both tables; SLUG_ID prefix
  uniqueness check moves to the data-field loop.
- `api_helpers.py` `_serialize_config_version`, `_serialize_version_as_draft_in`
  — emit both.
- `api_bundle.py` — bundle export/import of both.
- `api_entities.py`, `api_types.py`, `writer.py` — `apply_patch` resolves slugs
  against `DataField`; structural elements skipped (today via
  `STRUCTURAL_TYPES` check → now `FormElement.element_type`).
- `engine.py`, `policy_input.py` — **no change** under C1.
- `admin.py`, `actions.py`, `tasks.py`, `api_autocomplete.py`, `api_staging.py`
  — FK-target renames + tree traversal via `FormElement`.

**Migrations**
- New `0027_split_form_tree_and_data_fields.py`:
  - create `FormElement` / `FormElementTranslation` / `FormElementBinding`;
  - for each existing `FieldDefinition`:
    - `data_type in STRUCTURAL_TYPES` → move to `FormElement`
      (`element_type=data_type`, `parent` from `parent_slug`,
      `sort_order`/`is_preview`/translations carried over), delete the row;
    - else → rename to `DataField` row (drop the 3 form columns), create a 1:1
      `FormElement` of type `field` bound to it, carry
      `parent_slug`/`sort_order`/`is_preview`/translations onto the element.
  - Re-point `FieldValue.field`, rule FKs, `FieldEdit.field`,
    `StagingFile.intended_field`, `SubmodelInstance.parent_field` to the new
    `DataField` rows (FK renames if same table — D1; data moves if new table — D2).
- Per the project's migration-history pattern, do this as a **new** migration,
  not by editing old ones.

**Frontend**
- `schema_udm.d.ts` / `schema.d.ts` — regenerate via `bash buildnodeclient.sh`
  (per AGENTS.md) after the API schema changes.
- `src/UdmAdminPage.tsx` — field tree editor (`fieldsToNodes`/`nodesToFields`,
  `STRUCTURAL_TYPES`, `PARENT_TYPES`) becomes a **form element** tree editor;
  data fields managed in a separate list/panel and bound via element `bindings`.
- `src/UdmEntityEditor.tsx` — render the tree from `form_elements`; resolve each
  bound element's data field(s) for value read/write. `STRUCTURAL` set → element
  types.
- `src/udm-editors/*` — value editors already key off `data_type`; stay bound to
  `DataField`. A new `DateRangeEditor` consumes a multi-binding element.
- `src/apiUdm.ts` — update typed API calls for the new draft shape.

**Tests**
- `tests/factories.py` — `FieldDefinitionFactory` → `DataFieldFactory` +
  `FormElementFactory`; `make_simple_config`, `make_workflow_field` updated.
- `tests/test_api.py`, `tests/test_policy_actions.py` — update payloads/assertions.
- `project/tests/` Playwright tests (per AGENTS.md) — update admin field-editor flows.

**Docs / Rego**
- `documentation/configuration/policies/structural.rego`, `config.rego`,
  `view.rego` — **no change** under C1 (verify, don't assume).
- `documentation/POLICY_ENGINE.md`, `API_REVIEW.md` — update model description.
- `documentation/configuration/UDM_BUNDLE.json` — regenerate.

## 6. Migration Strategy — see Checklist D

Recommended **D1 (rename + add):** `RenameModel FieldDefinition→DataField`, drop the
3 form columns, create new tables, RunPython to populate `FormElement` from the old
rows' form columns + translations. `FieldValue.field` etc. keep their FK (same
table/PKs). Smallest diff, no FK re-pointing.

Alternative D2 (new table + copy) is cleaner but re-points every FK in one
migration — higher risk.

## 7. Suggested Implementation Order

1. **Models + migration** — introduce `FormElement`/`FormElementTranslation`/
   `FormElementBinding`, rename `FieldDefinition`→`DataField`, strip form columns,
   write the data migration. `makemigrations` + `migrate` on dev DB.
2. **Backend internals** — `models/node.py` schema/policy-doc builders,
   `ConfigVersion._create_draft_copy`, rules, history, staging, admin.
3. **API layer** — split schemas, `replace_draft`, serializers, bundle
   import/export. Regenerate `schema_udm.d.ts` via `buildnodeclient.sh`.
4. **Policy contract verification** — run `check_input_schema.py` + the policy
   test suite; confirm the shape-compatible path holds; fix any emission mismatch.
5. **Frontend** — admin field-tree editor → form-element editor + data-field list;
   entity editor renders the element tree; add the multi-binding widget path
   (at least a `date_range` stub to prove M:N).
6. **Tests** — update factories + unit/integration/Playwright; add tests for:
   hidden field, one element→two fields, two elements→one field.
7. **Docs** — update `POLICY_ENGINE.md`, `API_REVIEW.md`; regenerate
   `UDM_BUNDLE.json`.

## 8. Risks & Verification

| Risk | Mitigation |
|---|---|
| Rego contract silently breaks | Run `check_input_schema.py` + full policy suite after step 4; keep `input_version=1`. |
| `viewable_fields`/`editable_fields` slug assumptions | Grep Rego for slug↔element 1:1 assumptions; add a test with a 2-element/1-field config. |
| Migration data loss on structural rows | RunPython is reversible; add a `reverse` that rebuilds `FieldDefinition` rows. Test on a copy of `db.sqlite3`. |
| Bundle import/export round-trip | Add a round-trip test exporting+re-importing a config with structural + bound + multi-binding elements. |
| `public_type_fields` label resolution | Under B1, document the fallback order (bound element label → slug); add a test for a hidden field (no element → slug only). |
| Frontend tree editor rewrite size | Keep the existing drag-and-drop tree component; only swap the node payload from `FieldDefinition` to `FormElement`. |

---

*End of plan. Tick §0 then say "implement" (or paste your edited checklist) to proceed.*

---

## 9. Implementation Log (TODO #1–#14)

All 14 tracked tasks are complete. This section records what was actually
changed, where, and the key decisions that emerged during implementation.
Verification at the end of each item: backend tests (143 in `userdefinedmodel`),
frontend `tsc --noEmit`, and `VITE_DJANGO_BASE=true npm run build` all pass.

### #1 — Strip form-tree columns from the Data Field editor
- **Files:** `src/UdmAdminPage.tsx`
- Removed `label` / `help_text` / `is_preview` / `sort_order` inputs from the
  Data Field editor (these now live on the FormElement, not the DataField).
  Data Field editor keeps only storage semantics: `data_type`, `is_localized`,
  `type_config`, defaults, validation.

### #2 — Preview Config sub-tab stored like a form
- **Files:** `src/UdmAdminPage.tsx`
- `ConfigDraftEditor` gained a third sub-tab `preview`. The form element tree is
  split by `is_preview`: the Preview Config tab shows only `is_preview=true`
  elements; the Form Config tab shows the rest. `FormConfigEditor` takes an
  `isPreview` prop. The inline `FormElementEditor` no longer shows an "Is Preview"
  checkbox (set via the tab instead).

### #3 — Default submodel_config_version_id to latest published
- **Files:** `src/UdmAdminPage.tsx` (`SubmodelVersionPicker`),
  `backend/userdefinedmodel/api_configs.py` (`replace_draft`),
  `backend/userdefinedmodel/schemas.py` (validator),
  `backend/userdefinedmodel/api_bundle.py` (import).
- **Decision — split constraint:** drafts may be saved with orphaned submodel
  fields (no config); publishing is blocked until every submodel field has a
  config (`_validate_submodels_for_publish`). `SubmodelVersionPicker` auto-selects
  the latest published version. `replace_draft` accepts a FieldConfig id and
  resolves it to the latest published ConfigVersion. Bundle import resolves
  pending submodel refs leaf-first (topo order) before the parent publishes.

### #4 — Make sidebar→tree drag-drop reliable
- **Files:** `src/UdmAdminPage.tsx`, `src/UdmAdminPage.module.css`
- **Decision — remove PrimeReact dragdropScope:** eliminated the competing
  PrimeReact `dragdropScope` / `onDragDrop` handlers; the custom HTML5 drop lines
  are now the only drop path. Drop lines are taller (1.1rem) and always visible.
  The outer `onDrop` inserts at the last-hovered position as a fallback.

### #5 — Edit the languages available in a schema
- **Files:** `backend/userdefinedmodel/schemas.py` (`FieldConfigUpdateIn`),
  `backend/userdefinedmodel/api_configs.py` (`update_config`),
  `backend/userdefinedmodel/tests/test_api.py` (4 new tests),
  `src/UdmAdminPage.tsx` (`ConfigDetail` language editor),
  `src/UdmAdminPage.module.css` (`.langEditor`/`.langRow`/`.langDefault`),
  `src/schema_udm.d.ts` (regenerated).
- `FieldConfigUpdateIn` now accepts an optional `languages: list[ConfigLanguageIn]`
  with the same validator as create (exactly one default, no duplicate codes).
  `PATCH /configs/{id}/` handles language updates via **soft replace**: deletes
  existing `ConfigLanguage` rows and recreates them from the payload. Removing a
  language only deletes the `ConfigLanguage` row — existing
  translations/field-values for that code remain in the DB (orphaned but
  harmless; re-adding the same code re-enables them). Frontend language editor
  lives inside the Config Info edit view: per-language code/label inputs, a
  radio group for the default (enforces exactly one), ↑/↓ reorder, ✕ remove
  (disabled at one language; auto-promotes a remaining language to default),
  and "+ Add language".

### #6 — Warning badge for missing labels / help translations
- **Files:** `src/UdmAdminPage.tsx`
- A ⚠ badge appears on tree nodes when a form element lacks labels or has
  incomplete help-text translations. This is a warning, not a block (see #10).

### #7 — Preset / validate binding roles by form field type
- **Files:** `src/UdmAdminPage.tsx` (`BINDING_ROLES`),
  `backend/userdefinedmodel/schemas.py` (`FormElementIn.validate_element`,
  `_BINDING_ROLES`).
- **Decision — preset roles, no freetext:** `field` → role `""`;
  `date_range` → roles `from`/`to`. The inline editor renders fixed role labels
  and a data-field dropdown per role (no freetext role input). Backend
  `validate_element` enforces the exact role list per element type.

### #8 — Fix stuck bulk migration
- **Files:** `backend/userdefinedmodel/models/migration.py` (`error_message`),
  `backend/userdefinedmodel/migrations/0028_*`, `backend/userdefinedmodel/tasks.py`
  (`run_bulk_migration`), `backend/userdefinedmodel/schemas.py`
  (`BulkMigrationOut`), `src/UdmMigration.tsx`.
- **Root cause:** a stale Celery worker (predating migration 0027) had the old
  `userdefinedmodel_fielddefinition` table name cached in memory while the DB
  had `userdefinedmodel_datafield` (migration 0027). Added an `error_message`
  field to `BulkMigrationPlan` (migration 0028); the task now captures
  `executed_at` / `error_message` on failure and resets progress counters on
  re-run. Exposed in the API and the frontend migration UI.
- **Note:** the stale Celery worker must be restarted by the user (the agent does
  not kill processes).

### #9 — Localized help text
- **Files:** `src/UdmAdminPage.tsx` (`FormElementEditor`)
- Added per-language Help Text inputs alongside the per-language Labels in the
  inline `FormElementEditor`.

### #10 — Allow saving / publishing without labels
- **Files:** `backend/userdefinedmodel/schemas.py` (`validate_element`).
- **Decision — labels optional:** removed the hard `labels is required for
  'field' elements` validation. A field config may be saved and published
  without labels; the missing-label condition is surfaced as a warning badge
  (#6), not a hard block.

### #11 — Fix label input focus loss (HIGH PRIORITY)
- **Files:** `src/UdmAdminPage.tsx` (`FormConfigEditor`).
- **Root cause:** `FormConfigEditor` rebuilt the tree with fresh `genElKey()`
  keys on every prop change, causing the inline editor to unmount/remount and
  lose input focus on each keystroke. **Fix:** stable slug-based keys + a
  structural-signature guard (`structSig`) on the rebuild `useEffect` — the tree
  is rebuilt only when structure (slug/parent/type/order) changes; pure field
  value edits (labels/help/bindings) no longer trigger a rebuild. Browser-verified:
  focus retained across keystrokes in the date_range label field.

### #12 — Date-range field in the entity editor
- **Files:** `backend/userdefinedmodel/api_helpers.py` (compat merge),
  `src/UdmEntityEditor.tsx` (`FieldRow` date_range branch, `renderFieldRow`),
  `src/udm-editors/DateRangeEditor.tsx` (rewritten),
  `src/udm-editors/FieldCommitWrapper.tsx` (reused),
  `src/udm-editors/DateTimeEditors.tsx` (reference).
- **Backend:** the legacy `fields` compat merge folds the binding→data-field slugs
  into `type_config.bindings` for `date_range` elements, so the entity editor
  (which iterates `fields`) can resolve the bound fields.
- **Frontend visibility:** a multi-field widget shows when **all** bound fields
  are editable (per user correction — not viewable, not "any").
- **Widget:** a single PrimeReact `Calendar` with `selectionMode="range"` (one
  control, two dates). The in-progress selection is held in **local state** so
  the two-click range flow completes without parent re-renders resetting
  PrimeReact's second-click state. The parent is notified only when the range
  is complete (both dates set) or fully cleared — never mid-selection — so
  picking a new `from` doesn't wipe the existing `to`. **Dates are flipped** if
  the user picks the end before the start, so `from ≤ to`.
- **Autosave:** the `date_range` branch wraps `DateRangeEditor` in
  `FieldCommitWrapper` (same commit/cancel buttons as other fields, with
  blur-commit). Editability and saving aggregate over both bound fields
  (`commitBoth` saves `from` then `to`; `resetBoth` clears both).
- Browser-verified: single range calendar renders both bound values; two-click
  range completes; flip on reverse order; autosave buttons appear.

### #13 — Sync multiple form fields bound to the same data field
- **Files:** none — verified the sync already works through shared state.
- **Finding:** no code change was needed. The compat merge gives `field` elements
  `slug = data_field.slug`, so both the `date_range` widget (writing
  `dirty[fromSlug]`/`dirty[toSlug]`) and a separate `field` element bound to the
  same data field (writing `dirty[fd.slug]`) share the **same dirty keys**.
  Editing either one live-updates the other (visual sync, the chosen scope).
- **Test setup (per user instruction):** created a draft from the published
  config, added `startdate-field` and `enddate-field` `field` elements
  alongside the existing `date_range` picker, published, ran the bulk migration
  (28 slug-matched field mappings; 4/4 entities migrated with values intact).
- Browser-verified both directions: date_range → fields and field → date_range
  sync live. The format mismatch (date-only vs datetime) is handled gracefully
  by `parseDate` in both editors.

### #14 — Filter selectable form field bindings to the correct data type
- **Files:** `src/UdmAdminPage.tsx` (`BINDING_DATA_TYPES`, `FormElementEditor`).
- Added a `BINDING_DATA_TYPES` map alongside `BINDING_ROLES` declaring the allowed
  data types per element type / role. `undefined` means "any type" (no filter):
  `field` → role `""` → `undefined` (any data type with an editor);
  `date_range` → roles `from`/`to` → `['date', 'time', 'datetime']`. The binding
  dropdown filters `dataFields` by the allowed set; the currently-selected slug
  is always kept visible even if its type no longer matches, so an existing
  binding is never silently hidden.
- Browser-verified: `field` dropdown shows all 28 data fields (unfiltered);
  `date_range` filter applied to the same list yields only the date-compatible
  fields (`startdate`, `enddate`).

### Cross-cutting notes
- **Stable slug keys:** tree nodes are keyed by `el.slug` (not a random
  counter) to prevent unmount/remount on edits (#11).
- **Soft remove for languages:** removing a language never purges existing
  translations / field values; re-adding the code re-enables them (#5).
- **Submodel constraint split:** drafts may have orphaned submodel fields;
  publishing enforces all submodel fields have a config (#3).
- **Multi-field widget visibility:** show iff ALL bound fields are editable
  (#12, #13).
- **Schema regen:** `bash buildnodeclient.sh` was run after every backend API
  schema change (per AGENTS.md).
