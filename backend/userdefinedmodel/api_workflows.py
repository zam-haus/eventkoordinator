"""Workflow routes: /workflows/..."""
from __future__ import annotations

import uuid

from django.db import transaction
from django.http import JsonResponse
from ninja import Router
from ninja.security import django_auth

from userdefinedmodel.api_helpers import _require_perms, _serialize_workflow
from userdefinedmodel.schemas import WorkflowCreateIn, WorkflowDefinitionOut, WorkflowUpdateIn

router = Router(auth=django_auth)


# ─── Workflow CRUD ────────────────────────────────────────────────────────────

def _get_workflow_display_version(wf_def):
    """Return the draft version if it exists, otherwise the published version."""
    return (
        wf_def.versions.filter(status="draft").first()
        or wf_def.versions.filter(status="published").first()
    )


@router.get("/workflows/", response=list[WorkflowDefinitionOut], auth=django_auth)
def list_workflows(request):
    from userdefinedmodel.models import WorkflowDefinition
    if denied := _require_perms(request, "userdefinedmodel.view_fielddefinition"):
        return denied
    workflows = WorkflowDefinition.objects.prefetch_related(
        "versions",
        "versions__states__translations",
        "versions__transitions__translations",
        "versions__transitions__from_state",
        "versions__transitions__to_state",
    ).all()
    result = []
    for wf_def in workflows:
        version = _get_workflow_display_version(wf_def)
        if version:
            result.append(_serialize_workflow(wf_def, version))
    return result


@router.post("/workflows/", response={201: WorkflowDefinitionOut}, auth=django_auth)
def create_workflow(request, payload: WorkflowCreateIn):
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    if denied := _require_perms(request, "userdefinedmodel.add_fielddefinition"):
        return denied
    with transaction.atomic():
        wf_def = WorkflowDefinition.objects.create(
            name=payload.name, description=payload.description,
        )
        version = WorkflowVersion.objects.create(
            workflow=wf_def,
            status=WorkflowVersion.Status.DRAFT,
            virtual_node_positions=payload.virtual_node_positions,
        )
        state_map = {}
        for state_in in payload.states:
            state = WorkflowState.objects.create(
                version=version, name=state_in.name,
                is_initial=state_in.is_initial,
                position_x=state_in.position_x, position_y=state_in.position_y,
                background_color=state_in.background_color,
            )
            state_map[state_in.name] = state
            for lang, label in state_in.label.items():
                WorkflowStateTranslation.objects.create(state=state, language=lang, label=label)
        for trans_in in payload.transitions:
            trans = WorkflowTransition.objects.create(
                version=version,
                name=trans_in.name,
                from_state=state_map.get(trans_in.from_state) if trans_in.from_state else None,
                to_state=state_map[trans_in.to_state],
                from_undefined_only=trans_in.from_undefined_only,
                source_handle=trans_in.source_handle,
                target_handle=trans_in.target_handle,
                properties=trans_in.properties,
            )
            for lang, label in trans_in.label.items():
                WorkflowTransitionTranslation.objects.create(transition=trans, language=lang, label=label)
    return 201, _serialize_workflow(wf_def, version)


@router.get("/workflows/{workflow_id}/", response=WorkflowDefinitionOut, auth=django_auth)
def get_workflow(request, workflow_id: uuid.UUID):
    from userdefinedmodel.models import WorkflowDefinition
    if denied := _require_perms(request, "userdefinedmodel.view_fielddefinition"):
        return denied
    try:
        wf_def = WorkflowDefinition.objects.prefetch_related(
            "versions",
            "versions__states__translations",
            "versions__transitions__translations",
            "versions__transitions__from_state",
            "versions__transitions__to_state",
        ).get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    version = _get_workflow_display_version(wf_def)
    if not version:
        return JsonResponse({"detail": "Workflow has no versions"}, status=404)
    return _serialize_workflow(wf_def, version)


