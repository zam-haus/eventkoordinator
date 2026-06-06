package udm.udmframeworkv1.modules.proposals

import data.udm.udmframeworkv1.modules.config._deadline
import rego.v1

# ─── Configuration ─────────────────────────────────────────────────────────────
# Set to true to allow superusers to bypass all access restrictions.
SUDO_ACTIVE := false

# Deadline after which new proposals may no longer be created (ISO-8601 UTC).
# Must match the deadline in description.rego.
SUBMISSION_DEADLINE := "2026-12-31T23:59:59Z"

# Group names whose members act as proposal moderators.
MODERATOR_GROUP_NAMES := ["moderators"]

# States in which owner/editors may edit proposal content.
EDITABLE_STATUSES := {"draft", "revise"}

# States in which reviews become visible to the owner/editors.
# Reviews are hidden while the proposal is in draft or submitted,
# so owners cannot see reviewer feedback until a decision is reached.
POST_REVIEW_STATUSES := {"revise", "accepted", "rejected"}

# Human-readable status labels for info messages.
_STATUS_LABEL := {
	"draft": "Draft",
	"submitted": "Submitted — awaiting review",
	"revise": "Revision requested",
	"accepted": "Accepted",
	"rejected": "Rejected",
}

# ─── Current workflow state ────────────────────────────────────────────────────
# Workflow fields serialize as the state name string (e.g. "draft").
current_status := input.entity.fields.status.value

# ─── Role helpers ──────────────────────────────────────────────────────────────
is_owner if {
	owner_val := input.entity.fields.owner.value
	owner_val != null
	owner_val.id == input.user.id
	print("[role] is_owner user=", input.user.username)
}

is_editor if {
	editors := input.entity.fields.editors.value
	editors != null
	some ed in editors
	ed.id == input.user.id
	print("[role] is_editor user=", input.user.username)
}

is_owner_or_editor if is_owner
is_owner_or_editor if is_editor

is_moderator if {
	some group_name in MODERATOR_GROUP_NAMES
	some ug in input.user.groups
	ug.name == group_name
	print("[role] is_moderator user=", input.user.username, "group=", ug.name)
}

is_direct_reviewer if {
	reviewer_users := input.entity.fields["requested-reviewer-users"].value
	reviewer_users != null
	some ru in reviewer_users
	ru.id == input.user.id
	print("[role] is_direct_reviewer user=", input.user.username)
}

is_group_reviewer if {
	reviewer_groups := input.entity.fields["requested-reviewer-groups"].value
	reviewer_groups != null
	some rg in reviewer_groups
	some ug in input.user.groups
	rg.id == ug.id
	print("[role] is_group_reviewer user=", input.user.username, "group=", rg.name)
}

is_reviewer if is_direct_reviewer
is_reviewer if is_group_reviewer

is_superuser_sudo if {
	SUDO_ACTIVE
	input.user.is_superuser
	print("[role] is_superuser_sudo user=", input.user.username)
}

# ─── allow ─────────────────────────────────────────────────────────────────────
default allow := false

# ─── allow: view ───────────────────────────────────────────────────────────────

