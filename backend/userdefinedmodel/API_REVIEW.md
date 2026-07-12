# UDM API Bug Review (2026-07-12)

Scope: all `backend/userdefinedmodel/api_*.py` route modules plus `api_helpers.py`.
Findings are ordered by severity. Each finding carries a **Decision** recorded from the
2026-07-12 fix questionnaire.

**Status (2026-07-13): all decisions below are IMPLEMENTED** (backend + frontend +
tests, full `userdefinedmodel` suite green). Low-severity items remain deferred as
decided. Implementation notes:

- `ApiError(status, payload)` lives in `api_helpers.py`; the handler is registered on
  the NinjaAPI in `api.py`. Used in `replace_draft`, `update_workflow`,
  `execute_migration`, and also `create_bulk_migration` (same partial-commit class).
- Migration contract change: `MigrationPreviewOut` no longer returns `migration_id`;
  `MigrationExecuteIn` takes `target_version_id` / `target_user_defined_model_type_id`
  and the record is created at execute time. Frontend (`apiUdm.ts`, `UdmMigration.tsx`)
  and the generated `schema_udm.d.ts` updated accordingly.
- Autocomplete `ids`/`type_ids`/`group_ids` are now typed Ninja list params (repeated
  query keys); the frontend helpers accept arrays and split legacy comma strings.
- Bonus fix: `FieldDefinitionIn` now accepts `workflow_definition_id` so the
  as-input export round-trips into `replace_draft` (pre-existing test failure).

## High

### 1. Early `return` inside `transaction.atomic()` commits partial writes (data loss)
Returning a `JsonResponse` from inside an atomic block is a *normal* exit — Django
commits everything written so far. Several endpoints validate *after* destructive writes:

- **`replace_draft`** (`api_configs.py:254-310`): `draft.field_definitions.all().delete()`
  runs first; the duplicate-prefix check, prefix-conflict check, missing
  submodel/workflow-version checks, and default-value errors all `return` a 400 afterwards.
  Result: the existing draft's fields are wiped (or a partial field set is kept) and
  **committed** despite the error response.
- **`execute_migration`** (`api_entities.py:610-614`): when `validate_for_save()` fails,
  a 400 is returned, but the `FieldValue` rows and `MigrationFieldMapping` rows created
  above are committed — the entity ends up half-migrated while the client sees an error.
- **`update_workflow`** (`api_workflows.py:147-165`): the "exactly one is_initial" check
  and the rename-collision check return 400 after `wf_def.save()`, `draft.save()`, and
  earlier state renames — those mutations commit.

**Decision:** introduce a custom exception (e.g. `ApiError(status, payload)`) raised
inside atomic blocks — the exception aborts the transaction automatically — and convert
it to a `JsonResponse` in a shared handler/decorator (or a Ninja exception handler).
Migrate the affected endpoints' error returns to raises.

### 2. `create_entity`: uncaught `PolicyError` → HTTP 500
`api_entities.py:110-112` raises `PolicyError` when the create policy denies, but unlike
`patch_entity`/`transition_entity` there is no `except PolicyError` handler. A policy
denial (a perfectly normal outcome) surfaces as a 500 Internal Server Error instead of a
422 with `policy_messages`.

**Decision:** wrap in `except PolicyError` and return
`{"policy_messages": e.messages}` with status 422, matching `patch_entity` /
`transition_entity`.

### 3. `migration_preview` (GET) creates a database record on every call
`api_entities.py:484-489`: a `UserDefinedModelEntityMigration` row is created inside a
GET handler. GETs must be side-effect free — browser prefetching, retries, or a user
refreshing the preview page create unbounded orphan migration records.

**Decision:** make the preview pure (no DB writes) and create the migration record at
execute time — `execute_migration` takes the source/target parameters and creates the
`UserDefinedModelEntityMigration` itself. Requires a small frontend/API contract change
(preview no longer returns a `migration_id`).

