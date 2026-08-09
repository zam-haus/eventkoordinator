"""Entity routes: /entities/..."""
from __future__ import annotations

import uuid
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.utils.timezone import now
from ninja import Query, Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import (
    _entity_out_for_user,
    _http409_concurrent,
    _locale,
    _require_perms,
    _set_lock_timeout_ms,
)
from userdefinedmodel.schemas import (
    BacklinkOut,
    CalendarEntryOut,
    EditGroupOut,
    EditHistoryOut,
    EntityCreateIn,
    EntityOut,
    EntityPatchIn,
    FieldEditOut,
    MigrationExecuteIn,
    MigrationPreviewOut,
    TransitionIn,
    UserRefOut,
)

router = Router(auth=django_auth)


def entity_workflow_state(entity) -> Optional[str]:
    """The name of entity's current workflow state, if it has a workflow
    field with a value (shared by backlinks and calendar entries)."""
    from userdefinedmodel.models import FieldDefinition, FieldValue

    fv = (
        FieldValue.objects.filter(
            node=entity, field__data_type=FieldDefinition.DataType.WORKFLOW, language="",
        )
        .select_related("value_workflow_state")
        .first()
    )
    return fv.value_workflow_state.name if fv and fv.value_workflow_state else None


# ─── Entities ─────────────────────────────────────────────────────────────────

def _slug_id_prefixes(config_version) -> dict[str, str]:
    """{slug: prefix} for slug_id fields, so the filter query can match the
    displayed form ("PROP-6") and not just the stored number."""
    return {
        fd.slug: (fd.type_config or {}).get("prefix")
        for fd in config_version.field_definitions.all()
        if fd.data_type == "slug_id" and (fd.type_config or {}).get("prefix")
    }

@router.get("/entities/", response=list[EntityOut], auth=django_auth)
def list_entities(request, type_id: uuid.UUID, page_size: int = 200, q: str = ""):
    """List entities for a single UDM type, filtered to those the user may view.
    Field values are reduced to the viewable set per entity (policy-enforced).

    ``q`` is an optional Lucene-like filter query (see
    :mod:`userdefinedmodel.searchquery`). It is evaluated against the
    policy-redacted serialization, so it can never match a hidden field.
    """
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.engine import evaluate_policy
    from userdefinedmodel.searchquery import (
        QuerySyntaxError,
        UnsupportedQueryFeature,
        build_document,
        match,
        parse_query,
        validate_query,
    )

    try:
        query = parse_query(q)
        validate_query(query)
    except (QuerySyntaxError, UnsupportedQueryFeature) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    _prefetch = [
        "field_values__field",
        "config_version__field_definitions",
        "config_version__config__languages",
        "user_defined_model_type__type_policies__policy",
    ]
    qs = (
        UserDefinedModelEntity.objects
        .select_related("config_version__config", "user_defined_model_type")
        .prefetch_related(*_prefetch)
        .filter(user_defined_model_type_id=type_id)
    )
    results = []
    cap = min(max(1, page_size), 200)
    prefixes_by_version: dict[uuid.UUID, dict[str, str]] = {}
    for entity in qs.iterator(chunk_size=200):
        policy = evaluate_policy(entity, request.user, "view", locale=_locale(request))
        if not policy.allow:
            continue
        out = _entity_out_for_user(entity, request.user, view_policy=policy)
        if entity.config_version_id not in prefixes_by_version:
            prefixes_by_version[entity.config_version_id] = _slug_id_prefixes(entity.config_version)
        if not match(query, build_document(out, prefixes_by_version[entity.config_version_id])):
            continue
        results.append(out)
        if len(results) >= cap:
            break
    return results


@router.post("/entities/", response={201: EntityOut}, auth=django_auth)
def create_entity(request, payload: EntityCreateIn, validate: bool = False):
    from userdefinedmodel.models import UserDefinedModelType, UserDefinedModelEntity, ConfigVersion
    from userdefinedmodel.engine import evaluate_policy
    try:
        udm_type = UserDefinedModelType.objects.select_related("field_config").get(id=payload.user_defined_model_type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "UDMType not found"}, status=404)
    if not udm_type.field_config:
        return JsonResponse({"detail": "UDMType has no field config"}, status=400)
    try:
        version = ConfigVersion.objects.get(config=udm_type.field_config, status=ConfigVersion.Status.PUBLISHED)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No published config version"}, status=400)

    if validate:
        with transaction.atomic():
            entity = UserDefinedModelEntity.objects.create(
                config_version=version, user_defined_model_type=udm_type,
            )
            entity.materialize_defaults()
            entity.materialize_user_defaults(request.user)
            result = evaluate_policy(entity, request.user, "create", locale=_locale(request))
            transaction.set_rollback(True)
        return JsonResponse({
            "valid": result.allow,
            "policy_messages": result.messages,
            "errors": {},
        })

    from userdefinedmodel.engine import PolicyError
    try:
        with transaction.atomic():
            from userdefinedmodel.actions import ActionContext, dispatch_actions
            entity = UserDefinedModelEntity.objects.create(
                config_version=version, user_defined_model_type=udm_type,
            )
            entity.materialize_defaults()
            entity.materialize_user_defaults(request.user)
            result = evaluate_policy(entity, request.user, "create", locale=_locale(request))
            if not result.allow:
                raise PolicyError(result.messages or [{"level": "critical", "text": "Create denied by policy."}])
            pre_ctx = ActionContext(
                node=entity, user=request.user, trigger="create", phase="pre",
                policy_input=result.input_document, policy_output=result,
            )
            dispatch_actions(result.actions, pre_ctx)
            post_ctx = pre_ctx.model_copy(update={"phase": "post"})
            dispatch_actions(result.actions, post_ctx)
    except PolicyError as e:
        return JsonResponse({"policy_messages": e.messages}, status=422)
    return 201, _entity_out_for_user(entity, request.user)