@router.put("/workflows/{workflow_id}/", response=WorkflowDefinitionOut, auth=django_auth)
def update_workflow(request, workflow_id: uuid.UUID, payload: WorkflowUpdateIn):
    from userdefinedmodel.models import (
        WorkflowDefinition, WorkflowVersion, WorkflowState, WorkflowStateTranslation,
        WorkflowTransition, WorkflowTransitionTranslation,
    )
    if denied := _require_perms(request, "userdefinedmodel.change_fielddefinition"):
        return denied
    try:
        wf_def = WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    with transaction.atomic():
        if payload.name is not None:
            wf_def.name = payload.name
        if payload.description is not None:
            wf_def.description = payload.description
        wf_def.save()

        # Get or create the draft version
        draft = WorkflowVersion.objects.filter(workflow=wf_def, status=WorkflowVersion.Status.DRAFT).first()
        if draft is None:
            # Create a new draft as a copy of the published version
            published = WorkflowVersion.objects.filter(workflow=wf_def, status=WorkflowVersion.Status.PUBLISHED).first()
            if published:
                draft = published._create_draft_copy()
            else:
                draft = WorkflowVersion.objects.create(
                    workflow=wf_def, status=WorkflowVersion.Status.DRAFT,
                )

        draft.virtual_node_positions = payload.virtual_node_positions
        draft.save()

        if payload.states is not None:
            if sum(1 for s in payload.states if s.is_initial) != 1:
                return JsonResponse({"detail": "exactly one state must have is_initial=True"}, status=400)

            incoming_names = {s.name for s in payload.states}
            existing_states = {s.name: s for s in draft.states.all()}

            # Apply renames first
            for state_in in payload.states:
                prev = state_in.previous_name
                if prev and prev != state_in.name and prev in existing_states:
                    if state_in.name in existing_states:
                        return JsonResponse(
                            {"detail": f"State name '{state_in.name}' is already in use"},
                            status=400,
                        )
                    old_state = existing_states.pop(prev)
                    old_state.name = state_in.name
                    old_state.save()
                    existing_states[state_in.name] = old_state

            states_to_delete = {n: s for n, s in existing_states.items() if n not in incoming_names}

            draft.states.filter(name__in=incoming_names).update(is_initial=False)

            state_map = {}
            for state_in in payload.states:
                if state_in.name in existing_states:
                    state = existing_states[state_in.name]
                    state.is_initial = state_in.is_initial
                    state.position_x = state_in.position_x
                    state.position_y = state_in.position_y
                    state.background_color = state_in.background_color
                    state.save()
                    state.translations.all().delete()
                else:
                    state = WorkflowState.objects.create(
                        version=draft, name=state_in.name,
                        is_initial=state_in.is_initial,
                        position_x=state_in.position_x, position_y=state_in.position_y,
                        background_color=state_in.background_color,
                    )
                for lang, label in state_in.label.items():
                    WorkflowStateTranslation.objects.create(state=state, language=lang, label=label)
                state_map[state_in.name] = state

            for del_state in states_to_delete.values():
                del_state.delete()

            if payload.transitions is not None:
                draft.transitions.all().delete()
                for trans_in in payload.transitions:
                    trans = WorkflowTransition.objects.create(
                        version=draft,
                        name=trans_in.name,
                        from_state=state_map.get(trans_in.from_state) if trans_in.from_state else None,
                        to_state=state_map[trans_in.to_state],
                        from_undefined_only=trans_in.from_undefined_only,
                        source_handle=trans_in.source_handle,
                        target_handle=trans_in.target_handle,
                        properties=trans_in.properties,
                    )
                    for lang, label in trans_in.label.items():
                        WorkflowTransitionTranslation.objects.create(transition=trans, language=lang, label=label)
    return _serialize_workflow(wf_def, draft)


@router.post("/workflows/{workflow_id}/versions/draft/publish/", response=WorkflowDefinitionOut, auth=django_auth)
def publish_workflow_draft(request, workflow_id: uuid.UUID):
    from userdefinedmodel.models import WorkflowDefinition, WorkflowVersion
    if denied := _require_perms(request, "userdefinedmodel.change_fielddefinition"):
        return denied
    try:
        wf_def = WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    try:
        draft = WorkflowVersion.objects.get(workflow=wf_def, status=WorkflowVersion.Status.DRAFT)
    except WorkflowVersion.DoesNotExist:
        return JsonResponse({"detail": "No draft to publish"}, status=404)
    new_draft = draft.publish()
    return _serialize_workflow(wf_def, new_draft)


@router.delete("/workflows/{workflow_id}/", auth=django_auth)
def delete_workflow(request, workflow_id: uuid.UUID):
    from userdefinedmodel.models import WorkflowDefinition
    if denied := _require_perms(request, "userdefinedmodel.delete_fielddefinition"):
        return denied
    try:
        wf = WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)
    try:
        wf.delete()
    except Exception:
        return JsonResponse({"detail": "Workflow is in use and cannot be deleted"}, status=409)
    return JsonResponse({}, status=204)


@router.get("/workflows/{workflow_id}/state-counts/", auth=django_auth)
def workflow_state_counts(request, workflow_id: uuid.UUID):
    """Return a dict of state_name → entity count for fields using this workflow."""
    from userdefinedmodel.models import WorkflowDefinition
    from userdefinedmodel.models.node import FieldValue
    from django.db.models import Count

    if denied := _require_perms(request, "userdefinedmodel.view_fielddefinition"):
        return denied
    try:
        WorkflowDefinition.objects.get(id=workflow_id)
    except WorkflowDefinition.DoesNotExist:
        return JsonResponse({"detail": "Not found"}, status=404)

    rows = (
        FieldValue.objects
        .filter(field__workflow_version__workflow_id=workflow_id, value_workflow_state__isnull=False)
        .values("value_workflow_state__name")
        .annotate(count=Count("id"))
    )
    result = {row["value_workflow_state__name"]: row["count"] for row in rows}
    return JsonResponse(result)
