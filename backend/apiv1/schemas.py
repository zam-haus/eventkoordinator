"""
Centralized schema definitions for the API.

This module contains all Pydantic schema definitions used across the API
to avoid circular imports and serve as a single source of truth for API contracts.
"""

import uuid

from ninja import Schema
from typing import Optional, Literal
from pydantic import Field


class SiteConfigOut(Schema):
    imprint_url: str
    privacy_policy_url: str
    account_management_url: str


class UserIn(Schema):
    username: str
    password: str


class UserBasic(Schema):
    id: uuid.UUID
    username: str


class UserOut(Schema):
    username: str
    user_id: str


class ErrorOut(Schema):
    code: str
    detail: Optional[str] = None


class Event(Schema):
    id: uuid.UUID
    name: str
    startTime: str  # ISO format string
    endTime: str  # ISO format string
    tag: Optional[str] = None
    useFullDays: bool = False
    proposal_id: Optional[uuid.UUID] = None
    series_id: Optional[uuid.UUID] = None
    series_name: Optional[str] = None
    status: str = "draft"


class Series(Schema):
    id: uuid.UUID
    name: str
    description: Optional[str] = None
    events: list[Event]


class SeriesListItem(Schema):
    id: uuid.UUID
    name: str
    description: Optional[str] = None


class CreateSeriesIn(Schema):
    name: Optional[str] = None
    description: Optional[str] = None


class CreateEventIn(Schema):
    name: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    tag: Optional[str] = None
    useFullDays: Optional[bool] = None
    proposal_id: Optional[uuid.UUID] = None


class CreateEventOut(Schema):
    series_id: uuid.UUID
    event: Event


class SyncStatus(Schema):
    target_id: uuid.UUID
    platform: str
    status: Literal["no entry exists", "creation pending", "status unknown", "entry up-to-date", "entry differs"]
    last_synced: Optional[str] = None
    last_error: Optional[str] = None
    can_push: bool = False
    can_delete: bool = False
    item_url: Optional[str] = None


class EventSyncInfo(Schema):
    series_id: uuid.UUID
    event_id: uuid.UUID
    sync_statuses: list[SyncStatus]


class SyncPushResult(Schema):
    success: bool
    message: str
    timestamp: str
    target_id: uuid.UUID
    series_id: uuid.UUID
    event_id: uuid.UUID


class SyncDeleteResult(Schema):
    success: bool
    message: str
    timestamp: str
    target_id: uuid.UUID
    series_id: uuid.UUID
    event_id: uuid.UUID


class EventWithID(Schema):
    id: uuid.UUID
    name: str
    startTime: str
    endTime: str
    tag: Optional[str] = None


class UpdateSeriesIn(Schema):
    name: Optional[str] = None
    description: Optional[str] = None


class UpdateEventIn(Schema):
    name: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    tag: Optional[str] = None
    useFullDays: Optional[bool] = None
    proposal_id: Optional[uuid.UUID] = None
    series_id: Optional[uuid.UUID] = None


class ProposalSummary(Schema):
    id: uuid.UUID
    title: str
    submission_type: str
    call_id: Optional[uuid.UUID] = None


class ReviewStats(Schema):
    approved: int = 0
    revise: int = 0
    rejected: int = 0
    pending: int = 0
    total: int = 0


class ProposalListItem(Schema):
    id: uuid.UUID
    title: str
    status: str
    submission_type: Optional[str] = None
    owner: Optional[UserBasic] = None
    speakers: list[str]
    occurrence_count: int
    accepted_event_count: int
    call_title: Optional[str] = None
    review_stats: ReviewStats = Field(default_factory=ReviewStats)
    my_review_status: Optional[str] = None  # None | 'requested' | 'approved' | 'rejected' | 'revise'


class ProposalChecklistItem(Schema):
    status: Literal["ok", "error"]


class ProposalChecklist(Schema):
    items: dict[str, ProposalChecklistItem]


class ProposalCreateIn(Schema):
    title: Optional[str] = Field(default=None, max_length=30)
    submission_type: Optional[str] = None
    area: Optional[str] = None
    language: Optional[str] = None
    abstract: Optional[str] = None
    description: Optional[str] = None
    internal_notes: Optional[str] = None
    occurrence_count: Optional[int] = None
    duration_days: Optional[int] = None
    duration_time_per_day: Optional[str] = None
    is_basic_course: Optional[bool] = None
    max_participants: Optional[int] = None
    material_cost_eur: Optional[str] = None
    preferred_dates: Optional[str] = None
    has_building_access: Optional[bool] = None
    photo_alt_text: Optional[str] = Field(default=None, max_length=300)
    call_id: Optional[uuid.UUID] = None


