"""
Proposals router.

Handles endpoints for managing proposals, associations, and validation.
"""

import inspect
import logging
import uuid
from datetime import timedelta
from typing import cast

import pydot
from django.core.exceptions import ValidationError
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from ninja import File, Router, UploadedFile
from openid_user_management.models import OpenIDUser
from viewflow.fsm import chart
from viewflow.fsm.base import TransitionMethod

import apiv1
from apiv1.api_utils import (
    api_permission_mandatory,
    api_permission_required,
)
from django.contrib.auth.models import Group

from apiv1.flows import ProposalFlow
from apiv1.helpers import model_proposal_to_schema
from apiv1.models import check_proposal_required_fields
from apiv1.models import Proposal as ProposalModel, Speaker
from apiv1.models.basedata import ProposalReview
from apiv1.models import Event as EventModel
from apiv1.models import Call as CallModel
from apiv1.models import ProposalArea, ProposalLanguage, SubmissionType
from apiv1.schemas import (
    ErrorOut,
    EventFlowDiagram,
    FlowEdge,
    ProposalCreateIn,
    ProposalDetail,
    ProposalEventSummary,
    ProposalHistory,
    ProposalHistoryEntry,
    ProposalListItem,
    ProposalSummary,
    ProposalTransitionOut,
    ProposalTransitions,
    ProposalUpdateIn,
    ReviewStats,
    UserBasic,
)


router = Router()
logger = logging.getLogger(__name__)



def _proposal_to_detail_schema(proposal: ProposalModel) -> ProposalDetail:
    submission_type_code = (
        proposal.submission_type.code if proposal.submission_type else ""
    )
    area_code = proposal.area.code if proposal.area else None
    language_code = proposal.language.code if proposal.language else None

    owner = None
    if proposal.owner:
        owner = UserBasic(id=proposal.owner.pk, username=proposal.owner.username)

    editors = [UserBasic(id=p.pk, username=p.username) for p in proposal.editors.all()]

    return ProposalDetail(
        id=proposal.id,
        title=proposal.title,
        submission_type=submission_type_code,
        area=area_code,
        language=language_code,
        abstract=proposal.abstract,
        description=proposal.description,
        internal_notes=proposal.internal_notes,
        occurrence_count=proposal.occurrence_count,
        duration_days=proposal.duration_days,
        duration_time_per_day=proposal.duration_time_per_day,
        is_basic_course=proposal.is_basic_course,
        max_participants=proposal.max_participants,
        material_cost_eur=str(proposal.material_cost_eur),
        preferred_dates=proposal.preferred_dates,
        has_building_access=proposal.has_building_access,
        photo=proposal.photo.url if proposal.photo else None,
        owner=owner,
        editors=editors,
        moderation_comment=proposal.moderation_comment,
        call_id=proposal.call_id,
    )


@router.get("/flow-chart", response={200: bytes, 401: ErrorOut, 403: ErrorOut})
def flow_chart_image(request):
    dot_graph = chart(ProposalFlow.status)

    graphs = pydot.graph_from_dot_data(dot_graph)
    graph = graphs[0]
    png_data = graph.create(format="svg")

    return HttpResponse(png_data, content_type="image/svg+xml")


@router.get("/flow-diagram", response={200: EventFlowDiagram})
def proposal_flow_diagram(request):
    """Return the proposal FSM structure as a Pydantic model for frontend diagram rendering."""
    nodes: set[str] = set()
    edges: list[FlowEdge] = []
    for action, method in (
        (name, obj)
        for name, obj in inspect.getmembers(ProposalFlow)
        if isinstance(obj, TransitionMethod)
    ):
        for transition in method.get_transitions():
            sources = transition.source if isinstance(transition.source, list) else [transition.source]
            target = transition.target.value
            for source in sources:
                source_val = source.value
                nodes.add(source_val)
                nodes.add(target)
                edges.append(FlowEdge(source=source_val, target=target, label_id=action))
    return EventFlowDiagram(nodes=sorted(nodes), edges=edges)