allow if {
	input.action == "view"
	is_owner_or_editor
	print("[allow:view] owner/editor user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "view"
	is_superuser_sudo
	print("[allow:view] sudo user=", input.user.username)
}

allow if {
	input.action == "view"
	is_moderator
	current_status != "draft"
	print("[allow:view] moderator user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "view"
	is_reviewer
	current_status != "draft"
	print("[allow:view] reviewer user=", input.user.username, "status=", current_status)
}

# ─── allow: browse ─────────────────────────────────────────────────────────────
allow if {
	input.action == "browse"
	is_owner_or_editor
	print("[allow:browse] owner/editor user=", input.user.username)
}

allow if {
	input.action == "browse"
	is_moderator
	print("[allow:browse] moderator user=", input.user.username)
}

allow if {
	input.action == "browse"
	is_reviewer
	print("[allow:browse] reviewer user=", input.user.username)
}

allow if {
	input.action == "browse"
	is_superuser_sudo
	print("[allow:browse] sudo user=", input.user.username)
}

# ─── allow: save ───────────────────────────────────────────────────────────────
allow if {
	input.action == "save"
	is_owner_or_editor
	current_status in EDITABLE_STATUSES
	no_critical_errors
	print("[allow:save] owner/editor user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "save"
	is_moderator
	no_critical_errors
	print("[allow:save] moderator user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "save"
	is_reviewer
	current_status == "submitted"
	no_critical_errors
	print("[allow:save] reviewer user=", input.user.username)
}

allow if {
	input.action == "save"
	is_superuser_sudo
	no_critical_errors
	print("[allow:save] sudo user=", input.user.username)
}

# ─── allow: delete ─────────────────────────────────────────────────────────────
allow if {
	input.action == "delete"
	is_owner
	current_status == "draft"
	print("[allow:delete] owner user=", input.user.username)
}

allow if {
	input.action == "delete"
	is_superuser_sudo
	print("[allow:delete] sudo user=", input.user.username)
}

# ─── allow: create ─────────────────────────────────────────────────────────────
# Any active logged-in user may create a new proposal before the deadline.
allow if {
	input.action == "create"
	input.user.is_active
	print(
		"[allow:create] checking deadline for user=", input.user.username,
		"now_ns=", time.now_ns(), "deadline=", _deadline,
	)
	time.now_ns() <= time.parse_rfc3339_ns(_deadline)
	print("[allow:create] deadline ok, user=", input.user.username)
}

allow if {
	input.action == "create"
	is_superuser_sudo
	print("[allow:create] sudo user=", input.user.username)
}

# ─── error_messages ────────────────────────────────────────────────────────────
# Evaluated before allow; critical-level entries block save/transition.

error_messages contains msg if {
	input.action == "save"
	_can_view
	input.changed_fields["proposal-id"]
	not is_superuser_sudo
	print("[block:proposal-id] user=", input.user.username, "attempted to change proposal-id")
	msg := {
		"level": "critical",
		"text": "The proposal ID cannot be changed.",
		"field_slug": "proposal-id",
	}
}

error_messages contains msg if {
	input.action == "save"
	_can_view
	input.changed_fields.owner
	not is_superuser_sudo
	print("[block:owner] user=", input.user.username, "attempted to change owner")
	msg := {
		"level": "critical",
		"text": "The owner cannot be changed.",
		"field_slug": "owner",
	}
}

_reviewer_save_permitted if {
	is_reviewer
	current_status == "submitted"
	print("[role] _reviewer_save_permitted user=", input.user.username)
}

error_messages contains msg if {
	input.action == "save"
	is_owner_or_editor
	not is_superuser_sudo
	not is_moderator
	not _reviewer_save_permitted
	not current_status in EDITABLE_STATUSES
	print(
		"[block:status-edit] user=", input.user.username,
		"status=", current_status, "not in editable statuses:", EDITABLE_STATUSES,
	)
	msg := {
		"level": "critical",
		"text": "Proposals can only be edited in draft or revise status.",
		"field_slug": null,
	}
}

# Block modifying or deleting another user's review.
# Uses input.old_entity (pre-write snapshot) so the check works even for
# delete operations where the review no longer exists in input.entity.
error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_superuser_sudo
	some op in input.changed_fields.reviews.value
	op.op in {"update", "delete"}
	some existing in object.get(input.old_entity.children, "reviews", [])
	existing.id == op.id
	existing.fields.author.value.id != input.user.id
	print(
		"[block:review-modify] user=", input.user.username,
		"op=", op.op, "review_author=", existing.fields.author.value.id,
	)
	msg := {
		"level": "critical",
		"text": "You can only modify your own reviews.",
		"field_slug": "reviews",
	}
}

# Block changing the author field on an existing review.
error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_superuser_sudo
	some op in input.changed_fields.reviews.value
	op.op == "update"
	op.fields.author
	print(
		"[block:review-author-change] user=", input.user.username,
		"attempted to change author on review op=", op,
	)
	msg := {
		"level": "critical",
		"text": "The review author cannot be changed after creation.",
		"field_slug": "reviews",
	}
}

# Block creating a review attributed to someone other than the current user.
error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_superuser_sudo
	some op in input.changed_fields.reviews.value
	op.op == "create"
	op.fields.author != null
	op.fields.author != input.user.id
	print(
		"[block:review-attribution] user=", input.user.username,
		"tried to create review as=", op.fields.author,
	)
	msg := {
		"level": "critical",
		"text": "You can only create reviews as yourself.",
		"field_slug": "reviews",
	}
}

# Block non-reviewers (including moderators not in the reviewer lists) from creating reviews.
error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_superuser_sudo
	not is_reviewer
	some op in input.changed_fields.reviews.value
	op.op == "create"
	print(
		"[block:review-not-reviewer] user=", input.user.username,
		"is not a designated reviewer, status=", current_status,
	)
	msg := {
		"level": "critical",
		"text": "Only designated reviewers may add reviews.",
		"field_slug": "reviews",
	}
}