class ProposalUpdateIn(Schema):
    title: Optional[str] = Field(default=None, max_length=30)
    submission_type: Optional[str] = None
    area: Optional[str] = None
    language: Optional[str] = None
    abstract: Optional[str] = None
    description: Optional[str] = None
    internal_notes: Optional[str] = None
    occurrence_count: Optional[int] = None
    duration_days: Optional[int] = None
    duration_time_per_day: Optional[str] = None
    is_basic_course: Optional[bool] = None
    max_participants: Optional[int] = None
    material_cost_eur: Optional[str] = None
    preferred_dates: Optional[str] = None
    has_building_access: Optional[bool] = None
    # owner_id removed - owner is set on creation and cannot be changed
    editor_ids: Optional[list[str]] = None
    moderation_comment: Optional[str] = None
    photo_alt_text: Optional[str] = Field(default=None, max_length=300)
    call_id: Optional[uuid.UUID] = None


class CallOut(Schema):
    id: uuid.UUID
    title: str
    description: str
    execution_period_start: str  # ISO date string
    execution_period_end: str    # ISO date string
    submission_deadline: str     # ISO date string
    print_deadline: str          # ISO date string
    responsible_name: str
    responsible_email: str
    is_active: bool


class CallCreateIn(Schema):
    title: str
    description: Optional[str] = ""
    execution_period_start: str
    execution_period_end: str
    submission_deadline: str
    print_deadline: str
    responsible_name: str
    responsible_email: str
    is_active: Optional[bool] = True


class CallUpdateIn(Schema):
    title: Optional[str] = None
    description: Optional[str] = None
    execution_period_start: Optional[str] = None
    execution_period_end: Optional[str] = None
    submission_deadline: Optional[str] = None
    print_deadline: Optional[str] = None
    responsible_name: Optional[str] = None
    responsible_email: Optional[str] = None
    is_active: Optional[bool] = None


class ProposalDetail(Schema):
    id: uuid.UUID
    title: str
    submission_type: str
    area: Optional[str] = None
    language: Optional[str] = None
    abstract: str
    description: str
    internal_notes: str
    occurrence_count: int
    duration_days: int
    duration_time_per_day: str
    is_basic_course: bool
    max_participants: int
    material_cost_eur: str
    preferred_dates: str
    has_building_access: bool
    photo: Optional[str] = None
    photo_alt_text: str = ""
    owner: Optional[UserBasic] = None
    editors: list[UserBasic] = []
    moderation_comment: str = ""
    call_id: Optional[uuid.UUID] = None


class SpeakerOut(Schema):
    id: uuid.UUID
    email: str = ""
    display_name: str
    biography: str
    profile_picture: Optional[str] = None


class SpeakerCreateIn(Schema):
    email: str
    display_name: str
    biography: str


class SpeakerUpdateIn(Schema):
    email: Optional[str] = None
    display_name: Optional[str] = None
    biography: Optional[str] = None


class ProposalSpeakerOut(Schema):
    id: uuid.UUID
    speaker: SpeakerOut
    role: str
    sort_order: int


# Unified schemas with all optional fields
class ProposalIn(Schema):
    title: Optional[str] = Field(default=None, max_length=30)
    submission_type: Optional[str] = Field(default=None, max_length=1000)
    area: Optional[str] = Field(default=None, max_length=1000)
    language: Optional[str] = Field(default=None, max_length=1000)
    abstract: Optional[str] = Field(default=None, max_length=1000)
    description: Optional[str] = Field(default=None, max_length=5000)
    internal_notes: Optional[str] = Field(default=None, max_length=5000)
    occurrence_count: Optional[int] = Field(default=None)
    duration_days: Optional[int] = Field(default=None, max_length=1000)
    duration_time_per_day: Optional[str] = Field(default=None, max_length=1000)
    is_basic_course: Optional[bool] = Field(default=None)
    max_participants: Optional[int] = Field(default=None)
    material_cost_eur: Optional[str] = Field(default=None, max_length=1000)
    preferred_dates: Optional[str] = Field(default=None, max_length=1000)
    has_building_access: Optional[bool] = Field(default=None)


class SpeakerIn(Schema):
    email: Optional[str] = None
    display_name: Optional[str] = None
    biography: Optional[str] = None


# Lookup table schema
class LookupOut(Schema):
    code: str
    label: str
    description: str = ""
    is_active: bool = True
    sort_order: int = 0


class ExternalCalendarEvent(Schema):
    id: str
    title: str
    startUtc: str
    endUtc: str
    source: str


class ProposalHistoryEntry(Schema):
    """Represents a single change in the proposal's history."""

    timestamp: str  # ISO format datetime
    changed_by: str  # username of user who made the change
    change_type: str  # 'create', 'change', 'delete'
    field_name: Optional[str] = None  # which field was changed
    old_value: Optional[str] = None  # previous value
    new_value: Optional[str] = None  # new value
    summary: str  # human-readable summary of the change


class ProposalHistory(Schema):
    """List of historical changes for a proposal."""

    proposal_id: uuid.UUID
    entries: list[ProposalHistoryEntry]


