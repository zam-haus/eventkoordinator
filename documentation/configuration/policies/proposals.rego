package udm.udmframeworkv1.modules.proposals

import data.udm.udmframeworkv1.modules.config._deadline
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.utils
import data.udm.udmframeworkv1.modules.workflow
import data.udm.udmframeworkv1.modules.workflow.current_status
import data.udm.udmframeworkv1.modules.sudo
import rego.v1

_is_validation := utils.is_validation

# ─── allow ─────────────────────────────────────────────────────────────────────
default allow := false

# ─── allow: view ───────────────────────────────────────────────────────────────

allow if {
	input.action == "view"
	roles.is_owner_or_editor
	print("[allow:view] owner/editor user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "view"
	roles.is_moderator
	current_status != "draft"
	print("[allow:view] moderator user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "view"
	roles.is_reviewer
	current_status != "draft"
	print("[allow:view] reviewer user=", input.user.username, "status=", current_status)
}

# ─── allow: browse ─────────────────────────────────────────────────────────────
allow if {
	input.action == "browse"
	roles.is_owner_or_editor
	print("[allow:browse] owner/editor user=", input.user.username)
}

allow if {
	input.action == "browse"
	roles.is_moderator
	print("[allow:browse] moderator user=", input.user.username)
}

allow if {
	input.action == "browse"
	roles.is_reviewer
	print("[allow:browse] reviewer user=", input.user.username)
}


# ─── allow: save ───────────────────────────────────────────────────────────────
allow if {
	input.action == "save"
	roles.is_owner_or_editor
	roles.is_status_editable
	print("[allow:save] owner/editor user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "save"
	roles.is_moderator
	print("[allow:save] moderator user=", input.user.username, "status=", current_status)
}

allow if {
	input.action == "save"
	roles.is_reviewer
	current_status == "submitted"
	print("[allow:save] reviewer user=", input.user.username)
}


# ─── allow: delete ─────────────────────────────────────────────────────────────
allow if {
	input.action == "delete"
	roles.is_owner
	current_status == "draft"
	print("[allow:delete] owner user=", input.user.username)
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


# ─── error_messages ────────────────────────────────────────────────────────────
# Evaluated before allow; critical-level entries block save/transition.

error_messages contains msg if {
	input.action == "save"
	_can_view
	input.changed_fields.owner
	print("[block:owner] user=", input.user.username, "attempted to change owner")
	msg := {
		"level": "critical",
		"text": "The owner cannot be changed.",
		"field_slug": "owner",
	}
}

_reviewer_save_permitted if {
	roles.is_reviewer
	current_status == "submitted"
	print("[role] _reviewer_save_permitted user=", input.user.username)
}

error_messages contains msg if {
	input.action == "save"
	roles.is_owner_or_editor
	not roles.is_moderator
	not _reviewer_save_permitted
	workflow.is_status_editable
	print(
		"[block:status-edit] user=", input.user.username,
		"status=", current_status, "not in editable statuses",
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
	not roles.is_reviewer
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
	roles.is_owner_or_editor
	print("[can_view] owner/editor user=", input.user.username)
}

_can_view if {
	sudo.is_superuser_sudo
	print("[can_view] sudo user=", input.user.username)
}

_can_view if {
	roles.is_moderator
	current_status != "draft"
	print("[can_view] moderator user=", input.user.username, "status=", current_status)
}

_can_view if {
	roles.is_reviewer
	current_status != "draft"
	print("[can_view] reviewer user=", input.user.username, "status=", current_status)
}

# True when the user holds a role that would grant view access post-draft.
_has_limited_role if {
	roles.is_moderator
	print("[role] _has_limited_role (moderator) user=", input.user.username)
}

_has_limited_role if {
	roles.is_reviewer
	print("[role] _has_limited_role (reviewer) user=", input.user.username)
}

error_messages contains msg if {
	input.action == "view"
	not _can_view
	_has_limited_role
	not roles.is_owner_or_editor
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
	not roles.is_owner_or_editor
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
	roles.is_owner_or_editor
	label := object.get(workflow._STATUS_LABEL, current_status, current_status)
	msg := {
		"level": "info",
		"text": sprintf("Status: %v", [label]),
		"field_slug": "status",
	}
}

success_messages contains msg if {
	_proposal_ctx
	roles.is_moderator
	current_status != "draft"
	label := workflow.current_status_label
	msg := {
		"level": "info",
		"text": sprintf("Status: %v", [label]),
		"field_slug": "status",
	}
}

success_messages contains msg if {
	_proposal_ctx
	roles.is_reviewer
	not roles.is_moderator
	current_status != "draft"
	label := workflow.current_status_label
	msg := {
		"level": "info",
		"text": sprintf("Status: %v", [label]),
		"field_slug": "status",
	}
}

# ── View/Save/Transition: role context ──
success_messages contains msg if {
	_proposal_ctx
	roles.is_owner
	not roles.is_moderator
	msg := {
		"level": "info",
		"text": "You are the owner of this proposal.",
		"field_slug": "owner",
	}
}

success_messages contains msg if {
	_proposal_ctx
	roles.is_editor
	not roles.is_owner
	msg := {
		"level": "info",
		"text": "You are an editor of this proposal.",
		"field_slug": "editors",
	}
}

success_messages contains msg if {
	_proposal_ctx
	roles.is_moderator
	msg := {
		"level": "info",
		"text": "You are reviewing this proposal as a moderator.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	_proposal_ctx
	roles.is_reviewer
	not roles.is_moderator
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
	roles.is_owner_or_editor
	current_status == "draft"
	msg := {
		"level": "info",
		"text": "Draft saved. Submit the proposal when all required fields are complete.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not _is_validation
	roles.is_owner_or_editor
	current_status == "revise"
	msg := {
		"level": "info",
		"text": "Revisions saved. Resubmit the proposal when ready.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not _is_validation
	roles.is_moderator
	msg := {
		"level": "info",
		"text": "Reviewer assignments updated.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not _is_validation
	roles.is_reviewer
	current_status == "submitted"
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
	roles.is_owner_or_editor
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
	roles.is_moderator
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
	roles.is_moderator
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
	roles.is_moderator
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
	roles.is_moderator
	msg := {
		"level": "info",
		"text": "Proposal returned to revision. The owner may update and resubmit.",
		"field_slug": null,
	}
}
# ─── viewable_fields ───────────────────────────────────────────────────────────
viewable_fields contains f if {
	input.entity.fields[f]
	not f in data.udm.protected_fields
}

# Owner/editors can edit content fields; reviewer assignment is moderator-only.
editable_fields contains f if {
	roles.is_owner_or_editor
	workflow.is_status_editable
	input.entity.fields[f]
	not f in data.udm.protected_fields
}