# ─── save/transition denial for non-viewers ────────────────────────────────────
# Produces a single generic critical message when the user cannot view the entity
# and attempts a save or transition.  This replaces the Python-level "Save denied
# by policy." fallback and prevents any other detailed error_messages from leaking
# state information to someone who has no view access.

error_messages contains msg if {
	input.action in {"save", "transition"}
	not _can_view
	print(
		"[deny:save/transition] no view access user=", input.user.username,
		"action=", input.action, "status=", current_status,
	)
	msg := {
		"level": "critical",
		"text": "Access denied.",
		"field_slug": null,
	}
}

# ─── view-denial messages ──────────────────────────────────────────────────────
# _can_view mirrors the view allow rules without referencing allow/no_critical_errors,
# so there is no cyclic dependency.  All denial messages are gated on not _can_view,
# which ensures they only fire when the user truly has no path to view access —
# regardless of how many roles they hold simultaneously.

_can_view if {
	is_owner_or_editor
	print("[can_view] owner/editor user=", input.user.username)
}

_can_view if {
	is_superuser_sudo
	print("[can_view] sudo user=", input.user.username)
}

_can_view if {
	is_moderator
	current_status != "draft"
	print("[can_view] moderator user=", input.user.username, "status=", current_status)
}

_can_view if {
	is_reviewer
	current_status != "draft"
	print("[can_view] reviewer user=", input.user.username, "status=", current_status)
}

# True when the user holds a role that would grant view access post-draft.
_has_limited_role if {
	is_moderator
	print("[role] _has_limited_role (moderator) user=", input.user.username)
}

_has_limited_role if {
	is_reviewer
	print("[role] _has_limited_role (reviewer) user=", input.user.username)
}

error_messages contains msg if {
	input.action == "view"
	not _can_view
	_has_limited_role
	not is_owner_or_editor
	print("[deny:view] draft-blocked limited-role user=", input.user.username, "status=", current_status)
	msg := {
		"level": "error",
		"text": "This proposal is in draft and not yet visible to moderators or reviewers.",
		"field_slug": null,
	}
}

error_messages contains msg if {
	input.action == "view"
	not _can_view
	not _has_limited_role
	not is_owner_or_editor
	not is_superuser_sudo
	print("[deny:view] no-role user=", input.user.username, "status=", current_status)
	msg := {
		"level": "error",
		"text": "You do not have permission to view this proposal.",
		"field_slug": null,
	}
}

# ─── success_messages ──────────────────────────────────────────────────────────
# Informational messages shown alongside allowed actions.
# Does NOT reference `allow` — the conditions here imply allow is true for each
# case, avoiding a messages→allow→no_critical_errors→error_messages scheduling
# cycle that confuses regorus's rule scheduler.

# Guard used by proposal-level context messages: true for view/save and for
# transitions on the proposal status field. Suppresses proposal-level noise when
# the action is a review vote transition on a subfield.
_proposal_ctx if input.action == "save"

_proposal_ctx if {
	input.action == "transition"
	input.field == "status"
}

