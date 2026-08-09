package udm.udmframeworkv1.modules.event

import data.udmtree
import rego.v1

# ─────────────────────────────────────────────────────────────────────────────
# event.rego — demo UDM type for events-and-sync.md.
#
# Deliberately self-contained (does not reuse save.rego/view.rego, which are
# Proposal-specific) so it stands alone as a minimal worked example of:
#   §1.1  entity_select field ("origin") + the immutable-after-create pattern
#   §1.3  effective values, coalescing an override with the linked proposal
#   §2    a dynamic linked_inputs request ("origin")
#   §1.4  the effective object rendered into a markdown_display element
#         (type_config on the "summary" FormElement, see UDM_BUNDLE.json)
#   §1.5  Proposal's "linked_events" backlink_list element (UDM_BUNDLE.json)
#         reads this type back via its "origin" field
#   §3.2  the sync_status element (UDM_BUNDLE.json) — empty until a target
#         exists (sync_core has no ported plugin yet)
# Loaded alongside framework.rego (tree walker) + udm.rego (aggregator) +
# sudo.rego (bypass for superusers in sudo mode) — the same shared Policy
# rows Proposal uses.
# ─────────────────────────────────────────────────────────────────────────────

default allow := false

allow if {
	input.action in {"view", "browse"}
	input.user.is_active
}

allow if {
	input.action in {"save", "create", "delete", "preview"}
	input.user.is_staff
}

# ── status workflow (§15, ported from apiv1/flows.py EventFlow) ────────────
# Simplified vs. the apiv1 original: every transition is staff-only (no
# per-transition permission classes) — see the "Event Lifecycle Workflow"
# description in UDM_BUNDLE.json for the other simplifications (cancel/archive
# from any state, no auto-publish, no date-window conditions).

allow if {
	input.action == "transition"
	input.field == "status"
	input.user.is_staff
}

# Preview matrix (§4): every candidate transition on the status field is
# shown as available whenever the user is staff — mirrors the blanket
# staff-only `allow` above, so the buttons shown match what firing them
# would actually permit.
valid_transitions contains {"node": node_id, "field": field_slug, "name": name} if {
	input.user.is_staff
	some node_id, wf_fields in input.candidate_transitions
	some field_slug, wf in wf_fields
	field_slug == "status"
	some name, _ in wf.transitions
}

# ── field grants ────────────────────────────────────────────────────────────
# Every field is viewable. Every field is editable by staff EXCEPT "origin"
# once it carries a value — the immutable-after-create pattern (§1.1): no
# schema flag, just an editable_fields exclusion gated on the field's own
# current value.

viewable_fields contains {"node": node.id, "field": f} if {
	some node in udmtree.tree_nodes
	some f, _ in node.fields
}

_NO_VALUE_DISPLAY_TYPES := {"markdown_display", "backlink_list", "sync_status", "calendar"}

editable_fields contains {"node": node.id, "field": f} if {
	input.user.is_staff
	some node in udmtree.tree_nodes
	some f, entry in node.fields
	f != "origin"
	not entry.data_type in _NO_VALUE_DISPLAY_TYPES
}

editable_fields contains {"node": node.id, "field": "origin"} if {
	input.user.is_staff
	some node in udmtree.tree_nodes
	node.fields.origin.value == null
}

# ── timeslots submodel grants (Step 10, §6.1) ───────────────────────────────
# Staff may add/remove Timeslot children on any Event — start/end fields on
# the child nodes are already covered by the tree-wide viewable_fields /
# editable_fields rules above.

creatable_submodels contains {
	"node": input.entity.id, "field": "timeslots",
	"viewable": ["start", "end"],
	"editable": ["start", "end"],
} if {
	input.user.is_staff
}

deletable_nodes contains child.id if {
	input.user.is_staff
	some child in object.get(input.entity.children, "timeslots", [])
}

# ── linked_inputs (§2) ───────────────────────────────────────────────────────
# Request the linked proposal document so `effective` (below) can read its
# title. Unconditional here; a real policy might gate this on workflow state.

linked_inputs contains "origin"

# ── effective (§1.3) ─────────────────────────────────────────────────────────
# title_override wins when set; otherwise fall back to the linked proposal's
# title. input.linked.origin is null when origin is unset OR the reference is
# dangling (deleted proposal) — both fall through to the same default.

effective["title"] := v if {
	v := input.entity.fields.title_override.value
	v != null
	v != ""
}

effective["title"] := input.linked.origin.fields.title.value if {
	input.entity.fields.title_override.value in {null, ""}
	input.linked.origin != null
}