class ProposalTransitionOut(Schema):
    """Information about a single available or unavailable transition."""

    action: str  # 'submit', 'accept', 'reject', 'revise'
    label_id: str  # stable i18n key, e.g. 'submit', 'resubmit', 'revise_after_rejection'
    target_status: str  # target status
    enabled: bool  # whether the transition is currently allowed
    disable_reason: Optional[str] = None  # reason if disabled
    # i18n id of a warning about mail sent to the proposal owner, None if no such mail
    mail_warning_id: Optional[str] = None


class ProposalTransitions(Schema):
    """List of available transitions for a proposal."""

    proposal_id: uuid.UUID
    current_status: str
    transitions: list[ProposalTransitionOut]


class ProposalEventSummary(Schema):
    """Summary of an event linked to a proposal."""

    id: uuid.UUID
    name: str
    startTime: str
    endTime: str
    status: str
    series_id: uuid.UUID
    series_name: str


class EventTransitionOut(Schema):
    """Information about a single available or unavailable event transition."""

    action: str
    label_id: str  # stable i18n key, matches action name
    target_status: str
    enabled: bool
    disable_reason: Optional[str] = None
    # i18n id of a warning about mail sent to the proposal owner, None if no such mail
    mail_warning_id: Optional[str] = None


class EventTransitions(Schema):
    """List of available transitions for an event."""

    event_id: uuid.UUID
    current_status: str
    transitions: list[EventTransitionOut]


class FlowEdge(Schema):
    source: str
    target: str
    label_id: str


class EventFlowDiagram(Schema):
    """Static structure of the event FSM for frontend diagram rendering."""

    nodes: list[str]
    edges: list[FlowEdge]


class CreateCalculatedPricesIn(Schema):
    """Creation mode for calculated prices: default configuration or manual empty values."""

    use_default_pricing_configuration: bool = True

    model_config = {"extra": "forbid"}


class UpdateCalculatedPricesIn(Schema):
    pricing_configuration_id: Optional[uuid.UUID] = None
    member_regular_gross_eur: Optional[str] = None
    member_discounted_gross_eur: Optional[str] = None
    guest_regular_gross_eur: Optional[str] = None
    guest_discounted_gross_eur: Optional[str] = None
    business_net_eur: Optional[str] = None
    internal_training_eur: Optional[str] = None


class CalculatedPricesOut(Schema):
    id: uuid.UUID
    event_id: uuid.UUID
    pricing_configuration_id: Optional[uuid.UUID] = None
    member_regular_gross_eur: Optional[str] = None
    member_discounted_gross_eur: Optional[str] = None
    guest_regular_gross_eur: Optional[str] = None
    guest_discounted_gross_eur: Optional[str] = None
    business_net_eur: Optional[str] = None
    internal_training_eur: Optional[str] = None


class SyncTargetOut(Schema):
    id: uuid.UUID
    type: str
    public_properties: dict[str, str]


class SyncItemOut(Schema):
    id: uuid.UUID
    target_id: uuid.UUID
    event_id: uuid.UUID


# ── Proposal reviews ─────────────────────────────────────────────────────────

class ProposalReviewOut(Schema):
    id: uuid.UUID
    kind: str  # 'user' | 'group'
    # user review fields
    reviewer_id: Optional[uuid.UUID] = None
    reviewer_username: Optional[str] = None
    reviewer_is_system: bool = False
    # group request fields
    group_code: str = ""
    group_label: str = ""
    group_member_count: Optional[int] = None
    # status/comment
    status: str = "pending"
    comment: str = ""
    # attribution
    requested_by_id: Optional[uuid.UUID] = None
    requested_by_username: Optional[str] = None
    requested_at: Optional[str] = None
    completed_at: Optional[str] = None
    requested_directly: bool = False
    requested_via_groups: list[str] = Field(default_factory=list)
    # resubmission history
    previous_status: str = ""
    previous_comment: str = ""
    # flags
    migrated: bool = False
    created_at: str = ""


class ProposalReviewsOut(Schema):
    """Wrapper returned by GET /proposals/{id}/reviews."""
    reviews: list[ProposalReviewOut]
    pending_via_groups: list[str] = Field(default_factory=list)


class ProposalReviewCreateIn(Schema):
    """Create a new review or group-review request."""
    kind: str  # 'user' | 'group'
    # For kind='user': reviewer_id (omit for a self-review)
    reviewer_id: Optional[uuid.UUID] = None
    # For kind='group'
    group_code: Optional[str] = None
    # For migrated system reviews
    reviewer_is_system: bool = False
    comment: str = ""
    status: str = "pending"  # for system/note reviews
    requested_directly: bool = False
    requested_via_groups: list[str] = Field(default_factory=list)
    migrated: bool = False


class ProposalReviewUpdateIn(Schema):
    """Submit or update a vote on an existing user review."""
    status: str
    comment: str = ""


class ProposalReviewResetIn(Schema):
    """Reset all reviews to pending (called on resubmission)."""
    pass
