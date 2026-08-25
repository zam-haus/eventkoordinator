"""UDMType routes: /types/..."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from django.http import HttpResponse, JsonResponse
from ninja import Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _locale, _require_perms, _serialize_config_version
from userdefinedmodel.schemas import (
    ConfigVersionOut,
    EvalNodeOut,
    EvalWorkflowFieldOut,
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
    # Intentionally no model-permission gate: type metadata (name/label/config id)
    # is visible to every authenticated user; entity data stays policy-gated.
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


def _require_evaluator_access(request):
    """Access gate shared by the policy-evaluator endpoints.

    The input document includes the entity's full field values — data the policy
    would otherwise hide — plus the raw policy source. Require both view and
    change policy permissions. Superusers may evaluate policies for all models
    regardless of the granular policy permissions."""
    if request.user.is_superuser:
        return None
    return _require_perms(request, "userdefinedmodel.view_policy", "userdefinedmodel.change_policy")


@router.get("/types/{type_id}/eval-policy/", response=PolicyEvalOut, auth=django_auth)
def eval_policy_for_type(
    request,
    type_id: uuid.UUID,
    entity_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str = "view",
    transition: Optional[str] = None,
    node_id: Optional[uuid.UUID] = None,
    sudo: bool = False,
):
    """Evaluate the Rego policy for a given entity + user and return the full
    input document, the raw policy sources, and the structured output.

    ``action`` accepts every entity action of the policy contract, including
    ``browse`` and ``create``. For ``transition``, ``node_id`` selects the
    target node (a submodel node of the entity, or the entity itself when
    omitted); the transition is resolved against that node's own schema."""
    if denied := _require_evaluator_access(request):
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

    # Simulate the session sudo toggle for the evaluated user. _serialize_user
    # ANDs the flag with is_superuser, so it stays false for non-superusers.
    if sudo:
        eval_user.sudo_mode = True

    # Collect policy sources
    udm_type = get_udm_type_for_node(entity)
    policy_entries = []
    if udm_type:
        for tp in udm_type.type_policies.select_related("policy").order_by("sort_order"):
            policy_entries.append({"slug": tp.policy.slug, "source": tp.policy.source})

    # Build input document. Transition needs its descriptor; resolve it from
    # the first workflow field ON THE TARGET NODE that defines the named
    # transition — for submodel nodes that is the node's own schema, not the
    # root entity's.
    kwargs = {}
    if transition and action == "transition":
        from userdefinedmodel.models import FieldDefinition, UserDefinedModelEntityNode, WorkflowTransition
        target = entity
        if node_id is not None and node_id != entity.id:
            try:
                target = UserDefinedModelEntityNode.objects.select_related("config_version").get(id=node_id)
            except UserDefinedModelEntityNode.DoesNotExist:
                return JsonResponse({"detail": "Node not found"}, status=404)
            if target.get_root().pk != entity.pk:
                return JsonResponse({"detail": "Node does not belong to this entity"}, status=400)
        for fd in target.config_version.field_definitions.filter(
            data_type=FieldDefinition.DataType.WORKFLOW
        ).select_related("workflow_version"):
            t = WorkflowTransition.objects.filter(
                version=fd.workflow_version, name=transition
            ).select_related("from_state", "to_state", "version").first()
            if t:
                kwargs.update(
                    transition=transition, field=fd.slug, node_id=str(target.id),
                    transition_descriptor=t.to_descriptor(),
                )
                break
    if action in ("save", "transition", "preview"):
        from userdefinedmodel.engine import build_entity_document, evaluate_view_precheck
        kwargs.setdefault("old_entity_doc", build_entity_document(entity))
        # Mirror the real save/transition/preview flow: run the VIEW pre-check
        # on the persisted state and hand its carry-over to the evaluation as
        # input.additional_result. Unlike the real flow, a view denial does not
        # abort here — the evaluator should still show the policy's verdict.
        _, additional_result = evaluate_view_precheck(
            entity, eval_user, kwargs["old_entity_doc"], locale=_locale(request))
        kwargs["additional_result"] = additional_result
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
            import opa_bindings
            from userdefinedmodel.engine import RegoSession
            session = RegoSession([
                (f"policy_{entry['slug']}.rego", entry["source"]) for entry in policy_entries
            ])
            eng = session._engine

            try:
                result_val = eng.eval_document("udm.result", input_doc, coverage=True)
            except opa_bindings.OpaUndefinedError:
                result_val = None
            except Exception as exc:
                eval_rule_errors.append(f"data.udm.result: {exc}")
                result_val = None
            if isinstance(result_val, dict):
                output = result_val
            else:
                eval_rule_errors.append("data.udm.result: undefined or not an object (deny)")

            # Capture this eval's prints/coverage before the full-document eval
            # below resets them (each eval_document call overwrites last_*).
            eval_prints = [f"{location}: {message}" for message, location in eng.last_prints]
            coverage = eng.last_coverage
            eval_coverage = [
                {"path": path, **file_cov}
                for path, file_cov in (coverage or {}).get("files", {}).items()
            ]

            try:
                full_document = eng.eval_document("udm", input_doc)
                logger.debug("policy full document entity=%s action=%s raw=%s", entity_id, action, full_document)
            except opa_bindings.OpaUndefinedError:
                full_document = None
            except Exception as full_exc:
                logger.debug("policy full document error entity=%s action=%s", entity_id, action, exc_info=full_exc)
                eval_rule_errors.append(f"data.udm: {full_exc}")
                full_document = None
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


