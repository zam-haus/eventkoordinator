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