@router.get("/calendar-sources/", response=list[dict], auth=django_auth)
def list_calendar_sources(request):
    """{key, name, kind} for every enabled sync_core.CalendarSource — used by
    the standalone calendar dashboard's source picker. Never exposes url/
    username/password (see CalendarSource.secret_field_names)."""
    try:
        from sync_core.models import CalendarSource
    except ImportError:
        return []
    return [
        {"key": s.key, "name": s.name, "kind": s.kind}
        for s in CalendarSource.objects.filter(enabled=True).order_by("name")
    ]


@router.get("/calendar/", response=list[CalendarEntryOut], auth=django_auth)
def get_calendar(request, start: str, end: str, sources: str = ""):
    """events-and-sync.md §6: aggregated calendar entries in [start, end].

    ``sources`` is a comma-separated list of entries, each either
    ``"type_id:start_field:end_field"`` (a UDM type; ``end_field`` may be
    empty, in which case entries are point-in-time, `end == start`) or
    ``"source:<key>"`` (a sync_core CalendarSource — reads its already-fetched
    RemoteCalendarEntry rows, never a live remote fetch in the request path).
    Read access for UDM entries is policy-filtered exactly like the entity
    list; CalendarSource entries have no per-entity policy (the source itself
    is the access boundary).

    All datetimes are normalized to timezone-aware UTC before comparison —
    naive query params are treated as UTC, and DateField/date values are
    combined at midnight UTC — since RemoteCalendarEntry.start/end are always
    aware and a naive/aware comparison raises at request time otherwise.
    """
    import datetime as _dt

    from django.utils.timezone import is_naive, make_aware

    from userdefinedmodel.engine import evaluate_policy
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.summaries import compute_node_summary_parts, join_parts

    def _to_aware_utc(value: _dt.datetime) -> _dt.datetime:
        if is_naive(value):
            return make_aware(value, _dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc)

    try:
        range_start = _to_aware_utc(_dt.datetime.fromisoformat(start))
        range_end = _to_aware_utc(_dt.datetime.fromisoformat(end))
    except ValueError:
        return JsonResponse({"detail": "start/end must be ISO-8601"}, status=400)

    def _as_datetime(value):
        if value is None:
            return None
        if isinstance(value, _dt.datetime):
            return _to_aware_utc(value)
        if isinstance(value, _dt.date):
            return _dt.datetime.combine(value, _dt.time.min, tzinfo=_dt.timezone.utc)
        return None

    import re as _re

    _SUBMODEL_SPEC_RE = _re.compile(
        r"^submodel:(?P<entity>[^:]+):(?P<field>[a-z][a-z0-9_-]*)"
        r"\((?P<start>[a-z][a-z0-9_-]*)(,(?P<end>[a-z][a-z0-9_-]*))?\)$"
    )

    # Top-level source entries are comma-separated, but a submodel spec's
    # own "(start,end)" suffix also uses a comma — split only on commas not
    # enclosed in parens.
    _SOURCE_SPLIT_RE = _re.compile(r",(?![^(]*\))")

    entries = []
    for spec in _SOURCE_SPLIT_RE.split(sources):
        spec = spec.strip()
        if not spec:
            continue

        m = _SUBMODEL_SPEC_RE.match(spec)
        if m:
            from userdefinedmodel.models import UserDefinedModelEntityNode

            address = m.group("entity")
            field_slug = m.group("field")
            start_field = m.group("start")
            end_field = m.group("end")

            # `address` is either a single entity's id (a "self" spec,
            # substituted by the frontend) or a UDM type id (a type-wide
            # scope: every entity of that type). Entity ids and type ids are
            # both opaque UUIDs from independent PK spaces, so disambiguate
            # by existence check.
            root_entities = list(UserDefinedModelEntity.objects.filter(id=address))
            if not root_entities:
                root_entities = list(
                    UserDefinedModelEntity.objects.filter(user_defined_model_type_id=address)
                )
            for root_entity in root_entities:
                policy = evaluate_policy(root_entity, request.user, "view", locale=_locale(request))
                if not policy.allow:
                    continue
                children = UserDefinedModelEntityNode.objects.filter(
                    parent_node_id=root_entity.id, parent_field__slug=field_slug,
                )
                for child in children:
                    allowed = policy.viewable_fields.get(str(child.id))
                    if allowed is not None and start_field not in allowed:
                        continue
                    start_fv = child.get_field_value(start_field)
                    start_val = _as_datetime(start_fv.get_value() if start_fv else None)
                    if start_val is None:
                        continue
                    end_val = start_val
                    if end_field and (allowed is None or end_field in allowed):
                        end_fv = child.get_field_value(end_field)
                        end_val = _as_datetime(end_fv.get_value() if end_fv else None) or start_val
                    if end_val < range_start or start_val > range_end:
                        continue
                    entries.append({
                        "source": "submodel",
                        "uid": str(child.id),
                        "title": str(child.id),
                        "start": start_val.isoformat(),
                        "end": end_val.isoformat(),
                        "url": None,
                        "entity_id": str(child.id),
                        "spec": spec,
                    })
            continue

        if spec.startswith("submodel:"):
            continue  # malformed submodel spec — never fall through to the type-id parser

        parts = spec.split(":")

        if parts[0] == "source" and len(parts) == 2:
            source_key = parts[1]
            try:
                from sync_core.models import CalendarSource
            except ImportError:
                continue
            remote_entries = (
                CalendarSource.objects.filter(key=source_key, enabled=True)
                .values_list("entries__uid", "entries__title", "entries__start", "entries__end")
            )
            for uid, title, r_start, r_end in remote_entries:
                if uid is None:
                    continue
                r_start = _to_aware_utc(r_start)
                r_end = _to_aware_utc(r_end)
                if r_end < range_start or r_start > range_end:
                    continue
                entries.append({
                    "source": source_key,
                    "uid": uid,
                    "title": title,
                    "start": r_start.isoformat(),
                    "end": r_end.isoformat(),
                    "url": None,
                    "entity_id": None,
                    "spec": spec,
                })
            continue

        if len(parts) != 3:
            continue
        type_id, start_field, end_field = parts
        candidates = (
            UserDefinedModelEntity.objects.filter(user_defined_model_type_id=type_id)
            .filter(field_values__field__slug=start_field)
            .distinct()
        )
        for entity in candidates:
            start_fv = entity.field_values.filter(field__slug=start_field, language="").first()
            start_val = _as_datetime(start_fv.get_value() if start_fv else None)
            if start_val is None:
                continue
            end_val = start_val
            if end_field:
                end_fv = entity.field_values.filter(field__slug=end_field, language="").first()
                end_val = _as_datetime(end_fv.get_value() if end_fv else None) or start_val
            if end_val < range_start or start_val > range_end:
                continue
            policy = evaluate_policy(entity, request.user, "view", locale=_locale(request))
            if not policy.allow:
                continue
            parts_ = compute_node_summary_parts(entity)
            allowed = policy.viewable_fields.get(str(entity.id))
            title = join_parts(parts_, allowed_slugs=allowed) or str(entity.id)
            entries.append({
                "source": "udm",
                "uid": str(entity.id),
                "title": title,
                "start": start_val.isoformat(),
                "end": end_val.isoformat(),
                "url": f"/udm-entity/{entity.id}",
                "entity_id": str(entity.id),
                "spec": spec,
                "workflow_state": entity_workflow_state(entity),
            })
    return entries