# ── View/Save/Transition: overall status label ──
success_messages contains msg if {
	_proposal_ctx
	is_owner_or_editor
	label := object.get(_STATUS_LABEL, current_status, current_status)
	msg := {
		"level": "info",
		"text": sprintf("Status: %v", [label]),
		"field_slug": "status",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status != "draft"
	label := object.get(_STATUS_LABEL, current_status, current_status)
	msg := {
		"level": "info",
		"text": sprintf("Status: %v", [label]),
		"field_slug": "status",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_reviewer
	not is_moderator
	current_status != "draft"
	label := object.get(_STATUS_LABEL, current_status, current_status)
	msg := {
		"level": "info",
		"text": sprintf("Status: %v", [label]),
		"field_slug": "status",
	}
}

# ── View/Save/Transition: role context ──
success_messages contains msg if {
	_proposal_ctx
	is_owner
	not is_moderator
	msg := {
		"level": "info",
		"text": "You are the owner of this proposal.",
		"field_slug": "owner",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_editor
	not is_owner
	msg := {
		"level": "info",
		"text": "You are an editor of this proposal.",
		"field_slug": "editors",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	msg := {
		"level": "info",
		"text": "You are reviewing this proposal as a moderator.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_reviewer
	not is_moderator
	current_status != "draft"
	msg := {
		"level": "info",
		"text": "You have been requested to review this proposal.",
		"field_slug": "reviews",
	}
}

# ── Save: context messages ──
success_messages contains msg if {
	input.action == "save"
	not _is_validation
	is_owner_or_editor
	current_status == "draft"
	no_critical_errors
	msg := {
		"level": "info",
		"text": "Draft saved. Submit the proposal when all required fields are complete.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not _is_validation
	is_owner_or_editor
	current_status == "revise"
	no_critical_errors
	msg := {
		"level": "info",
		"text": "Revisions saved. Resubmit the proposal when ready.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not _is_validation
	is_moderator
	no_critical_errors
	msg := {
		"level": "info",
		"text": "Reviewer assignments updated.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not _is_validation
	is_reviewer
	current_status == "submitted"
	no_critical_errors
	msg := {
		"level": "info",
		"text": "Your review has been saved.",
		"field_slug": null,
	}
}

# ── Transition: what is about to happen ──
success_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition in {"submit", "resubmit"}
	is_owner_or_editor
	msg := {
		"level": "info",
		"text": "Proposal submitted. Moderators will be notified for review.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition == "accept"
	is_moderator
	msg := {
		"level": "info",
		"text": "Proposal accepted. All requested reviewers approved.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition == "reject"
	is_moderator
	msg := {
		"level": "info",
		"text": "Proposal rejected. The owner will be notified.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition == "request-revision"
	is_moderator
	msg := {
		"level": "info",
		"text": "Revision requested. The owner will be notified to update and resubmit.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition == "allow-revision"
	is_moderator
	msg := {
		"level": "info",
		"text": "Proposal returned to revision. The owner may update and resubmit.",
		"field_slug": null,
	}
}

# ── SUDO notice ──
success_messages contains msg if {
	is_superuser_sudo
	msg := {
		"level": "info",
		"text": "SUDO mode active: all restrictions bypassed.",
		"field_slug": null,
	}
}

# ─── viewable_fields ───────────────────────────────────────────────────────────
viewable_fields := [f | some f in _viewable_set]

_viewable_set contains f if {
	is_superuser_sudo
	input.entity.fields[f]
}

_viewable_set contains f if {
	is_moderator
	current_status != "draft"
	input.entity.fields[f]
}

# Moderators always see reviewer fields, even in draft or when also owner/editor.
_viewable_set contains "reviews" if {
	is_moderator
	input.entity.fields.reviews
}

_viewable_set contains "requested-reviewer-groups" if {
	is_moderator
	input.entity.fields["requested-reviewer-groups"]
}

_viewable_set contains "requested-reviewer-users" if {
	is_moderator
	input.entity.fields["requested-reviewer-users"]
}

_viewable_set contains f if {
	is_reviewer
	current_status != "draft"
	input.entity.fields[f]
}

# Owner/editors see all fields except reviews while in draft or submitted.
_viewable_set contains f if {
	is_owner_or_editor
	input.entity.fields[f]
	f != "reviews"
}

_viewable_set contains "reviews" if {
	is_owner_or_editor
	current_status in POST_REVIEW_STATUSES
}

# ─── editable_fields ───────────────────────────────────────────────────────────
editable_fields := [f | some f in _editable_set]

_editable_set contains f if {
	is_superuser_sudo
	input.entity.fields[f]
}

# Owner/editors can edit content fields; reviewer assignment is moderator-only.
_editable_set contains f if {
	is_owner_or_editor
	current_status in EDITABLE_STATUSES
	input.entity.fields[f]
	not f in {"owner", "proposal-id", "requested-reviewer-groups", "requested-reviewer-users"}
}

# Moderators can manage the reviewer assignment fields.
_editable_set contains "requested-reviewer-groups" if is_moderator
_editable_set contains "requested-reviewer-users" if is_moderator

# Reviewers can add/update their own review submodel while the proposal is submitted.
# The author field within a review is set automatically and blocked from editing above.
_editable_set contains "reviews" if {
	is_reviewer
	current_status == "submitted"
}

# ─── Utilities ─────────────────────────────────────────────────────────────────
no_critical_errors if not any_critical_error

default any_critical_error := false

any_critical_error if {
	some m in error_messages
	m.level == "critical"
	print(m)
}

# True when the engine is doing a dry-run (validate_only=true from the API).
_is_validation if input.validate_only == true