effective["title"] := "(untitled event)" if {
	input.entity.fields.title_override.value in {null, ""}
	input.linked.origin == null
}

# ── sync trigger: fire on entering "published", plus a manual re-sync ───────
# execute_transition() evaluates the policy (and freezes its `actions` set)
# BEFORE writing the new workflow state (engine.py) — so checking
# input.entity.fields.status.value here would still see the OLD state. The
# transition's own name is what's available at evaluation time, so gate on
# that instead of the field value.
#
# "publish" fires it once, the moment that transition executes. The three
# resync_<state> transitions (self-loops: published->published,
# confirmed->confirmed, completed->completed — see the "Event Lifecycle
# Workflow" transitions in UDM_BUNDLE.json) exist so a re-sync can be
# triggered from those three states without leaving them — sync only ever
# makes sense once an event is published, so a state without a resync_<state>
# self-loop simply has no such button. mark_sync's own get_or_create on
# (entity, target) (sync_core/models.py) already updates the existing
# SyncBaseItem in place rather than creating a second one — true for both
# "publish" and every resync_<state> firing.
actions contains {
	"type": "mark_sync", "phase": "post",
	"target": "pretix-test", "status": "pending",
} if {
	input.action == "transition"
	input.field == "status"
	input.transition in {"publish", "resync_published", "resync_confirmed", "resync_completed"}
}

# ── timeslot span (§15, why sync_pretix doesn't get §13.3's per-timeslot
# fan-out): a Pretix subevent is one span, not a list of remote objects, so
# effective.start/effective.end collapse every Timeslot child into the
# earliest start / latest end instead of exposing one pair per slot.

_timeslot_children := object.get(input.entity.children, "timeslots", [])

_timeslot_starts_ns := {ns |
	some child in _timeslot_children
	raw := child.fields.start.value
	raw != null
	ns := time.parse_rfc3339_ns(raw)
}

_timeslot_ends_ns := {ns |
	some child in _timeslot_children
	raw := child.fields.end.value
	raw != null
	ns := time.parse_rfc3339_ns(raw)
}

effective["start"] := time.format(min(_timeslot_starts_ns)) if {
	count(_timeslot_starts_ns) > 0
}

effective["end"] := time.format(max(_timeslot_ends_ns)) if {
	count(_timeslot_ends_ns) > 0
}

# ── course pricing (§15) ─────────────────────────────────────────────────────
# Rego port of PretixPricingConfiguration.get_calculated_prices()
# (sync_pretix/models.py:721-848). The seven rate constants below are
# hardcoded in-policy — an implementer wiring this against a live pricing
# admin should instead read them via a `data.*` import per the rego contract,
# see events-and-sync.md §15's first open decision; hardcoding is option (a)
# from that list, picked here to keep this example self-contained.

_prep_hours := 0
_lecturer_rate := 40
_workshop_rate_basis := 10
_workshop_rate_regular := 20
_guest_surcharge := 10
_discount_rate := 0.50
_business_surcharge := 0.75
_vat_rate := 0.07

# threshold -> deduction, highest threshold <= max_participants wins
# (PretixPricingConfiguration.get_min_participants, models.py:710-719).
_min_participants_params := {0: 1, 7: 2}

# duration_hours/material_cost/max_participants/is_basic_course are NOT
# separate Event fields — they already live on the linked Proposal (`origin`,
# same `input.linked.origin` §2 dependency `effective["title"]` above already
# uses): `is-basic-course`, `max-participants`, `material-cost-eur`,
# `duration-days` + `duration-time-per-day` (UDM_BUNDLE.json, Proposal field
# config). Reading them off the Proposal rather than duplicating them onto
# Event avoids a second, driftable copy of data an editor already fills in
# when writing the proposal. `duration_hours` itself needs a small derivation
# (`duration-time-per-day` is an "HH:MM" string) mirroring
# `time_string_to_minutes` (apiv1/models/basedata.py:50-61).

_duration_time_parts := split(input.linked.origin.fields["duration-time-per-day"].value, ":")

# to_number() rejects zero-padded strings ("01" is not valid JSON number
# grammar) — "HH:MM" is always zero-padded, so strip leading zeros first.
_num_stripped_zeros(s) := to_number(t) if {
	t := trim_left(s, "0")
	t != ""
}

_num_stripped_zeros(s) := 0 if trim_left(s, "0") == ""

_duration_time_minutes := (_num_stripped_zeros(_duration_time_parts[0]) * 60) + _num_stripped_zeros(_duration_time_parts[1]) if {
	count(_duration_time_parts) > 1
}

_duration_time_minutes := _num_stripped_zeros(_duration_time_parts[0]) if {
	count(_duration_time_parts) == 1
}

