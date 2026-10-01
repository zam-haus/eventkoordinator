"""
Helper functions for API operations.

This module contains utility functions for converting models to schemas,
parsing dates, and other common operations used across routers.
"""

import logging
import re
from datetime import datetime
from typing import Optional

from django.core.exceptions import ValidationError
from apiv1.models import (
    Series as SeriesModel,
    Event as EventModel,
    Proposal as ProposalModel,
)
from apiv1.schemas import Series, SeriesListItem, Event, ProposalSummary

logger = logging.getLogger(__name__)


def model_series_list_item_to_schema(series_model: SeriesModel) -> SeriesListItem:
    """Convert a Series model to list-item schema without events."""
    return SeriesListItem(
        id=series_model.id,
        name=series_model.name,
        description=series_model.description,
    )


def model_series_to_schema(series_model: SeriesModel) -> Series:
    """Convert a Series model to API schema"""
    return Series(
        id=series_model.id,
        name=series_model.name,
        description=series_model.description,
        events=[
            model_event_to_schema(event)
            for event in series_model.events.all().order_by("start_time")
        ],
    )


def model_event_to_schema(event_model: EventModel) -> Event:
    """Convert an Event model to API schema"""
    return Event(
        id=event_model.id,
        name=event_model.name,
        startTime=event_model.start_time.isoformat(),
        endTime=event_model.end_time.isoformat(),
        tag=event_model.tag,
        useFullDays=event_model.use_full_days,
        proposal_id=event_model.proposal_id,
        series_id=event_model.series_id,
        series_name=event_model.series.name if event_model.series else None,
        status=event_model.status,
    )


def model_proposal_to_schema(proposal_model: ProposalModel) -> ProposalSummary:
    submission_type_label = (
        proposal_model.submission_type.label if proposal_model.submission_type else ""
    )
    return ProposalSummary(
        id=proposal_model.id,
        title=proposal_model.title,
        submission_type=submission_type_label,
        call_id=proposal_model.call_id,
    )


def parse_icalendar_date(date_str: str) -> datetime:
    """Parse iCalendar date format"""
    if "T" in date_str:
        date_part, time_part = date_str.split("T")
        year = int(date_part[0:4])
        month = int(date_part[4:6])
        day = int(date_part[6:8])
        hours = int(time_part[0:2])
        minutes = int(time_part[2:4])
        seconds = int(time_part[4:6])
        return datetime(year, month, day, hours, minutes, seconds)
    else:
        year = int(date_str[0:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        return datetime(year, month, day, 0, 0, 0)


def extract_tag(categories: Optional[str]) -> Optional[str]:
    """Extract the first tag from categories"""
    if not categories:
        return None
    tags = [tag.strip() for tag in categories.split(",")]
    return next((tag for tag in tags if tag), None)


def unescape_ical_text(text: Optional[str]) -> Optional[str]:
    """Unescape iCal text by replacing escape sequences using regex"""
    if not text:
        return text

    # Use regex to find all escape sequences and replace them in one pass
    # This correctly handles \\n (literal \n) vs \n (newline)
    def replace_escape(match):
        escaped_char = match.group(1)
        if escaped_char == "n":
            return "\n"
        elif escaped_char == ",":
            return ","
        elif escaped_char == ";":
            return ";"
        elif escaped_char == "\\":
            return "\\"
        else:
            # Unknown escape sequence, keep as-is
            return match.group(0)

    # Match backslash followed by any character
    result = re.sub(r"\\(.)", replace_escape, text)
    return result


def maybe_create_calculated_prices(event_model: EventModel) -> Optional[object]:
    """Create calculated prices for a newly created event if its submission type asks for it.

    Returns the created ``CalculatedPrices`` instance, or ``None`` when the event has no
    proposal, the submission type does not opt in, or prices already exist.
    """
    from sync_pretix.models import CalculatedPrices

    proposal = event_model.proposal
    if proposal is None or proposal.submission_type is None:
        return None
    if not proposal.submission_type.auto_calculate_prices:
        return None
    if CalculatedPrices.objects.filter(event=event_model).exists():
        return None

    instance = CalculatedPrices(event=event_model)
    try:
        instance.save()
    except ValidationError:
        logger.getChild("maybe_create_calculated_prices").warning(
            "Could not auto-create calculated prices for event %s", event_model.pk,
            exc_info=True,
        )
        return None
    return instance