@router.get(
    "/search", response={200: list[ProposalSummary], 401: ErrorOut, 403: ErrorOut}
)
@api_permission_required((apiv1, "browse", ProposalModel))
def search_proposals(request, q: str = ""):
    """Search proposals by title for autocomplete dropdowns."""
    proposals = (
        ProposalModel.objects.select_related("submission_type")
        .filter(title__icontains=q)
        .order_by("title")
    )
    return [
        model_proposal_to_schema(p)
        for p in proposals
        if request.user.has_perm((apiv1, "view", ProposalModel), p)
    ]


@router.post(
    "/create",
    response={201: ProposalSummary, 400: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_required((apiv1, "add", ProposalModel))
def create_proposal(
    request, payload: ProposalCreateIn
) -> tuple[int, ProposalSummary] | tuple[int, ErrorOut]:
    """Create a new proposal with sensible defaults"""
    try:
        # Get submission type if provided, otherwise leave as None
        submission_type = None
        if payload.submission_type:
            try:
                submission_type = SubmissionType.objects.get(
                    code=payload.submission_type
                )
            except SubmissionType.DoesNotExist:
                return 400, ErrorOut(code="proposals.invalidSubmissionType")

        # Use defaults for all optional fields
        title = payload.title or ""
        # Keep text fields empty by default; frontend shows guidance instead
        abstract = payload.abstract or ""
        description = payload.description or ""
        duration_days = (
            payload.duration_days if payload.duration_days is not None else 1
        )
        duration_time_per_day = (
            payload.duration_time_per_day
            if payload.duration_time_per_day is not None
            else "00:00"
        )
        max_participants = (
            payload.max_participants if payload.max_participants is not None else 0
        )
        occurrence_count = (
            payload.occurrence_count if payload.occurrence_count is not None else 1
        )
        material_cost_eur = (
            payload.material_cost_eur
            if payload.material_cost_eur is not None
            else "0.00"
        )
        preferred_dates = payload.preferred_dates or ""
        is_basic_course = (
            payload.is_basic_course if payload.is_basic_course is not None else False
        )
        has_building_access = (
            payload.has_building_access
            if payload.has_building_access is not None
            else False
        )

        call = None
        if payload.call_id:
            try:
                call = CallModel.objects.get(pk=payload.call_id)
            except CallModel.DoesNotExist:
                return 400, ErrorOut(code="proposals.callNotFound")

        # Create proposal with owner set to authenticated user
        proposal = ProposalModel.objects.create(
            title=title,
            submission_type=submission_type,
            area_id=payload.area,
            language_id=payload.language,
            abstract=abstract,
            description=description,
            internal_notes=payload.internal_notes or "",
            occurrence_count=occurrence_count,
            duration_days=duration_days,
            duration_time_per_day=duration_time_per_day,
            is_basic_course=is_basic_course,
            max_participants=max_participants,
            material_cost_eur=material_cost_eur,
            preferred_dates=preferred_dates,
            has_building_access=has_building_access,
            call=call,
            owner=request.user if request.user.is_authenticated else None,
        )

        # Create default speaker entry for the proposal owner
        if request.user.is_authenticated:
            Speaker.objects.create(
                proposal=proposal,
                email=request.user.email or "",
                display_name=request.user.get_full_name() or request.user.username or "",
                biography="",
                role=Speaker.Role.PRIMARY,
                sort_order=0,
            )

        try:
            proposal.clean()  # Validate duration
        except Exception as e:
            logger.error(f"Proposal validation failed: {str(e)}")
            return 400, ErrorOut(code="proposals.validationError")

        return 201, model_proposal_to_schema(proposal)
    except Exception as e:
        logger.error(f"Failed to create proposal: {str(e)}")
        return 400, ErrorOut(code="proposals.createFailed")


_ACCEPTED_EVENT_STATUSES = {
    EventModel.Status.CONFIRMED,
    EventModel.Status.PUBLISHED,
    EventModel.Status.COMPLETED,
}


def _derive_group_status(group_code: str, reviews: list) -> str:
    """Derive a group review's effective status from its member votes."""
    member_reviews = [
        r for r in reviews
        if r.kind == ProposalReview.KIND_USER and group_code in (r.requested_via_groups or [])
    ]
    if any(r.status == "rejected" for r in member_reviews):
        return "rejected"
    if any(r.status == "revise" for r in member_reviews):
        return "revise"
    if any(r.status == "approved" for r in member_reviews):
        return "approved"
    return "pending"


def _compute_review_stats(reviews: list, current_user_id) -> tuple[ReviewStats, str | None]:
    """Compute aggregate review stats and the current user's review status."""
    approved = revise = rejected = pending = 0
    my_status: str | None = None

    # Determine current user's review status first (before aggregation filtering)
    for r in reviews:
        if r.kind == ProposalReview.KIND_USER and r.reviewer_id == current_user_id:
            if r.status == "note":
                continue
            if r.status == "pending" and not r.requested_directly and (r.requested_via_groups or []):
                # Not yet voted, only requested via group — treat as "requested"
                my_status = "requested"
            elif r.status == "pending":
                my_status = "requested"
            else:
                my_status = r.status
            break

    for r in reviews:
        if r.kind == ProposalReview.KIND_USER and r.status == "note":
            continue
        if (
            r.kind == ProposalReview.KIND_USER
            and not r.requested_directly
            and (r.requested_via_groups or [])
        ):
            # Counted via their group entry instead
            continue
        s = _derive_group_status(r.group_code, reviews) if r.kind == ProposalReview.KIND_GROUP else r.status
        if s == "approved":
            approved += 1
        elif s == "revise":
            revise += 1
        elif s == "rejected":
            rejected += 1
        else:
            pending += 1

    total = approved + revise + rejected + pending
    return ReviewStats(approved=approved, revise=revise, rejected=rejected, pending=pending, total=total), my_status


@router.get(
    "/",
    response={200: list[ProposalListItem], 401: ErrorOut, 403: ErrorOut},
)
@api_permission_required((apiv1, "browse", ProposalModel))
def list_proposals(request) -> list[ProposalListItem]:
    """List all proposals visible to the current user."""
    proposals_qs = (
        ProposalModel.objects.select_related("submission_type", "owner", "call")
        .prefetch_related("speakers", "events", "reviews__reviewer")
        .order_by("title")
    )
    result = []
    for p in proposals_qs:
        if not request.user.has_perm((apiv1, "view", ProposalModel), p):
            continue
        accepted = sum(1 for e in p.events.all() if e.status in _ACCEPTED_EVENT_STATUSES)
        reviews = list(p.reviews.all())
        review_stats, my_review_status = _compute_review_stats(reviews, request.user.pk)
        result.append(
            ProposalListItem(
                id=p.id,
                title=p.title,
                status=p.status,
                submission_type=p.submission_type.code if p.submission_type else None,
                owner=UserBasic(id=p.owner.pk, username=p.owner.username) if p.owner else None,
                speakers=[s.display_name for s in sorted(p.speakers.all(), key=lambda s: s.sort_order)],
                occurrence_count=p.occurrence_count,
                accepted_event_count=accepted,
                call_title=p.call.title if p.call else None,
                review_stats=review_stats,
                my_review_status=my_review_status,
            )
        )
    return result


@router.get(
    "/{proposal_id}",
    response={200: ProposalDetail, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def get_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail] | tuple[int, ErrorOut]:
    """Get full proposal details"""
    try:
        proposal = (
            ProposalModel.objects.select_related(
                "submission_type", "area", "language", "owner"
            )
            .prefetch_related("editors")
            .get(pk=proposal_id)
        )
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    if not request.user.has_perm((apiv1, "view", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    return 200, _proposal_to_detail_schema(proposal)


@router.post(
    "/{proposal_id}/photo",
    response={200: ProposalDetail, 400: ErrorOut, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def upload_proposal_photo(
    request, proposal_id: uuid.UUID, file: UploadedFile = File(...)
) -> tuple[int, ProposalDetail] | tuple[int, ErrorOut]:
    """Upload or replace a proposal image."""
    try:
        proposal = (
            ProposalModel.objects.select_related(
                "submission_type", "area", "language", "owner"
            )
            .prefetch_related("editors")
            .get(pk=proposal_id)
        )
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    if not request.user.has_perm((apiv1, "change", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    photo_field = cast(models.FileField, proposal._meta.get_field("photo"))
    try:
        photo_field.clean(file, proposal)
    except ValidationError as exc:
        return 400, ErrorOut(
            code="proposals.invalidImage",
            detail=" ".join(exc.messages),
        )

    try:
        from PIL import Image as PilImage
        img = PilImage.open(file)
        width, height = img.size
        if width < 1440 or height < 1080:
            return 400, ErrorOut(
                code="proposals.imageTooSmall",
                detail=f"Image must be at least 1440×1080 pixels (landscape), got {width}×{height}.",
            )
    except Exception:
        return 400, ErrorOut(code="proposals.invalidImage", detail="Could not read image dimensions.")
    finally:
        file.seek(0)

    previous_photo_name = proposal.photo.name if proposal.photo else None
    proposal.photo = file
    proposal.save(update_fields=["photo"])

    if previous_photo_name and previous_photo_name != proposal.photo.name:
        photo_field.storage.delete(previous_photo_name)

    return 200, _proposal_to_detail_schema(proposal)


@router.put(
    "/{proposal_id}",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def update_proposal(
    request, proposal_id: uuid.UUID, payload: ProposalUpdateIn
) -> tuple[int, ProposalDetail] | tuple[int, ErrorOut]:
    """Update a proposal"""
    try:
        proposal = ProposalModel.objects.select_related(
            "submission_type", "area", "language"
        ).get(pk=proposal_id)
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")
    if not request.user.has_perm((apiv1, "change", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    # Update fields that were provided
    if payload.title is not None:
        proposal.title = payload.title

    if payload.submission_type is not None:
        try:
            submission_type = SubmissionType.objects.get(code=payload.submission_type)
            proposal.submission_type = submission_type
        except SubmissionType.DoesNotExist:
            return 400, ErrorOut(code="proposals.invalidSubmissionType")

    if payload.area is not None:
        if payload.area:
            try:
                area = ProposalArea.objects.get(code=payload.area)
                proposal.area = area
            except ProposalArea.DoesNotExist:
                return 400, ErrorOut(code="proposals.invalidArea")
        else:
            proposal.area = None

    if payload.language is not None:
        if payload.language:
            try:
                language = ProposalLanguage.objects.get(code=payload.language)
                proposal.language = language
            except ProposalLanguage.DoesNotExist:
                return 400, ErrorOut(code="proposals.invalidLanguage")
        else:
            proposal.language = None

    if payload.abstract is not None:
        proposal.abstract = payload.abstract

    if payload.description is not None:
        proposal.description = payload.description

    if payload.internal_notes is not None:
        proposal.internal_notes = payload.internal_notes

    if payload.occurrence_count is not None:
        proposal.occurrence_count = payload.occurrence_count

    if payload.duration_days is not None:
        proposal.duration_days = payload.duration_days

    if payload.duration_time_per_day is not None:
        proposal.duration_time_per_day = payload.duration_time_per_day

    if payload.is_basic_course is not None:
        proposal.is_basic_course = payload.is_basic_course

    if payload.max_participants is not None:
        proposal.max_participants = payload.max_participants

    if payload.material_cost_eur is not None:
        proposal.material_cost_eur = payload.material_cost_eur

    if payload.preferred_dates is not None:
        proposal.preferred_dates = payload.preferred_dates

    if payload.has_building_access is not None:
        proposal.has_building_access = payload.has_building_access

    # Owner cannot be changed after proposal creation
    # It is automatically set to the creating user and remains immutable

    # Handle editors update
    if payload.editor_ids is not None:
        try:
            # UUIDs are already strings, no conversion needed
            user_ids = [uid for uid in payload.editor_ids if uid]
            editors = OpenIDUser.objects.filter(pk__in=user_ids)
            proposal.editors.set(editors)
        except (ValueError, Exception) as e:
            logger.error(f"Failed to update editors: {str(e)}")
            return 400, ErrorOut(code="proposals.invalidEditorId")

    if payload.moderation_comment is not None:
        if request.user.has_perm((apiv1, "moderate", ProposalModel), proposal):
            proposal.moderation_comment = payload.moderation_comment

    if payload.call_id is not None:
        if str(payload.call_id) != str(proposal.call_id or ''):
            if proposal.status != ProposalModel.Status.DRAFT:
                return 400, ErrorOut(code="proposals.callChangeNotAllowedInStatus")
            try:
                proposal.call = CallModel.objects.get(pk=payload.call_id)
            except CallModel.DoesNotExist:
                return 400, ErrorOut(code="proposals.callNotFound")
    elif 'call_id' in (payload.model_fields_set if hasattr(payload, 'model_fields_set') else set()):
        # Explicitly set to null — only act if there is a change
        if proposal.call_id is not None:
            if proposal.status != ProposalModel.Status.DRAFT:
                return 400, ErrorOut(code="proposals.callChangeNotAllowedInStatus")
            proposal.call = None

    try:
        proposal.clean()  # Validate duration
    except Exception as e:
        logger.error(f"Proposal validation failed: {str(e)}")
        return 400, ErrorOut(code="proposals.validationError")

    proposal.save()

    return 200, _proposal_to_detail_schema(proposal)


@router.delete(
    "/{proposal_id}",
    response={204: None, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def delete_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, None] | tuple[int, ErrorOut]:
    """Delete a proposal when the current user has object-level permission."""
    try:
        proposal = ProposalModel.objects.get(pk=proposal_id)
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    if not request.user.has_perm((apiv1, "delete", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    proposal.delete()
    return 204, None


@router.get(
    "/{proposal_id}/checklist",
    response={200: dict, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def get_proposal_checklist(
    request, proposal_id: uuid.UUID
) -> tuple[int, dict] | tuple[int, ErrorOut]:
    """Get validation checklist for a proposal showing what information is complete"""
    try:
        proposal = ProposalModel.objects.select_related("submission_type").get(
            pk=proposal_id
        )
        if not request.user.has_perm((apiv1, "view", ProposalModel), proposal):
            return 401, ErrorOut(code="auth.permissionDenied")
    except ProposalModel.DoesNotExist:
        if not request.user.has_perm((apiv1, "view", ProposalModel), None):
            return 401, ErrorOut(code="auth.permissionDenied")
        return 404, ErrorOut(code="proposals.notFound")

    checklist = check_proposal_required_fields(proposal)

    return 200, checklist


@router.get(
    "/{proposal_id}/history",
    response={200: ProposalHistory, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def get_proposal_history(
    request, proposal_id: uuid.UUID, days: int = 7
) -> tuple[int, ProposalHistory] | tuple[int, ErrorOut]:
    """Get proposal history changes for the last N days (default 7 days)."""
    try:
        proposal = ProposalModel.objects.get(pk=proposal_id)
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    if not request.user.has_perm((apiv1, "view", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    safe_days = max(1, min(30, days))  # Limit to 1-30 days for safety
    cutoff_date = timezone.now() - timedelta(days=safe_days)

    history_records = (
        proposal.history.filter(history_date__gte=cutoff_date)
        .select_related("history_user")
        .order_by("-history_date", "-history_id")
    )

    change_type_map = {"+": "create", "~": "change", "-": "delete"}
    entries: list[ProposalHistoryEntry] = []

    for record in history_records:
        change_type = change_type_map.get(record.history_type, "change")
        changed_by = record.history_user.username if record.history_user else "Unknown"

        if record.history_type == "~":
            prev_record = getattr(record, "prev_record", None)

            if prev_record is not None:
                diff = record.diff_against(prev_record)
                if diff.changes:
                    for change in diff.changes:
                        old_val = None if change.old is None else str(change.old)
                        new_val = None if change.new is None else str(change.new)
                        entries.append(
                            ProposalHistoryEntry(
                                timestamp=record.history_date.isoformat(),
                                changed_by=changed_by,
                                change_type=change_type,
                                field_name=change.field,
                                old_value=old_val,
                                new_value=new_val,
                                summary=f"Changed proposal field: {change.field}",
                            )
                        )
                    continue

            entries.append(
                ProposalHistoryEntry(
                    timestamp=record.history_date.isoformat(),
                    changed_by=changed_by,
                    change_type=change_type,
                    field_name=None,
                    old_value=None,
                    new_value=None,
                    summary="Proposal updated",
                )
            )
            continue

        summary = (
            "Proposal created" if record.history_type == "+" else "Proposal deleted"
        )
        entries.append(
            ProposalHistoryEntry(
                timestamp=record.history_date.isoformat(),
                changed_by=changed_by,
                change_type=change_type,
                field_name=None,
                old_value=None,
                new_value=None,
                summary=summary,
            )
        )

    return 200, ProposalHistory(proposal_id=proposal_id, entries=entries)


@router.get(
    "/{proposal_id}/transitions",
    response={200: ProposalTransitions, 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def get_proposal_transitions(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalTransitions | ErrorOut]:
    """Get available transitions for a proposal and the current user."""
    try:
        proposal = ProposalModel.objects.get(pk=proposal_id)
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    if not request.user.has_perm((apiv1, "view", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    # Get available transitions from ProposalFlow
    flow = ProposalFlow(proposal)
    transitions = flow.get_available_transitions(request.user)

    transitions_data = [
        ProposalTransitionOut(
            action=t.action,
            label_id=t.label_id,
            target_status=t.target_status,
            enabled=t.enabled,
            disable_reason=t.disable_reason,
        )
        for t in transitions
    ]

    return 200, ProposalTransitions(
        proposal_id=proposal_id,
        current_status=proposal.status,
        transitions=transitions_data,
    )


def _reset_reviews_on_resubmit(proposal: ProposalModel) -> None:
    """Reset all completed user reviews to pending when a proposal is resubmitted."""
    now = timezone.now()
    reviews = ProposalReview.objects.filter(proposal=proposal)
    to_update = []
    for r in reviews:
        if r.reviewer_is_system or r.status == "note":
            continue
        if r.kind == "group":
            r.requested_at = now
            to_update.append(r)
        elif r.status != "pending":
            r.previous_status = r.status
            r.previous_comment = r.comment
            r.status = "pending"
            r.completed_at = None
            r.requested_at = now
            to_update.append(r)
    if to_update:
        ProposalReview.objects.bulk_update(
            to_update,
            ["status", "completed_at", "requested_at", "previous_status", "previous_comment"],
        )


def _execute_proposal_transition(
    request, proposal_id: uuid.UUID, action: str
) -> tuple[int, ProposalDetail | ErrorOut]:
    """
    Common helper method for executing proposal transitions.
    """
    try:
        proposal = ProposalModel.objects.select_related(
            "submission_type", "area", "language"
        ).get(pk=proposal_id)
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    # Check if user can view the proposal
    if not request.user.has_perm((apiv1, "view", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    # Create flow and evaluate transition
    flow = ProposalFlow(proposal)
    transitions = flow.get_available_transitions(request.user)

    # Find the transition
    transition = next((t for t in transitions if t.action == action), None)
    if not transition:
        return 400, ErrorOut(code="proposals.unknownTransition", detail=f"Unknown transition action: {action}")

    # Check if transition is enabled
    if not transition.enabled:
        return 400, ErrorOut(
            code="proposals.transitionNotAllowed", detail=transition.disable_reason
        )

    # For resubmissions, reset all completed reviews to pending
    is_resubmission = action == "submit" and proposal.status != ProposalModel.Status.DRAFT

    # Execute the transition
    try:
        success = flow.execute_transition(action)
        if not success:
            return 400, ErrorOut(code="proposals.transitionFailed", detail=f"Failed to execute {action} transition")
    except Exception as e:
        logger.error(f"Error executing transition {action}: {str(e)}")
        return 400, ErrorOut(code="proposals.transitionFailed")

    if is_resubmission:
        _reset_reviews_on_resubmit(proposal)

    return 200, _proposal_to_detail_schema(proposal)


@router.post(
    "/{proposal_id}/submit",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def submit_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail | ErrorOut]:
    """Submit a proposal (from DRAFT or REVISE status)."""
    return _execute_proposal_transition(request, proposal_id, "submit")


@router.post(
    "/{proposal_id}/accept",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def accept_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail | ErrorOut]:
    """Accept a submitted proposal."""
    return _execute_proposal_transition(request, proposal_id, "accept")


@router.post(
    "/{proposal_id}/reject",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def reject_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail | ErrorOut]:
    """Reject a submitted proposal."""
    return _execute_proposal_transition(request, proposal_id, "reject")


@router.post(
    "/{proposal_id}/revise",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def revise_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail | ErrorOut]:
    """Request revision for a submitted or rejected proposal."""
    return _execute_proposal_transition(request, proposal_id, "revise")


@router.post(
    "/{proposal_id}/submit-on-behalf",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def submit_proposal_on_behalf(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail | ErrorOut]:
    """Submit a proposal on behalf of the author."""
    return _execute_proposal_transition(request, proposal_id, "submit_on_behalf")


@router.post(
    "/{proposal_id}/undo-accept",
    response={
        200: ProposalDetail,
        400: ErrorOut,
        404: ErrorOut,
        401: ErrorOut,
        403: ErrorOut,
    },
)
@api_permission_mandatory()
def undo_accept_proposal(
    request, proposal_id: uuid.UUID
) -> tuple[int, ProposalDetail | ErrorOut]:
    """Undo the acceptance of a proposal."""
    return _execute_proposal_transition(request, proposal_id, "undo_accept")


@router.get(
    "/{proposal_id}/events",
    response={200: list[ProposalEventSummary], 404: ErrorOut, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_mandatory()
def get_proposal_events(
    request, proposal_id: uuid.UUID
) -> tuple[int, list[ProposalEventSummary]] | tuple[int, ErrorOut]:
    """List all events linked to a proposal."""
    try:
        proposal = ProposalModel.objects.get(pk=proposal_id)
    except ProposalModel.DoesNotExist:
        return 404, ErrorOut(code="proposals.notFound")

    if not request.user.has_perm((apiv1, "view", ProposalModel), proposal):
        return 401, ErrorOut(code="auth.permissionDenied")

    events = (
        EventModel.objects.filter(proposal=proposal)
        .select_related("series", "proposal")
        .order_by("start_time")
    )

    return 200, [
        ProposalEventSummary(
            id=event.id,
            name=event.name,
            startTime=event.start_time.isoformat(),
            endTime=event.end_time.isoformat(),
            status=event.status,
            series_id=event.series_id,
            series_name=event.series.name,
        )
        for event in events
        if request.user.has_perm((apiv1, "view", EventModel), event)
    ]

