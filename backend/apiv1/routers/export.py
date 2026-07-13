"""
Export router.

Provides a single endpoint that generates an Excel workbook with three sheets:
proposals, events, and event prices — scoped to what the current user can access.
"""

import io
import os
import zipfile
from datetime import datetime

import polars as pl
import xlsxwriter
from django.http import HttpResponse
from ninja import Router

import apiv1
import sync_pretix
from apiv1.api_utils import api_permission_required
from apiv1.models import Event as EventModel
from apiv1.models import Proposal as ProposalModel
from apiv1.schemas import ErrorOut
from sync_pretix.models import CalculatedPrices

router = Router()

TABLE_STYLE = "Table Style Medium 9"


def _decimal_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _isoformat_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


@router.get(
    "/excel",
    response={200: bytes, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_required((apiv1, "browse", ProposalModel))
def export_excel(request):
    """Download an Excel workbook with proposals, events, and event prices."""

    # ── proposals ──────────────────────────────────────────────────────────────
    proposals_qs = (
        ProposalModel.objects.select_related("submission_type", "area", "language", "owner", "call")
        .prefetch_related("speakers", "events")
        .order_by("title")
    )
    accessible_proposals = [
        p for p in proposals_qs
        if request.user.has_perm((apiv1, "view", ProposalModel), p)
    ]
    accessible_proposal_ids = {p.id for p in accessible_proposals}

    proposals_rows = []
    for p in accessible_proposals:
        proposals_rows.append({
            "id": str(p.id),
            "title": p.title,
            "status": p.status,
            "submission_type": p.submission_type.code if p.submission_type else None,
            "area": p.area.code if p.area else None,
            "language": p.language.code if p.language else None,
            "owner": p.owner.username if p.owner else None,
            "call": p.call.title if p.call else None,
            "description": p.description,
            "abstract": p.abstract,
            "occurrence_count": p.occurrence_count,
            "duration_days": p.duration_days,
            "duration_time_per_day": p.duration_time_per_day,
            "is_basic_course": p.is_basic_course,
            "max_participants": p.max_participants,
            "material_cost_eur": _decimal_or_none(p.material_cost_eur),
            "has_building_access": p.has_building_access,
            "speakers": ", ".join(s.display_name for s in sorted(p.speakers.all(), key=lambda s: s.sort_order)),
        })

    df_proposals = pl.DataFrame(
        proposals_rows or {
            "id": pl.Series([], dtype=pl.String),
            "title": pl.Series([], dtype=pl.String),
            "status": pl.Series([], dtype=pl.String),
            "submission_type": pl.Series([], dtype=pl.String),
            "area": pl.Series([], dtype=pl.String),
            "language": pl.Series([], dtype=pl.String),
            "owner": pl.Series([], dtype=pl.String),
            "call": pl.Series([], dtype=pl.String),
            "description": pl.Series([], dtype=pl.String),
            "abstract": pl.Series([], dtype=pl.String),
            "occurrence_count": pl.Series([], dtype=pl.Int32),
            "duration_days": pl.Series([], dtype=pl.Int32),
            "duration_time_per_day": pl.Series([], dtype=pl.String),
            "is_basic_course": pl.Series([], dtype=pl.Boolean),
            "max_participants": pl.Series([], dtype=pl.Int32),
            "material_cost_eur": pl.Series([], dtype=pl.Float64),
            "has_building_access": pl.Series([], dtype=pl.Boolean),
            "speakers": pl.Series([], dtype=pl.String),
        }
    )

    # ── events ─────────────────────────────────────────────────────────────────
    events_qs = (
        EventModel.objects.filter(proposal_id__in=accessible_proposal_ids)
        .select_related("series", "proposal")
        .order_by("start_time")
    )
    accessible_events = [
        e for e in events_qs
        if request.user.has_perm((apiv1, "view", EventModel), e)
    ]
    accessible_event_ids = {e.id for e in accessible_events}

    events_rows = []
    for e in accessible_events:
        events_rows.append({
            "id": str(e.id),
            "name": e.name,
            "status": e.status,
            "series_id": str(e.series_id),
            "series_name": e.series.name,
            "proposal_id": str(e.proposal_id) if e.proposal_id else None,
            "proposal_title": e.proposal.title if e.proposal else None,
            "start_time": _isoformat_or_none(e.start_time),
            "end_time": _isoformat_or_none(e.end_time),
            "tag": e.tag,
            "use_full_days": e.use_full_days,
        })

    df_events = pl.DataFrame(
        events_rows or {
            "id": pl.Series([], dtype=pl.String),
            "name": pl.Series([], dtype=pl.String),
            "status": pl.Series([], dtype=pl.String),
            "series_id": pl.Series([], dtype=pl.String),
            "series_name": pl.Series([], dtype=pl.String),
            "proposal_id": pl.Series([], dtype=pl.String),
            "proposal_title": pl.Series([], dtype=pl.String),
            "start_time": pl.Series([], dtype=pl.String),
            "end_time": pl.Series([], dtype=pl.String),
            "tag": pl.Series([], dtype=pl.String),
            "use_full_days": pl.Series([], dtype=pl.Boolean),
        }
    )

    # ── event prices ───────────────────────────────────────────────────────────
    prices_qs = (
        CalculatedPrices.objects.filter(event_id__in=accessible_event_ids)
        .select_related("event", "event__proposal", "pricing_configuration")
    )

    prices_rows = []
    for cp in prices_qs:
        if not request.user.has_perm((sync_pretix, "view", CalculatedPrices), cp):
            continue
        prices_rows.append({
            "event_id": str(cp.event_id),
            "event_name": cp.event.name,
            "proposal_title": cp.event.proposal.title if cp.event.proposal else None,
            "pricing_configuration_id": str(cp.pricing_configuration_id) if cp.pricing_configuration_id else None,
            "member_regular_gross_eur": _decimal_or_none(cp.member_regular_gross_eur),
            "member_discounted_gross_eur": _decimal_or_none(cp.member_discounted_gross_eur),
            "guest_regular_gross_eur": _decimal_or_none(cp.guest_regular_gross_eur),
            "guest_discounted_gross_eur": _decimal_or_none(cp.guest_discounted_gross_eur),
            "business_net_eur": _decimal_or_none(cp.business_net_eur),
            "internal_training_eur": _decimal_or_none(cp.internal_training_eur),
        })

    df_prices = pl.DataFrame(
        prices_rows or {
            "event_id": pl.Series([], dtype=pl.String),
            "event_name": pl.Series([], dtype=pl.String),
            "proposal_title": pl.Series([], dtype=pl.String),
            "pricing_configuration_id": pl.Series([], dtype=pl.String),
            "member_regular_gross_eur": pl.Series([], dtype=pl.Float64),
            "member_discounted_gross_eur": pl.Series([], dtype=pl.Float64),
            "guest_regular_gross_eur": pl.Series([], dtype=pl.Float64),
            "guest_discounted_gross_eur": pl.Series([], dtype=pl.Float64),
            "business_net_eur": pl.Series([], dtype=pl.Float64),
            "internal_training_eur": pl.Series([], dtype=pl.Float64),
        }
    )

    # ── write workbook ─────────────────────────────────────────────────────────
    buf = io.BytesIO()
    with xlsxwriter.Workbook(buf, {"strings_to_formulas": False}) as workbook:
        df_proposals.write_excel(
            workbook=workbook,
            worksheet="Proposals",
            table_name="Proposals",
            table_style=TABLE_STYLE,
        )
        df_events.write_excel(
            workbook=workbook,
            worksheet="Events",
            table_name="Events",
            table_style=TABLE_STYLE,
        )
        df_prices.write_excel(
            workbook=workbook,
            worksheet="EventPrices",
            table_name="EventPrices",
            table_style=TABLE_STYLE,
        )

    buf.seek(0)
    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="export.xlsx"'
    return response


@router.get(
    "/images",
    response={200: bytes, 401: ErrorOut, 403: ErrorOut},
)
@api_permission_required((apiv1, "browse", ProposalModel))
def export_images(request):
    """Download a ZIP archive of proposal photos, named by proposal ID."""
    proposals_qs = (
        ProposalModel.objects.select_related("owner")
        .exclude(photo="")
        .order_by("title")
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in proposals_qs:
            if not request.user.has_perm((apiv1, "view", ProposalModel), p):
                continue
            if not p.photo:
                continue
            try:
                ext = os.path.splitext(p.photo.name)[1].lower()
                arcname = f"{p.id}{ext}"
                with p.photo.open("rb") as fh:
                    zf.writestr(arcname, fh.read())
            except (OSError, FileNotFoundError):
                continue

    buf.seek(0)
    response = HttpResponse(buf.read(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="proposal_images.zip"'
    return response