### 4. Autocomplete `ids` parameter silently discards other filters
- **`search_users`** (`api_autocomplete.py:23-25`): when `ids` is given, the queryset is
  rebuilt from `OpenIDUser.objects.filter(id__in=...)`, dropping the `is_active=True`
  filter (inactive/deactivated users are returned) as well as the `q`/`group_ids` filters.
- **`search_groups`** (`api_autocomplete.py:35-37`) and **`search_entities`**
  (`api_autocomplete.py:57-61`): same pattern — `ids` replaces the queryset, discarding
  `q`/`type_ids`.

**Decision:** combine with the other filters — `qs = qs.filter(id__in=...)` — so
`is_active`, `q`, `group_ids`/`type_ids` still apply.

### 5. Unvalidated UUID/ID query params → 500
- `search_entities`: `type_ids` / `ids` fragments are passed unparsed into
  `id__in`; a non-UUID string raises `ValidationError`/`ValueError` → 500.
- `search_users`: `ids` values likewise unvalidated (users are UUID-keyed).
- `upload_staging_file` (`api_staging.py:32`): `intended_field_id` is written as a raw FK
  without existence check — a random UUID raises `IntegrityError` → 500.

**Decision:** switch the comma-separated string params to typed Ninja params
(`list[uuid.UUID]` / `list[int]`) so malformed values are rejected with an automatic
422; validate `intended_field_id` existence and return 400 when the field doesn't exist.

## Medium

### 6. `entity_history` pagination is unbounded and crashes on `page=0`
`api_entities.py:340,367-368`: `page_size` has no cap (a client can request millions of
rows) and `page <= 0` produces a negative offset — Django querysets raise
"Negative indexing is not supported" → 500.

**Decision:** use Ninja constrained query params (`page: int = Query(1, ge=1)`,
`page_size: int = Query(20, ge=1, le=100)`) so out-of-range values are rejected with an
automatic 422 — no clamping logic in the handler.

### 7. `execute_migration` only migrates one language of localized fields
`api_entities.py:585`: `entity.field_values.filter(field=src_field).first()` picks a
single `FieldValue`. For `is_localized` fields with several language rows, all but one
language's value is silently dropped (and for overflow, only one language is preserved).

**Decision:** iterate every `FieldValue` per source field (all languages) when mapping
and when building overflow data.

### 8. `execute_migration` can be replayed
There is no check that `migration.executed_at` is null, so re-POSTing the same
`migration_id` re-applies mappings after the entity already moved to the target version
(source-slug lookups then run against the *new* config version, producing wrong or no-op
mappings plus duplicate `MigrationFieldMapping` rows).

**Decision:** return 409 when `migration.executed_at` is already set.

**Update 2026-07-12:** both migration endpoints (`migration_preview`,
`execute_migration`) are now additionally restricted to superusers (403 otherwise).
In `execute_migration`, the policy "save" gate moved from the pre-migration state to
the *migrated* state: it is evaluated inside the transaction after the config/type
switch, with `old_entity_doc` built from the migrated entity itself (old == new), so
the policy verifies the model is valid as-is under its new config. A denial calls
`transaction.set_rollback(True)` and returns 403 with the policy messages.

### 9. `delete_entity` has no lock / atomic block
`api_entities.py:176-188`: unlike patch/transition, delete evaluates the policy and
deletes without `select_for_update`, so it can race a concurrent patch (policy evaluated
against stale state, or delete mid-save). Also returns `JsonResponse({}, status=204)` —
204 must not carry a body (several endpoints share this nit).

**Decision:** give `delete_entity` the same `select_for_update(nowait=True)` + atomic +
409-on-conflict pattern as patch/transition, and drop bodies from 204 responses.

### 10. `list_udm_types` permission check is commented out
`api_types.py:40-43`: the TODO leaves every authenticated user able to enumerate all
UDM types and their config IDs.