default _duration_time_minutes := 0

_duration := (_duration_time_minutes * to_number(input.linked.origin.fields["duration-days"].value)) / 60

_material := to_number(input.linked.origin.fields["material-cost-eur"].value)

_is_basic_course := input.linked.origin.fields["is-basic-course"].value == true

_workshop_rate := _workshop_rate_basis if _is_basic_course
_workshop_rate := _workshop_rate_regular if not _is_basic_course

# ── max/min participants (overridable, same coalescing pattern as `title`
# above) ─────────────────────────────────────────────────────────────────────
# max_participants_override wins when set; otherwise the linked Proposal's
# own estimate. min_participants_override wins when set; otherwise the
# threshold-deduction table applied to the *effective* (possibly overridden)
# max_participants — so overriding max also re-derives min unless min itself
# is overridden too.

effective["max_participants"] := v if {
	v := to_number(input.entity.fields.max_participants_override.value)
	input.entity.fields.max_participants_override.value != null
}

effective["max_participants"] := to_number(input.linked.origin.fields["max-participants"].value) if {
	input.entity.fields.max_participants_override.value == null
}

_max_participants := effective["max_participants"]

_min_participants_deduction := d if {
	applicable := [t | some t, _ in _min_participants_params; t <= _max_participants]
	count(applicable) > 0
	d := _min_participants_params[max(applicable)]
}

default _min_participants_deduction := 0

_min_participants_computed := max([_max_participants - _min_participants_deduction, 1])

effective["min_participants"] := v if {
	v := to_number(input.entity.fields.min_participants_override.value)
	input.entity.fields.min_participants_override.value != null
}

effective["min_participants"] := _min_participants_computed if {
	input.entity.fields.min_participants_override.value == null
}

_min_participants := effective["min_participants"]

# ── course pricing, each price overridable individually (same pattern) ──────

effective["price_member_regular"] := v if {
	v := to_number(input.entity.fields.price_member_regular_override.value)
	input.entity.fields.price_member_regular_override.value != null
}

effective["price_member_regular"] := ceil(
	(_duration * (_workshop_rate + _lecturer_rate) + _lecturer_rate * _prep_hours) *
	(1 + _vat_rate) / _min_participants + _material,
) if {
	input.entity.fields.price_member_regular_override.value == null
}

effective["price_member_discounted"] := v if {
	v := to_number(input.entity.fields.price_member_discounted_override.value)
	input.entity.fields.price_member_discounted_override.value != null
}

effective["price_member_discounted"] := ceil(
	(_duration * (_workshop_rate * (1 - _discount_rate) + _lecturer_rate) + _lecturer_rate * _prep_hours) *
	(1 + _vat_rate) / _min_participants + _material,
) if {
	input.entity.fields.price_member_discounted_override.value == null
}

effective["price_guest_regular"] := v if {
	v := to_number(input.entity.fields.price_guest_regular_override.value)
	input.entity.fields.price_guest_regular_override.value != null
}

effective["price_guest_regular"] := ceil(
	(_duration * (_workshop_rate + _guest_surcharge + _lecturer_rate) + _lecturer_rate * _prep_hours) *
	(1 + _vat_rate) / _min_participants + _material,
) if {
	input.entity.fields.price_guest_regular_override.value == null
}

# Matches the documented pricing sheet exactly: "guest discounted" reuses the
# member-regular formula (get_guest_discounted_price, models.py:797-808)
# rather than a separate guest+discount calculation — preserve the quirk. An
# explicit override still wins over that reuse.

effective["price_guest_discounted"] := v if {
	v := to_number(input.entity.fields.price_guest_discounted_override.value)
	input.entity.fields.price_guest_discounted_override.value != null
}

effective["price_guest_discounted"] := effective["price_member_regular"] if {
	input.entity.fields.price_guest_discounted_override.value == null
}

_business_base := ceil(
	(_duration * (_workshop_rate + _guest_surcharge + _lecturer_rate) + _lecturer_rate * _prep_hours) /
	_min_participants + _material,
)

effective["price_business"] := v if {
	v := to_number(input.entity.fields.price_business_override.value)
	input.entity.fields.price_business_override.value != null
}

effective["price_business"] := ceil(_business_base * (1 + _business_surcharge)) if {
	input.entity.fields.price_business_override.value == null
}

effective["price_internal_training"] := v if {
	v := to_number(input.entity.fields.price_internal_training_override.value)
	input.entity.fields.price_internal_training_override.value != null
}

effective["price_internal_training"] := _material if {
	input.entity.fields.price_internal_training_override.value == null
}
