"""UDMType routes: /types/..."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from django.http import JsonResponse
from ninja import Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _locale, _require_perms, _serialize_config_version
from userdefinedmodel.schemas import (
    ConfigVersionOut,
    PolicyAssignIn,
    PolicyEvalOut,
    PolicyOut,
    TypePublicFieldsOut,
    UDMTypeCreateIn,
    UDMTypeOut,
    UDMTypeUpdateIn,
)

logger = logging.getLogger(__name__)

router = Router(auth=django_auth)


# ─── UDMType ──────────────────────────────────────────────────────────────────

def _udmtype_out(t) -> UDMTypeOut:
    return UDMTypeOut(id=t.id, name=t.name, label=t.label, field_config_id=t.field_config_id)


@router.get("/types/", response=list[UDMTypeOut], auth=django_auth)
def list_udm_types(request):
    from userdefinedmodel.models import UserDefinedModelType
    if denied := _require_perms(request, "userdefinedmodel.view_userdefinedmodeltype"):
        return denied
    types = UserDefinedModelType.objects.select_related("field_config").all()
    return [_udmtype_out(t) for t in types]


@router.post("/types/", response={201: UDMTypeOut}, auth=django_auth)
def create_udm_type(request, payload: UDMTypeCreateIn):
    from userdefinedmodel.models import UserDefinedModelType
    if denied := _require_perms(request, "userdefinedmodel.add_userdefinedmodeltype"):
        return denied
    udm_type = UserDefinedModelType.objects.create(
        name=payload.name, label=payload.label,
    )
    return 201, _udmtype_out(udm_type)


@router.get("/types/{type_id}/", response=UDMTypeOut, auth=django_auth)
def get_udm_type(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType
    try:
        t = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return _udmtype_out(t)


@router.get("/types/{type_id}/eval-policy/", response=PolicyEvalOut, auth=django_auth)
def eval_policy_for_type(
    request,
    type_id: uuid.UUID,
    entity_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str = "view",
    transition: Optional[str] = None,
):
    """Evaluate the Rego policy for a given entity + user and return the full
    input document, the raw policy sources, and the structured output.

    The input document includes the entity's full field values — data the policy
    would otherwise hide — plus the raw policy source. Require both view and
    change policy permissions; never gate on staff/superuser status alone."""
    if denied := _require_perms(request, "userdefinedmodel.view_policy", "userdefinedmodel.change_policy"):
        return denied

    from userdefinedmodel.models import UserDefinedModelEntity, UserDefinedModelType
    from userdefinedmodel.engine import build_policy_input, get_udm_type_for_node

    try:
        entity = UserDefinedModelEntity.objects.select_related(
            "config_version", "user_defined_model_type"
        ).get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Entity not found"}, status=404)

    try:
        from openid_user_management.models import OpenIDUser
        eval_user = OpenIDUser.objects.prefetch_related("groups", "user_permissions").get(id=user_id)
    except Exception:
        return JsonResponse({"detail": "User not found"}, status=404)

    # Collect policy sources
    udm_type = get_udm_type_for_node(entity)
    policy_entries = []
    if udm_type:
        for tp in udm_type.type_policies.select_related("policy").order_by("sort_order"):
            policy_entries.append({"slug": tp.policy.slug, "source": tp.policy.source})

    # Build input document. Transition needs its descriptor; resolve it from
    # the first workflow field that defines the named transition.
    kwargs = {}
    if transition and action == "transition":
        from userdefinedmodel.models import FieldDefinition, WorkflowTransition
        for fd in entity.config_version.field_definitions.filter(
            data_type=FieldDefinition.DataType.WORKFLOW
        ).select_related("workflow_version"):
            t = WorkflowTransition.objects.filter(
                version=fd.workflow_version, name=transition
            ).select_related("from_state", "to_state", "version").first()
            if t:
                kwargs.update(
                    transition=transition, field=fd.slug, node_id=str(entity.id),
                    transition_descriptor=t.to_descriptor(),
                )
                break
    if action in ("save", "transition", "preview"):
        from userdefinedmodel.engine import build_entity_document
        kwargs.setdefault("old_entity_doc", build_entity_document(entity))
    if action == "preview":
        from userdefinedmodel.engine import build_candidate_transitions
        kwargs["candidate_transitions"] = build_candidate_transitions(entity)
    input_doc = build_policy_input(entity, eval_user, action, locale=_locale(request), **kwargs)

    # Run evaluation on the SAME compiled-session code path as the engine
    # (§3.1-2), reading the single aggregate rule data.udm.result.
    error_msg = None
    output = {"allow": False, "messages": [], "viewable_fields": {}, "editable_fields": {}}
    eval_prints: list[str] = []
    eval_coverage: list[dict] = []
    eval_rule_errors: list[str] = []
    full_document = None
    if policy_entries:
        try:
            import json as _json
            from userdefinedmodel.engine import RegoSession, _UNDEFINED
            session = RegoSession([
                (f"policy_{entry['slug']}.rego", entry["source"]) for entry in policy_entries
            ])
            eng = session.clone()
            eng.set_input_json(_json.dumps(input_doc))
            eng.set_gather_prints(True)
            eng.set_enable_coverage(True)

            try:
                result_val = RegoSession.eval_rule(eng, "data.udm.result")
            except Exception as exc:
                eval_rule_errors.append(f"data.udm.result: {exc}")
                result_val = None
            if isinstance(result_val, dict):
                output = result_val
            else:
                eval_rule_errors.append("data.udm.result: undefined or not an object (deny)")

            try:
                raw_full = eng.eval_query_as_json("data.udm")
                logger.debug("policy full document entity=%s action=%s raw=%s", entity_id, action, raw_full)
                parsed_full = _json.loads(raw_full)
                full_document = None if parsed_full == _UNDEFINED else parsed_full
            except Exception as full_exc:
                logger.debug("policy full document error entity=%s action=%s", entity_id, action, exc_info=full_exc)
                eval_rule_errors.append(f"data.udm: {full_exc}")
                full_document = None

            eval_prints = eng.take_prints()
            coverage_json = _json.loads(eng.get_coverage_report_as_json())
            # Strip the redundant `code` field — sources are already in `policies`.
            eval_coverage = [
                {k: v for k, v in f.items() if k != "code"}
                for f in coverage_json.get("files", [])
            ]
        except Exception as exc:
            error_msg = str(exc)
            output = {"allow": False, "messages": [], "viewable_fields": {}, "editable_fields": {}}

    return PolicyEvalOut(
        input_document=input_doc,
        policies=policy_entries,
        output=output,
        full_document=full_document if policy_entries else None,
        error=error_msg,
        rule_errors=eval_rule_errors,
        prints=eval_prints,
        coverage=eval_coverage,
    )


@router.get("/types/{type_id}/config/", response=ConfigVersionOut, auth=django_auth)
def get_type_config(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType, ConfigVersion
    try:
        udm_type = UserDefinedModelType.objects.select_related("field_config").get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if not udm_type.field_config:
        return JsonResponse({"detail": "No field config assigned"}, status=404)
    try:
        version = ConfigVersion.objects.get(config=udm_type.field_config, status=ConfigVersion.Status.PUBLISHED)
    except ConfigVersion.DoesNotExist:
        return JsonResponse({"detail": "No published version"}, status=404)
    return _serialize_config_version(version)


@router.get("/types/{type_id}/public-fields/", response=TypePublicFieldsOut, auth=django_auth)
def get_type_public_fields(request, type_id: uuid.UUID):
    """Evaluate data.udm.public_type_fields from the type's policies.

    Runs all policies for this type with a minimal input
    (action=public_type_fields, no entity or user) and returns the resulting
    dict.  Returns an empty dict when no policies are attached or the rule is
    not defined.
    """
    from userdefinedmodel.models import UserDefinedModelType
    from userdefinedmodel.engine import evaluate_type_public_fields
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    _, descriptions = evaluate_type_public_fields(udm_type, user=request.user, locale=_locale(request))
    return TypePublicFieldsOut(descriptions=descriptions)


@router.patch("/types/{type_id}/", response=UDMTypeOut, auth=django_auth)
def update_udm_type(
    request,
    type_id: uuid.UUID,
    field_config_id: Optional[uuid.UUID] = None,
    payload: Optional[UDMTypeUpdateIn] = None,
):
    from userdefinedmodel.models import UserDefinedModelType, FieldConfig
    if denied := _require_perms(request, "userdefinedmodel.change_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if payload is not None:
        if payload.name is not None:
            udm_type.name = payload.name
        if payload.label is not None:
            udm_type.label = payload.label
        udm_type.save()
    if field_config_id is not None:
        try:
            cfg = FieldConfig.objects.get(id=field_config_id)
        except FieldConfig.DoesNotExist:
            return JsonResponse({"detail": "FieldConfig not found"}, status=404)
        from userdefinedmodel.models import BulkMigrationPlan, UserDefinedModelEntity
        stale = UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).exclude(config_version__config=cfg)
        if stale.exists():
            confirmed_plans = BulkMigrationPlan.objects.filter(
                target_version__config=cfg,
                user_defined_model_type_filter=udm_type,
                status=BulkMigrationPlan.Status.DONE,
            )
            if not confirmed_plans.exists():
                return JsonResponse({"detail": "Stale entities exist without a confirmed BulkMigrationPlan"}, status=400)
        udm_type.field_config = cfg
        udm_type.save()
    return _udmtype_out(udm_type)


@router.delete("/types/{type_id}/", auth=django_auth)
def delete_udm_type(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType, UserDefinedModelEntity
    if denied := _require_perms(request, "userdefinedmodel.delete_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    if UserDefinedModelEntity.objects.filter(user_defined_model_type=udm_type).exists():
        return JsonResponse({"detail": "UDMType still has entities and cannot be deleted"}, status=400)
    udm_type.delete()
    return JsonResponse({}, status=204)


@router.get("/types/{type_id}/policies/", response=list[PolicyOut], auth=django_auth)
def list_type_policies(request, type_id: uuid.UUID):
    from userdefinedmodel.models import UserDefinedModelType
    if denied := _require_perms(request, "userdefinedmodel.view_policy"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    return [PolicyOut(slug=tp.policy.slug, source=tp.policy.source)
            for tp in udm_type.type_policies.select_related("policy").order_by("sort_order")]


@router.post("/types/{type_id}/policies/", response={201: PolicyOut}, auth=django_auth)
def assign_policy(request, type_id: uuid.UUID, payload: PolicyAssignIn):
    from userdefinedmodel.models import UserDefinedModelType, Policy, UserDefinedModelTypePolicy
    if denied := _require_perms(request, "userdefinedmodel.change_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    try:
        policy = Policy.objects.get(slug=payload.policy_slug)
    except Policy.DoesNotExist:
        return JsonResponse({"detail": "Policy not found"}, status=404)
    UserDefinedModelTypePolicy.objects.get_or_create(
        user_defined_model_type=udm_type, policy=policy,
        defaults={"sort_order": payload.sort_order},
    )
    return 201, PolicyOut(slug=policy.slug, source=policy.source)


@router.delete("/types/{type_id}/policies/{slug}/", auth=django_auth)
def remove_policy(request, type_id: uuid.UUID, slug: str):
    from userdefinedmodel.models import UserDefinedModelType, UserDefinedModelTypePolicy
    if denied := _require_perms(request, "userdefinedmodel.change_userdefinedmodeltype"):
        return denied
    try:
        udm_type = UserDefinedModelType.objects.get(id=type_id)
    except UserDefinedModelType.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    UserDefinedModelTypePolicy.objects.filter(user_defined_model_type=udm_type, policy__slug=slug).delete()
    return JsonResponse({}, status=204)