**Decision:** type metadata is intentionally visible to all authenticated users. Remove
the TODO and the commented-out check, and add a comment documenting the decision.

### 11. `assign_policy` ignores `sort_order` on re-assign and always returns 201
`api_types.py:387-391`: `get_or_create` with `sort_order` only in `defaults` means
re-posting with a new sort_order is a silent no-op that still reports 201 Created.

**Decision:** use `update_or_create` so re-assigning updates `sort_order`, and return
200 for updates vs 201 for creates.

### 12. `create_config` doesn't validate languages
`api_configs.py:84-90`: no check that exactly one language has `is_default=True`, nor for
duplicate codes — later default-language lookups (`_entity_preview_display`) quietly
misbehave.

**Decision:** validate in `create_config`: reject duplicate language codes and require
exactly one `is_default=True`, returning 400 otherwise.

## Low

**Decision:** all low-severity items below are deferred — no fixes planned for now,
except that `list_udm_types` (see #10) is confirmed intentionally public.

- **`_wcag_text_color`** (`api_helpers.py:29`): non-hex characters raise `ValueError`
  → 500; wrap the `int(..., 16)` parse.
- **`_create_field_default`** (`api_helpers.py:237`): `except (DjangoValidationError,
  Exception)` catches *everything* (the first class is redundant), converting programming
  errors into user-facing validation strings.
- **`delete_workflow`** (`api_workflows.py:241`): bare `except Exception` maps every
  failure — including bugs — to "Workflow is in use" 409.
- **`entity_history`** (`api_entities.py:404-406`): bare `except Exception` around
  `group.node.userdefinedmodelentity` hides real errors; catch the specific
  `RelatedObjectDoesNotExist` / `ObjectDoesNotExist`.
- **`upload_staging_file`**: no file-size or MIME-type limit beyond server defaults;
  consider an explicit cap. `parse_bundle_zip` / `import_bundle_zip` similarly read the
  whole ZIP into memory with no size limit (zip-bomb / memory DoS by any authenticated
  user — `parse_bundle_zip` has no permission gate at all).
- **`list_entities`** (`api_entities.py:41-69`): `page_size` exists but there is no
  offset/cursor, so results beyond the first ~200 viewable entities are unreachable; the
  loop also evaluates the Rego policy per row over the whole table (perf).
- **`migration_preview` / `execute_migration`** call `_policy_allows(..., "save")`
  without `locale=`, unlike every other call site — policy messages lose localization.
- **`get_published_version` / `create_entity`** use `ConfigVersion.objects.get(...,
  status=PUBLISHED)`: if the schema ever permits two published versions,
  `MultipleObjectsReturned` → 500 (elsewhere `_field_config_out` defensively uses
  `.order_by(...).first()`).

## Fix plan summary

| # | Issue | Decision |
|---|-------|----------|
| 1 | Partial commits in atomic blocks | Custom `ApiError` exception + shared handler |
| 2 | create_entity PolicyError → 500 | Catch → 422 with `policy_messages` |
| 3 | migration_preview GET side effect | Pure preview; create record at execute time |
| 4 | Autocomplete `ids` drops filters | `qs.filter(id__in=...)`, combine with other filters |
| 5 | Unvalidated ID params → 500 | Typed Ninja params (auto-422); FK check → 400 |
| 6 | entity_history pagination | Ninja `ge`/`le` constraints (auto-422) |
| 7 | Localized fields in migration | Migrate all language `FieldValue`s |
| 8 | Migration replay | 409 when `executed_at` set |
| 9 | delete_entity race / 204 body | select_for_update + atomic + 409; bodyless 204 |
| 10 | list_udm_types perms TODO | Intentionally public; remove TODO, document |
| 11 | assign_policy sort_order no-op | `update_or_create`; 200 update / 201 create |
| 12 | create_config language validation | Reject duplicates; exactly one default → 400 |
| — | All Low items | Deferred |