@router.get("/entities/{entity_id}/backlinks/", response=list[BacklinkOut], auth=django_auth)
def get_entity_backlinks(
    request, entity_id: uuid.UUID,
    source_type_ids: str = "", source_field_slug: str = "",
):
    """Entities referencing entity_id via an entity_select field
    (events-and-sync.md §1.5), used by the `backlink_list` form element.

    ``source_type_ids`` is a comma-separated list of UDMType ids (empty =
    every type); ``source_field_slug`` restricts to one entity_select slug
    (empty = every field). Policy-filtered: a backlink the requesting user
    may not view is silently omitted, never surfaced as denied.
    """
    from userdefinedmodel.backlinks import find_backlinks
    from userdefinedmodel.engine import evaluate_policy
    from userdefinedmodel.models import FieldDefinition, FieldValue, UserDefinedModelEntity
    from userdefinedmodel.summaries import compute_node_summary_parts, join_parts

    try:
        UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    type_filter = {t for t in source_type_ids.split(",") if t} or None

    results = []
    for bl in find_backlinks(entity_id):
        if type_filter is not None and bl.type_id not in type_filter:
            continue
        if source_field_slug and bl.field_slug != source_field_slug:
            continue
        policy = evaluate_policy(bl.entity, request.user, "view", locale=_locale(request))
        if not policy.allow:
            continue
        parts = compute_node_summary_parts(bl.entity)
        allowed = policy.viewable_fields.get(str(bl.entity.id))
        results.append({
            "id": bl.entity.id,
            "type_id": bl.type_id,
            "field_slug": bl.field_slug,
            "workflow_state": entity_workflow_state(bl.entity),
            "preview": join_parts(parts, allowed_slugs=allowed),
        })
    results.sort(key=lambda r: r["preview"])
    return results