@router.get("/types/{type_id}/eval-policy/nodes/", response=list[EvalNodeOut], auth=django_auth)
def eval_policy_nodes(request, type_id: uuid.UUID, entity_id: uuid.UUID):
    """The full node tree of an entity for the policy evaluator's node picker,
    with each node's workflow fields and their transition names.

    Deliberately bypasses the entity view policy: evaluator users (policy
    admins / superusers) must be able to target submodel nodes they could not
    browse themselves. Gated like eval-policy itself."""
    if denied := _require_evaluator_access(request):
        return denied

    from userdefinedmodel.models import FieldDefinition, UserDefinedModelEntity, WorkflowTransition

    try:
        entity = UserDefinedModelEntity.objects.select_related("config_version").get(id=entity_id)
    except UserDefinedModelEntity.DoesNotExist:
        return JsonResponse({"detail": "Entity not found"}, status=404)

    def workflow_fields(node) -> list[EvalWorkflowFieldOut]:
        out = []
        for fd in node.config_version.field_definitions.filter(
            data_type=FieldDefinition.DataType.WORKFLOW
        ).select_related("workflow_version"):
            if not fd.workflow_version_id:
                continue
            names = list(
                WorkflowTransition.objects.filter(version=fd.workflow_version)
                .order_by("name").values_list("name", flat=True)
            )
            out.append(EvalWorkflowFieldOut(slug=fd.slug, transitions=names))
        return out

    results: list[EvalNodeOut] = []

    def walk(node, parent_id, parent_field_slug, label):
        results.append(EvalNodeOut(
            id=node.id, parent_id=parent_id, parent_field_slug=parent_field_slug,
            label=label, workflow_fields=workflow_fields(node),
        ))
        by_slug: dict[str, int] = {}
        for child in node.children.select_related("parent_field", "config_version").order_by("created_at"):
            slug = child.parent_field.slug if child.parent_field_id else "unknown"
            idx = by_slug.get(slug, 0)
            by_slug[slug] = idx + 1
            walk(child, node.id, slug, f"{label}.{slug}[{idx}]")

    walk(entity, None, None, "root")
    return results


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
    return HttpResponse(status=204)


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


@router.post("/types/{type_id}/policies/", response={200: PolicyOut, 201: PolicyOut}, auth=django_auth)
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
    _, created = UserDefinedModelTypePolicy.objects.update_or_create(
        user_defined_model_type=udm_type, policy=policy,
        defaults={"sort_order": payload.sort_order},
    )
    return (201 if created else 200), PolicyOut(slug=policy.slug, source=policy.source)


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
    return HttpResponse(status=204)