@router.get("/entities/{entity_id}/", response=EntityOut, auth=django_auth)
def get_entity(request, entity_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.engine import evaluate_policy
    try:
        entity = UserDefinedModelEntity.objects.select_related(
            "config_version", "user_defined_model_type"
        ).prefetch_related("field_values__field", "children").get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    # Object-level view authorization: the policy "view" allow decision gates
    # whether the entity is visible at all. 404 (not 403) avoids leaking existence
    # unless the policy produced messages explaining the denial.
    policy = evaluate_policy(entity, request.user, "view", locale=_locale(request))
    if not policy.allow:
        msgs = policy.messages or []
        if msgs:
            return JsonResponse({"detail": "Access denied", "policy_messages": msgs}, status=403)
        return JsonResponse({"detail": "Not found"}, status=404)
    return _entity_out_for_user(entity, request.user, view_policy=policy)


@router.patch("/entities/{entity_id}/", response=EntityOut, auth=django_auth)
def patch_entity(request, entity_id: uuid.UUID, payload: EntityPatchIn):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.writer import apply_patch
    from userdefinedmodel.engine import TransitionError, PolicyError

    try:
        with transaction.atomic():
            try:
                entity = (UserDefinedModelEntity.objects
                          .select_for_update(nowait=True, of=("self",))
                          .select_related("config_version")
                          .get(id=entity_id))
            except UserDefinedModelEntity.DoesNotExist:
                return JsonResponse({"detail": "Not found"}, status=404)
            except OperationalError:
                return _http409_concurrent()
            _eg, save_messages = apply_patch(entity, payload.changed_fields, request.user, locale=_locale(request))
    except PolicyError as e:
        return JsonResponse({"policy_messages": e.messages}, status=422)
    except TransitionError as e:
        if e.http_status == 409:
            return JsonResponse({"error": e.args[0], **e.details}, status=409)
        return JsonResponse({"error": str(e)}, status=e.http_status)
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
        return JsonResponse({"errors": errors}, status=400)
    except OperationalError:
        return _http409_concurrent()
    return _entity_out_for_user(entity, request.user, policy_messages=save_messages, locale=_locale(request))


@router.delete("/entities/{entity_id}/", auth=django_auth)
def delete_entity(request, entity_id: uuid.UUID):
    from django.http import HttpResponse
    from userdefinedmodel.backlinks import backlink_summary
    from userdefinedmodel.engine import evaluate_policy
    from userdefinedmodel.models import UserDefinedModelEntity
    with transaction.atomic():
        try:
            entity = (UserDefinedModelEntity.objects
                      .select_for_update(nowait=True, of=("self",))
                      .get(id=entity_id))
        except UserDefinedModelEntity.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        except OperationalError:
            return _http409_concurrent()
        # Backlinks are computed BEFORE policy evaluation: they feed the
        # delete-policy input (events-and-sync.md §1.1).
        summary = backlink_summary(entity.id)
        # Object-level delete authorization is delegated to the entity's policy
        # ("delete" action). Default-deny: no policy means no delete.
        policy = evaluate_policy(
            entity, request.user, "delete", locale=_locale(request), backlink_summary=summary,
        )
        if not policy.allow:
            return JsonResponse({"detail": "Delete denied by policy"}, status=403)
        # Application-level protect: refuse while backlinks exist unless the
        # policy explicitly forces deletion (leaving referencing ids dangling).
        if summary["count"] > 0 and not policy.force_delete:
            return JsonResponse(
                {
                    "detail": "Cannot delete: other entities reference this one",
                    "backlink_summary": summary,
                },
                status=409,
            )
        entity.delete()
    return HttpResponse(status=204)


@router.post("/entities/{entity_id}/transition/", response=EntityOut, auth=django_auth)
def transition_entity(request, entity_id: uuid.UUID, payload: TransitionIn):
    """Apply pending edits (if any) and execute the transition ATOMICALLY: the
    policy evaluates the patched, not-yet-committed state against the persisted
    pre-patch snapshot, and a denial rolls back the edits with the transition —
    they are never persisted on their own (review §4, execution semantics)."""
    from userdefinedmodel.engine import execute_transition, TransitionError, PolicyError, build_entity_document
    from userdefinedmodel.writer import apply_patch
    from userdefinedmodel.models import UserDefinedModelEntityNode

    transition_messages = []
    try:
        with transaction.atomic():
            try:
                entity = (UserDefinedModelEntityNode.objects
                          .select_for_update(nowait=True, of=("self",))
                          .select_related("config_version")
                          .get(id=entity_id))
            except UserDefinedModelEntityNode.DoesNotExist:
                return JsonResponse({"detail": "Not found"}, status=404)
            except OperationalError:
                return _http409_concurrent()
            # Snapshot the persisted state BEFORE the patch so the transition
            # policy can verify nothing unauthorized changed.
            old_entity_doc = build_entity_document(entity)
            patch_eg = None
            if payload.changed_fields:
                patch_eg, _ = apply_patch(
                    entity, payload.changed_fields, request.user,
                    _old_entity_doc=old_entity_doc, locale=_locale(request),
                )
            transition_messages = execute_transition(
                entity, payload.field, payload.transition, request.user,
                edit_group=patch_eg, locale=_locale(request), old_entity_doc=old_entity_doc,
            )
    except PolicyError as e:
        return JsonResponse({"policy_messages": e.messages}, status=422)
    except TransitionError as e:
        return JsonResponse({"error": str(e), **e.details}, status=e.http_status)
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
        return JsonResponse({"errors": errors}, status=400)
    except OperationalError:
        return _http409_concurrent()
    return _entity_out_for_user(entity, request.user, policy_messages=transition_messages, locale=_locale(request))


@router.post("/entities/{entity_id}/validation-preview/", auth=django_auth)
def validation_preview(request, entity_id: uuid.UUID, payload: EntityPatchIn):
    """ONE preview request replacing the removed validate_only modes (§4):
    applies the pending edits in a rolled-back transaction, runs a single
    'preview' policy evaluation, and returns the save verdict, all messages,
    and the per-node per-workflow-field valid-transition matrix."""
    from userdefinedmodel.engine import (
        build_entity_document, build_candidate_transitions, evaluate_policy,
        evaluate_view_precheck, TransitionError, PolicyError, _validate_subtree,
    )
    from userdefinedmodel.writer import apply_patch, serialize_changed_fields
    from userdefinedmodel.models import UserDefinedModelEntityNode

    locale = _locale(request)
    response = {"save": {"valid": True, "errors": {}}, "messages": [], "nodes": {}}
    try:
        with transaction.atomic():
            # One consolidated preview per debounce — worth waiting briefly for
            # the root lock instead of 409ing when a save/GET is in flight.
            _set_lock_timeout_ms(500)
            try:
                entity = (UserDefinedModelEntityNode.objects
                          .select_for_update(nowait=False, of=("self",))
                          .select_related("config_version")
                          .get(id=entity_id))
            except UserDefinedModelEntityNode.DoesNotExist:
                return JsonResponse({"detail": "Not found"}, status=404)
            except OperationalError:
                return _http409_concurrent()
            try:
                root = entity.get_root()
                context = root if root.pk != entity.pk else entity

                # 1. Persisted-state snapshot + VIEW pre-check (before the patch).
                old_entity_doc = build_entity_document(entity)
                view_allowed, additional_result = evaluate_view_precheck(
                    entity, request.user, old_entity_doc, locale=locale)
                if not view_allowed:
                    return JsonResponse({"detail": "Not found"}, status=404)

                # Apply pending edits; writes only — the single preview
                # evaluation below covers policy for the whole tree.
                if payload.changed_fields:
                    apply_patch(
                        entity, payload.changed_fields, request.user,
                        validate_only=True, skip_policy=True,
                        _old_entity_doc=old_entity_doc, locale=locale,
                    )

                # 2. State-valid candidates (no Rego).
                candidates = build_candidate_transitions(context)

                # 3.–5. ONE evaluation: save verdict + messages + matrix.
                output = evaluate_policy(
                    entity, request.user, "preview",
                    locale=locale,
                    changed_fields=serialize_changed_fields(payload.changed_fields or {}),
                    old_entity_doc=old_entity_doc,
                    additional_result=additional_result,
                    candidate_transitions=candidates,
                )
                has_critical = any(m.get("level") == "critical" for m in output.messages)
                response["messages"] = output.messages
                response["save"]["valid"] = bool(output.allow and not has_critical)
                # §6: list-button grants ride along in the same single evaluation
                response["deletable_nodes"] = output.deletable_nodes
                response["creatable_submodels"] = output.creatable_submodels

                # Save-rule floor: gates the save button only, never the matrix
                # (transition pre-actions may repair data at execution time).
                try:
                    _validate_subtree(context)
                except TransitionError as e:
                    response["save"]["valid"] = False
                    response["save"]["errors"] = e.details.get("field_errors", {})

                allowed = {(t.get("node"), t.get("field"), t.get("name"))
                           for t in output.valid_transitions}
                for node_id, wf_fields in candidates.items():
                    response["nodes"][node_id] = {}
                    for slug, wf in wf_fields.items():
                        response["nodes"][node_id][slug] = {
                            "current_state": wf["current_state"],
                            "valid_transitions": sorted(
                                name for name in wf["transitions"]
                                if (node_id, slug, name) in allowed
                            ),
                        }
            except PolicyError as e:
                response["save"] = {"valid": False, "errors": {}}
                response["messages"] = e.messages
            except ValidationError as exc:
                errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": [str(exc)]}
                response["save"] = {"valid": False, "errors": errors}
            finally:
                transaction.set_rollback(True)
    except OperationalError:
        return _http409_concurrent()
    return JsonResponse(response)


@router.get("/entities/{entity_id}/history/", response=EditHistoryOut, auth=django_auth)
def entity_history(
    request,
    entity_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.models.history import EditGroup
    from userdefinedmodel.engine import evaluate_policy
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    # Object-level view authorization. History exposes old/new field values, so
    # gate on the "view" allow decision and redact edits for non-viewable fields.
    policy = evaluate_policy(entity, request.user, "view", locale=_locale(request))
    if not policy.allow:
        return JsonResponse({"detail": "Not found"}, status=404)
    # Per-node grant map {node_id: [slugs]} — redact per affected node, so
    # submodel field edits are governed by their own node's grant instead of
    # being blanket-hidden by the root slug list.
    viewable = policy.viewable_fields

    qs = EditGroup.objects.filter(root_entity=entity).prefetch_related(
        "field_edits__field__translations",
        "field_edits__affected_node__parent_field",
        "field_edits__old_attachment",
        "field_edits__new_attachment",
        "saved_by",
    ).order_by("-saved_at")

    total = qs.count()
    offset = (page - 1) * page_size
    groups = list(qs[offset:offset + page_size])

    results = []
    for group in groups:
        edits = []
        for fe in group.field_edits.all():
            # Hide value edits for fields the policy does not expose to this user.
            # Non-field edits (node add/remove) carry no field value and remain
            # visible so structural history stays coherent.
            node_key = str(fe.affected_node_id) if fe.affected_node_id else str(entity.id)
            if fe.field is not None:
                # Structural rows (item added/removed/reordered) name the parent's
                # submodel field but point at the child node, so their grant lives
                # on the parent — checking the child's grants would hide them all.
                from userdefinedmodel.models.history import FieldEdit as _FE
                if fe.change_kind in (
                    _FE.ChangeKind.NODE_ADDED,
                    _FE.ChangeKind.NODE_REMOVED,
                    _FE.ChangeKind.NODE_REORDERED,
                ):
                    parent_id = getattr(fe.affected_node, "parent_node_id", None)
                    grant_key = str(parent_id) if parent_id else str(group.node_id)
                else:
                    grant_key = node_key
                if fe.field.slug not in viewable.get(grant_key, []):
                    continue
            # Identify the affected (sub)model by the preview label it carried
            # before the edit, redacted to the parts this user may see.
            from userdefinedmodel.summaries import join_parts
            node_summary = join_parts(fe.affected_node_summary, viewable.get(node_key, [])) or None
            node_field = None
            if fe.affected_node_id and fe.affected_node_id != entity.id:
                parent_field = getattr(fe.affected_node, "parent_field", None)
                node_field = parent_field.slug if parent_field else None

            slug = fe.field.slug if fe.field else None
            label = None
            if fe.field:
                # Labels now live on FormElement (B1). Resolve via the bound
                # element's translation; fall back to the slug.
                from userdefinedmodel.models import FormElementTranslation
                trans = (
                    FormElementTranslation.objects
                    .filter(element__bindings__data_field=fe.field)
                    .first()
                )
                label = trans.label if trans and trans.label else slug
            from userdefinedmodel.models.history import FieldEdit
            is_policy_action = fe.change_kind in (
                FieldEdit.ChangeKind.POLICY_PRE_ACTION,
                FieldEdit.ChangeKind.POLICY_POST_ACTION,
            )
            edits.append(FieldEditOut(
                change_kind=fe.change_kind,
                field_slug=slug,
                field_label=label,
                language=fe.language,
                old_value=fe.old_value,
                new_value=fe.new_value if (not is_policy_action or request.user.is_superuser) else None,
                old_file_name=fe.old_attachment.original_name if fe.old_attachment else None,
                new_file_name=fe.new_attachment.original_name if fe.new_attachment else None,
                affected_node_id=fe.affected_node_id,
                affected_node_summary=node_summary,
                affected_node_field=node_field,
            ))

        node_type = "entity"
        try:
            group.node.userdefinedmodelentity
        except Exception:
            pf = getattr(group.node, "parent_field", None)
            if pf:
                node_type = f"submodel:{pf.slug}"

        results.append(EditGroupOut(
            id=group.id,
            saved_at=group.saved_at.isoformat(),
            saved_by=UserRefOut(id=group.saved_by.id, display_name=group.saved_by.username) if group.saved_by else None,
            node_id=group.node_id,
            node_type=node_type,
            edits=edits,
        ))

    next_url = None
    if offset + page_size < total:
        next_url = f"/api/udm/entities/{entity_id}/history/?page={page + 1}&page_size={page_size}"

    return EditHistoryOut(count=total, next=next_url, results=results)


@router.get("/entities/{entity_id}/policy-document/", auth=django_auth)
def entity_policy_document(request, entity_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelEntity
    # Returns the raw, unredacted policy input document (every field value,
    # bypassing the policy's own field-level visibility). Treat it like policy
    # internals: require both view and change policy permissions.
    if denied := _require_perms(request, "userdefinedmodel.view_policy", "userdefinedmodel.change_policy"):
        return denied
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return JsonResponse(entity.to_policy_document())


# ─── Migration ────────────────────────────────────────────────────────────────

def _resolve_migration_target(entity, target_type_id, target_version_id):
    """Resolve (target_type, target_version) for a migration, or raise ApiError.

    Either an explicit ConfigVersion id, or a UDMType id whose published config
    version is used. Shared by preview (pure) and execute (creates the record).
    """
    from userdefinedmodel.api_helpers import ApiError
    from userdefinedmodel.models import ConfigVersion, UserDefinedModelType
    if target_version_id:
        try:
            tgt_version = ConfigVersion.objects.get(id=target_version_id)
        except ConfigVersion.DoesNotExist:
            raise ApiError(404, {"detail": "Target version not found"})
        tgt_type = entity.user_defined_model_type
    elif target_type_id:
        try:
            tgt_type = UserDefinedModelType.objects.select_related("field_config").get(id=target_type_id)
        except UserDefinedModelType.DoesNotExist:
            raise ApiError(404, {"detail": "Target type not found"})
        try:
            tgt_version = ConfigVersion.objects.get(config=tgt_type.field_config, status=ConfigVersion.Status.PUBLISHED)
        except ConfigVersion.DoesNotExist:
            raise ApiError(404, {"detail": "Target type has no published config"})
    else:
        raise ApiError(400, {"detail": "Either target_user_defined_model_type or target_version is required"})
    return tgt_type, tgt_version


@router.get("/entities/{entity_id}/migration-preview/", response=MigrationPreviewOut, auth=django_auth)
def migration_preview(
    request,
    entity_id: uuid.UUID,
    target_user_defined_model_type: Optional[uuid.UUID] = None,
    target_version: Optional[uuid.UUID] = None,
):
    """Side-effect-free migration preview: the migration record is created by
    the execute endpoint, so refreshes/retries of this GET write nothing."""
    from userdefinedmodel.models import UserDefinedModelEntity
    from userdefinedmodel.schemas import MigrationAction, MigrationPreviewFieldOut
    if not request.user.is_superuser:
        return JsonResponse({"detail": "Not allowed"}, status=403)
    try:
        entity = UserDefinedModelEntity.objects.select_related("config_version").get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    _tgt_type, tgt_version = _resolve_migration_target(
        entity, target_user_defined_model_type, target_version)

    source_fields = {f.slug: f for f in entity.config_version.field_definitions.all()}
    target_fields = {f.slug: f for f in tgt_version.field_definitions.all()}
    _ALLOWED = {
        ("integer", "float"), ("text_short", "text_long"), ("text_long", "text_markdown"),
        ("select_single", "select_multi"), ("user_select", "user_select_multi"),
        ("group_select", "group_select_multi"), ("entity_select", "entity_select_multi"),
    }

    previews = []
    for slug, src_field in source_fields.items():
        if slug in target_fields:
            tgt_field = target_fields[slug]
            if src_field.data_type == tgt_field.data_type or (src_field.data_type, tgt_field.data_type) in _ALLOWED:
                previews.append(MigrationPreviewFieldOut(
                    source_slug=slug, source_data_type=src_field.data_type,
                    suggested_action=MigrationAction.MAP, suggested_target_slug=slug, conflict_reason=None,
                ))
            else:
                previews.append(MigrationPreviewFieldOut(
                    source_slug=slug, source_data_type=src_field.data_type,
                    suggested_action=MigrationAction.OVERFLOW, suggested_target_slug=None,
                    conflict_reason=f"Incompatible: {src_field.data_type} → {tgt_field.data_type}",
                ))
        else:
            previews.append(MigrationPreviewFieldOut(
                source_slug=slug, source_data_type=src_field.data_type,
                suggested_action=MigrationAction.OVERFLOW, suggested_target_slug=None, conflict_reason=None,
            ))

    return MigrationPreviewOut(
        source_version_id=entity.config_version_id,
        target_version_id=tgt_version.id,
        field_previews=previews,
    )


def _resolve_migration_value(src_fv, tgt_field):
    """Return a value for set_value(val, field=tgt_field), or None to skip.

    Workflow fields: get_value() returns the state name string, which cannot be
    assigned directly as value_workflow_state_id. Resolve by name in the target
    workflow version; return None if not found so materialize_defaults() sets the initial state.
    """
    from userdefinedmodel.models.config import FieldDefinition
    val = src_fv.get_value()
    if val is None:
        return None
    if tgt_field.data_type == FieldDefinition.DataType.WORKFLOW:
        if not tgt_field.workflow_version_id or not isinstance(val, str):
            return None
        from userdefinedmodel.models import WorkflowState
        return WorkflowState.objects.filter(
            version_id=tgt_field.workflow_version_id, name=val
        ).first()
    return val


@router.post("/entities/{entity_id}/migrate/", response=EntityOut, auth=django_auth)
def execute_migration(request, entity_id: uuid.UUID, payload: MigrationExecuteIn):
    from userdefinedmodel.models import (
        UserDefinedModelEntity, UserDefinedModelEntityMigration, MigrationFieldMapping, FieldValue,
    )
    if not request.user.is_superuser:
        return JsonResponse({"detail": "Not allowed"}, status=403)
    try:
        entity = UserDefinedModelEntity.objects.get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    with transaction.atomic():
        try:
            entity = (UserDefinedModelEntity.objects
                      .select_for_update(nowait=True, of=("self",))
                      .get(id=entity_id))
        except OperationalError:
            return _http409_concurrent()

        tgt_type, tgt_version = _resolve_migration_target(
            entity, payload.target_user_defined_model_type_id, payload.target_version_id)
        migration = UserDefinedModelEntityMigration.objects.create(
            user_defined_model_entity=entity,
            source_version=entity.config_version,
            target_user_defined_model_type=tgt_type,
            target_version=tgt_version,
        )
        source_field_map = {f.slug: f for f in entity.config_version.field_definitions.all()}
        target_field_map = {f.slug: f for f in tgt_version.field_definitions.all()}

        for mapping_in in payload.field_mappings:
            src_field = source_field_map.get(mapping_in.source_field_slug)
            if not src_field:
                continue
            # All languages: localized fields carry one FieldValue row per language.
            fvs = list(entity.field_values.filter(field=src_field))
            if not fvs:
                continue
            action = mapping_in.action.value
            if action == "map" and mapping_in.target_field_slug:
                tgt_field = target_field_map.get(mapping_in.target_field_slug)
                if tgt_field:
                    for fv in fvs:
                        val = _resolve_migration_value(fv, tgt_field)
                        if val is not None:
                            new_fv, _ = FieldValue.objects.get_or_create(node=entity, field=tgt_field, language=fv.language)
                            new_fv.set_value(val, field=tgt_field)
                            new_fv.save()
            # "discard": nothing to carry over — the old FieldValue rows belong
            # to the source version and stop being serialized after the switch.
            MigrationFieldMapping.objects.create(
                migration=migration,
                source_field=src_field,
                action=action,
                target_field=target_field_map.get(mapping_in.target_field_slug) if mapping_in.target_field_slug else None,
            )

        entity.config_version = tgt_version
        entity.user_defined_model_type = tgt_type
        try:
            entity.validate_for_save()
        except ValidationError as exc:
            transaction.set_rollback(True)
            errors = exc.message_dict if hasattr(exc, "message_dict") else {"__all__": exc.messages}
            return JsonResponse({"errors": errors}, status=400)
        entity.save(update_fields=["config_version", "user_defined_model_type"])
        entity.materialize_defaults()
        # Save gate on the MIGRATED state: evaluate "save" as if the new entity
        # were the preexisting one (old doc == new doc), so the policy verifies
        # the migrated model is valid as-is under its new config/type. A denial
        # rolls back the whole migration.
        from userdefinedmodel.engine import evaluate_policy, build_entity_document
        result = evaluate_policy(
            entity, request.user, "save",
            locale=_locale(request),
            old_entity_doc=build_entity_document(entity),
        )
        if not result.allow:
            transaction.set_rollback(True)
            return JsonResponse(
                {"detail": "Not allowed", "policy_messages": result.messages}, status=403,
            )
        migration.executed_at = now()
        migration.executed_by = request.user
        migration.save(update_fields=["executed_at", "executed_by"])

    return _entity_out_for_user(entity, request.user)
