package udm

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
	"draft":     "Draft",
	"submitted": "Submitted — awaiting review",
	"revise":    "Revision requested",
	"accepted":  "Accepted",
	"rejected":  "Rejected",
}

# ─── Current workflow state ────────────────────────────────────────────────────
# Workflow fields serialize as the state name string (e.g. "draft").
current_status := input.entity.fields["status"].value

# ─── Role helpers ──────────────────────────────────────────────────────────────
is_owner if {
	owner_val := input.entity.fields.owner.value
	owner_val != null
	owner_val.id == input.user.id
}

is_editor if {
	editors := input.entity.fields.editors.value
	editors != null
	some ed in editors
	ed.id == input.user.id
}

is_owner_or_editor if is_owner
is_owner_or_editor if is_editor

is_moderator if {
	some group_name in MODERATOR_GROUP_NAMES
	some ug in input.user.groups
	ug.name == group_name
}

is_direct_reviewer if {
	reviewer_users := input.entity.fields["requested-reviewer-users"].value
	reviewer_users != null
	some ru in reviewer_users
	ru.id == input.user.id
}

is_group_reviewer if {
	reviewer_groups := input.entity.fields["requested-reviewer-groups"].value
	reviewer_groups != null
	some rg in reviewer_groups
	some ug in input.user.groups
	rg.id == ug.id
}

is_reviewer if is_direct_reviewer
is_reviewer if is_group_reviewer

is_superuser_sudo if {
	SUDO_ACTIVE
	input.user.is_superuser
}

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
	print("[allow:create] checking deadline for user=", input.user.username,
	      "now_ns=", time.now_ns(), "deadline=", _deadline)
	time.now_ns() <= time.parse_rfc3339_ns(_deadline)
	print("[allow:create] deadline ok, user=", input.user.username)
}

allow if {
	input.action == "create"
	is_superuser_sudo
	print("[allow:create] sudo user=", input.user.username)
}

# ─── allow: transition ─────────────────────────────────────────────────────────
allow if {
	input.action == "transition"
	input.transition in {"submit", "resubmit"}
	is_owner_or_editor
	print("[allow:transition] submit/resubmit user=", input.user.username,
	      "transition=", input.transition, "status=", current_status)
}

allow if {
	input.action == "transition"
	input.transition in {"reject", "request-revision", "allow-revision"}
	is_moderator
	print("[allow:transition] moderator action user=", input.user.username,
	      "transition=", input.transition, "status=", current_status)
}

allow if {
	input.action == "transition"
	input.transition == "accept"
	is_moderator
	all_reviews_accepted
	print("[allow:transition] accept granted user=", input.user.username)
}

# A reviewer may transition only the vote field on their own review node.
# input.node_id identifies the specific review node; we verify the review's author
# matches the current user. Moderators not in the reviewer lists cannot vote.
allow if {
	input.action == "transition"
	input.field == "vote"
	input.transition in {"accept", "reject", "revise", "reset"}
	current_status == "submitted"
	some r in input.entity.children.reviews
	r.id == input.node_id
	r.fields.author.value.id == input.user.id
	print("[allow:transition] vote user=", input.user.username,
	      "transition=", input.transition, "node=", input.node_id)
}

allow if {
	input.action == "transition"
	is_superuser_sudo
	print("[allow:transition] sudo user=", input.user.username,
	      "transition=", input.transition)
}

# ─── Accept gate ────────────────────────────────────────────────────────────────
# Acceptance requires at least one requested reviewer, and every requested
# individual/group must have voted "accept".
# Uses set operations instead of `every` for regorus compatibility.

_accepting_user_ids := {r.fields.author.value.id |
	some r in input.entity.children.reviews
	r.fields.vote.value == "accept"
}

_accepting_group_ids := {rg.id |
	some rg in input.entity.fields["requested-reviewer-groups"].value
	some member in rg.members
	some r in input.entity.children.reviews
	r.fields.author.value.id == member.id
	r.fields.vote.value == "accept"
}

all_reviews_accepted if {
	requested_user_ids := {u.id | some u in input.entity.fields["requested-reviewer-users"].value}
	requested_group_ids := {g.id | some g in input.entity.fields["requested-reviewer-groups"].value}

	count(requested_user_ids) + count(requested_group_ids) > 0

	print("[accept_gate] requested_users=", count(requested_user_ids),
	      "accepting_users=", count(_accepting_user_ids),
	      "missing_users=", count(requested_user_ids - _accepting_user_ids),
	      "requested_groups=", count(requested_group_ids),
	      "accepting_groups=", count(_accepting_group_ids),
	      "missing_groups=", count(requested_group_ids - _accepting_group_ids))

	# Every requested user must appear in the accepting set.
	count(requested_user_ids - _accepting_user_ids) == 0

	# Every requested group must have at least one accepting member.
	count(requested_group_ids - _accepting_group_ids) == 0

	print("[accept_gate] PASS all reviewers accepted")
}

# IDs of users who have cast any non-open vote (i.e., have actually reviewed).
_voted_user_ids := {r.fields.author.value.id |
	some r in input.entity.children.reviews
	r.fields.vote.value != null
	r.fields.vote.value != "open"
}

# IDs of requested groups for which at least one member has cast a non-open vote.
_voted_group_ids := {rg.id |
	some rg in input.entity.fields["requested-reviewer-groups"].value
	some member in rg.members
	some r in input.entity.children.reviews
	r.fields.author.value.id == member.id
	r.fields.vote.value != null
	r.fields.vote.value != "open"
}

# ─── error_messages ────────────────────────────────────────────────────────────
# Evaluated before allow; critical-level entries block save/transition.

error_messages contains msg if {
	input.action == "save"
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
}

error_messages contains msg if {
	input.action == "save"
	is_owner_or_editor
	not is_superuser_sudo
	not is_moderator
	not _reviewer_save_permitted
	not current_status in EDITABLE_STATUSES
	print("[block:status-edit] user=", input.user.username,
	      "status=", current_status, "not in editable statuses:", EDITABLE_STATUSES)
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
	not is_superuser_sudo
	some op in input.changed_fields.reviews.value
	op.op in {"update", "delete"}
	some existing in input.old_entity.children.reviews
	existing.id == op.id
	existing.fields.author.value.id != input.user.id
	print("[block:review-modify] user=", input.user.username,
	      "op=", op.op, "review_author=", existing.fields.author.value.id)
	msg := {
		"level": "critical",
		"text": "You can only modify your own reviews.",
		"field_slug": "reviews",
	}
}

# Block changing the author field on an existing review.
error_messages contains msg if {
	input.action == "save"
	not is_superuser_sudo
	some op in input.changed_fields.reviews.value
	op.op == "update"
	op.fields.author
	print("[block:review-author-change] user=", input.user.username,
	      "attempted to change author on review op=", op)
	msg := {
		"level": "critical",
		"text": "The review author cannot be changed after creation.",
		"field_slug": "reviews",
	}
}

# Block creating a review attributed to someone other than the current user.
error_messages contains msg if {
	input.action == "save"
	not is_superuser_sudo
	some op in input.changed_fields.reviews.value
	op.op == "create"
	op.fields.author != null
	op.fields.author != input.user.id
	print("[block:review-attribution] user=", input.user.username,
	      "tried to create review as=", op.fields.author)
	msg := {
		"level": "critical",
		"text": "You can only create reviews as yourself.",
		"field_slug": "reviews",
	}
}

# Block non-reviewers (including moderators not in the reviewer lists) from creating reviews.
error_messages contains msg if {
	input.action == "save"
	not is_superuser_sudo
	not is_reviewer
	some op in input.changed_fields.reviews.value
	op.op == "create"
	print("[block:review-not-reviewer] user=", input.user.username,
	      "is not a designated reviewer, status=", current_status)
	msg := {
		"level": "critical",
		"text": "Only designated reviewers may add reviews.",
		"field_slug": "reviews",
	}
}

_changing_reviewer_assignments if { input.changed_fields["requested-reviewer-groups"] }
_changing_reviewer_assignments if { input.changed_fields["requested-reviewer-users"] }

error_messages contains msg if {
	input.action == "save"
	not is_moderator
	not is_superuser_sudo
	_changing_reviewer_assignments
	print("[block:reviewer-assignment] user=", input.user.username,
	      "is not a moderator, changed_fields=", input.changed_fields)
	msg := {
		"level": "critical",
		"text": "Only moderators may change reviewer assignments.",
		"field_slug": null,
	}
}

error_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition == "accept"
	is_moderator
	not all_reviews_accepted
	print("[block:accept] not all reviews accepted, user=", input.user.username,
	      "accepting_user_ids=", _accepting_user_ids,
	      "accepting_group_ids=", _accepting_group_ids)
	msg := {
		"level": "error",
		"text": "Acceptance requires all requested reviewers to have voted accept.",
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
_proposal_ctx if { input.action in {"view", "save"} }
_proposal_ctx if { input.action == "transition"; input.field == "status" }

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

# ── View: what the owner/editor can do next ──
success_messages contains msg if {
	input.action == "view"
	is_owner_or_editor
	current_status == "draft"
	msg := {
		"level": "info",
		"text": "This proposal is a draft. Fill in the required fields and submit it when ready.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "view"
	is_owner_or_editor
	current_status == "submitted"
	msg := {
		"level": "info",
		"text": "This proposal is under review. You cannot edit it until a revision is requested.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "view"
	is_owner_or_editor
	current_status == "revise"
	msg := {
		"level": "warning",
		"text": "Revision has been requested. Update the proposal and resubmit.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "view"
	is_owner_or_editor
	current_status == "accepted"
	msg := {
		"level": "info",
		"text": "This proposal has been accepted.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "view"
	is_owner_or_editor
	current_status == "rejected"
	msg := {
		"level": "warning",
		"text": "This proposal has been rejected.",
		"field_slug": null,
	}
}

# ── View/Save/Transition: pending reviews summary for moderator ──
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	pending_count := count([r |
		some r in input.entity.children.reviews
		r.fields.vote.value != "accept"
	])
	pending_count > 0
	msg := {
		"level": "info",
		"text": sprintf("%v review(s) have not yet voted accept.", [pending_count]),
		"field_slug": "reviews",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	all_reviews_accepted
	msg := {
		"level": "info",
		"text": "All requested reviewers have voted accept. You may accept this proposal.",
		"field_slug": "reviews",
	}
}

# ── Per-reviewer status breakdown ──

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some u in input.entity.fields["requested-reviewer-users"].value
	not u.id in _voted_user_ids
	msg := {
		"level": "warning",
		"text": sprintf("Pending review: %v has not yet voted.", [u.username]),
		"field_slug": "requested-reviewer-users",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some u in input.entity.fields["requested-reviewer-users"].value
	u.id in _voted_user_ids
	msg := {
		"level": "info",
		"text": sprintf("Review submitted: %v has voted.", [u.username]),
		"field_slug": "requested-reviewer-users",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some g in input.entity.fields["requested-reviewer-groups"].value
	not g.id in _voted_group_ids
	msg := {
		"level": "warning",
		"text": sprintf("Pending review: no member of '%v' has voted yet.", [g.name]),
		"field_slug": "requested-reviewer-groups",
	}
}

success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	some g in input.entity.fields["requested-reviewer-groups"].value
	g.id in _voted_group_ids
	msg := {
		"level": "info",
		"text": sprintf("Review submitted: a member of '%v' has voted.", [g.name]),
		"field_slug": "requested-reviewer-groups",
	}
}

# ── View/Save/Transition: per-transition allow/deny (shown below the transition buttons) ─

# owner/editor in draft: submit is available
success_messages contains msg if {
	_proposal_ctx
	is_owner_or_editor
	current_status == "draft"
	msg := {
		"level": "info",
		"text": "↑ submit: you may submit this proposal for moderator review.",
		"field_slug": "status",
	}
}

# owner/editor in revise: resubmit is available
success_messages contains msg if {
	_proposal_ctx
	is_owner_or_editor
	current_status == "revise"
	msg := {
		"level": "info",
		"text": "↑ resubmit: update the proposal and resubmit for review.",
		"field_slug": "status",
	}
}

# moderator, submitted, accept allowed
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	all_reviews_accepted
	msg := {
		"level": "info",
		"text": "↑ accept: all requested reviewers have voted accept.",
		"field_slug": "status",
	}
}

# moderator, submitted, accept blocked — show how many are missing per type
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	not all_reviews_accepted
	missing_users := count({u.id | some u in input.entity.fields["requested-reviewer-users"].value} - _accepting_user_ids)
	missing_groups := count({g.id | some g in input.entity.fields["requested-reviewer-groups"].value} - _accepting_group_ids)
	msg := {
		"level": "warning",
		"text": sprintf("↑ accept: blocked — %v direct reviewer(s) and %v group(s) have not yet accepted.", [missing_users, missing_groups]),
		"field_slug": "status",
	}
}

# moderator, submitted: reject and request-revision are always available
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	msg := {
		"level": "info",
		"text": "↑ reject / request-revision: available.",
		"field_slug": "status",
	}
}

# moderator, rejected: allow-revision is available
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "rejected"
	msg := {
		"level": "info",
		"text": "↑ allow-revision: return this proposal to the owner for changes.",
		"field_slug": "status",
	}
}

# reviewer (not moderator, not owner/editor): no transitions available — explain why
success_messages contains msg if {
	_proposal_ctx
	is_reviewer
	not is_moderator
	not is_owner_or_editor
	msg := {
		"level": "info",
		"text": "No transitions available for reviewers. Submit your review and the moderator will decide.",
		"field_slug": "status",
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

# ─── messages (engine reads this: union of error and success messages) ──────────
messages contains msg if { some msg in error_messages }
messages contains msg if { some msg in success_messages }

# ─── viewable_fields ───────────────────────────────────────────────────────────
viewable_fields := [f | some f in _viewable_set]

_viewable_set contains f if { is_superuser_sudo; input.entity.fields[f] }

_viewable_set contains f if {
	is_moderator
	current_status != "draft"
	input.entity.fields[f]
}

# Moderators always see reviewer fields, even in draft or when also owner/editor.
_viewable_set contains "reviews" if { is_moderator; input.entity.fields["reviews"] }
_viewable_set contains "requested-reviewer-groups" if { is_moderator; input.entity.fields["requested-reviewer-groups"] }
_viewable_set contains "requested-reviewer-users" if { is_moderator; input.entity.fields["requested-reviewer-users"] }

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

_editable_set contains f if { is_superuser_sudo; input.entity.fields[f] }

# Owner/editors can edit content fields; reviewer assignment is moderator-only.
_editable_set contains f if {
	is_owner_or_editor
	current_status in EDITABLE_STATUSES
	input.entity.fields[f]
	not f in {"owner", "proposal-id", "requested-reviewer-groups", "requested-reviewer-users"}
}

# Moderators can manage the reviewer assignment fields.
_editable_set contains "requested-reviewer-groups" if { is_moderator }
_editable_set contains "requested-reviewer-users" if { is_moderator }

# Reviewers can add/update their own review submodel while the proposal is submitted.
# The author field within a review is set automatically and blocked from editing above.
_editable_set contains "reviews" if {
	is_reviewer
	current_status == "submitted"
}

# ─── Utilities ─────────────────────────────────────────────────────────────────
no_critical_errors if { not any_critical_error }

any_critical_error if {
	some m in error_messages
	m.level == "critical"
}

# True when the engine is doing a dry-run (validate_only=true from the API).
_is_validation if { input.validate_only == true }

# ─── Submission checklist ───────────────────────────────────────────────────────
# Warning-level messages shown to owner/editor during view/save that flag fields
# not yet ready for submission.  Level "warning" never blocks save — only
# "critical" entries do (see no_critical_errors above).

_checklist_ctx if { _proposal_ctx; is_owner_or_editor }

# title: 1–30 non-empty characters
_title_complete if {
	v := input.entity.fields.title.value
	print("[check:title] value=", v)
	v != null
	count(trim_space(v)) >= 1
	count(v) <= 30
	print("[check:title] PASS len=", count(v))
}
error_messages contains msg if {
	_checklist_ctx
	not _title_complete
	print("[checklist:title] FAIL title=", input.entity.fields.title.value)
	msg := {"level": "warning", "text": "Title is required (1–30 characters).", "field_slug": "title"}
}

# abstract: 50–250 characters
_abstract_complete if {
	v := input.entity.fields.abstract.value
	print("[check:abstract] value_len=", count(v) if v != null else 0)
	v != null
	count(v) >= 50
	count(v) <= 250
	print("[check:abstract] PASS len=", count(v))
}
error_messages contains msg if {
	_checklist_ctx
	not _abstract_complete
	print("[checklist:abstract] FAIL len=", count(input.entity.fields.abstract.value) if input.entity.fields.abstract.value != null else 0)
	msg := {"level": "warning", "text": "Abstract must be 50–250 characters.", "field_slug": "abstract"}
}

# description: 50–1000 characters
_description_complete if {
	v := input.entity.fields.description.value
	print("[check:description] value_len=", count(v) if v != null else 0)
	v != null
	count(v) >= 50
	count(v) <= 1000
	print("[check:description] PASS len=", count(v))
}
error_messages contains msg if {
	_checklist_ctx
	not _description_complete
	print("[checklist:description] FAIL len=", count(input.entity.fields.description.value) if input.entity.fields.description.value != null else 0)
	msg := {"level": "warning", "text": "Description must be 50–1000 characters.", "field_slug": "description"}
}

# duration: duration-days >= 1 and duration-time-per-day parses to > 0 minutes
_duration_complete if {
	days := input.entity.fields["duration-days"].value
	t := input.entity.fields["duration-time-per-day"].value
	print("[check:duration] days=", days, "time=", t)
	days != null
	days >= 1
	t != null
	parts := split(t, ":")
	count(parts) == 2
	total_min := (to_number(parts[0]) * 60) + to_number(parts[1])
	total_min > 0
	print("[check:duration] PASS total_min=", total_min)
}
error_messages contains msg if {
	_checklist_ctx
	not _duration_complete
	print("[checklist:duration] FAIL days=", input.entity.fields["duration-days"].value,
	      "time=", input.entity.fields["duration-time-per-day"].value)
	msg := {"level": "warning", "text": "Duration must be at least 1 day with a non-zero time per day (HH:MM).", "field_slug": "duration-days"}
}

# max-participants: >= 1
_max_participants_complete if {
	v := input.entity.fields["max-participants"].value
	print("[check:max-participants] value=", v)
	v != null
	v >= 1
	print("[check:max-participants] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _max_participants_complete
	print("[checklist:max-participants] FAIL value=", input.entity.fields["max-participants"].value)
	msg := {"level": "warning", "text": "Maximum number of participants must be at least 1.", "field_slug": "max-participants"}
}

# occurrence-count: >= 1
_occurrence_count_complete if {
	v := input.entity.fields["occurrence-count"].value
	print("[check:occurrence-count] value=", v)
	v != null
	v >= 1
	print("[check:occurrence-count] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _occurrence_count_complete
	print("[checklist:occurrence-count] FAIL value=", input.entity.fields["occurrence-count"].value)
	msg := {"level": "warning", "text": "Occurrence count must be at least 1.", "field_slug": "occurrence-count"}
}

# preferred-dates: non-empty text
_preferred_dates_complete if {
	v := input.entity.fields["preferred-dates"].value
	print("[check:preferred-dates] value=", v)
	v != null
	count(trim_space(v)) >= 1
	print("[check:preferred-dates] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _preferred_dates_complete
	print("[checklist:preferred-dates] FAIL value=", input.entity.fields["preferred-dates"].value)
	msg := {"level": "warning", "text": "Please specify your preferred dates.", "field_slug": "preferred-dates"}
}

# language: a choice has been selected
_language_complete if {
	v := input.entity.fields.language.value
	print("[check:language] value=", v)
	v != null
	count(v) > 0
	print("[check:language] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _language_complete
	print("[checklist:language] FAIL value=", input.entity.fields.language.value)
	msg := {"level": "warning", "text": "Please select a language.", "field_slug": "language"}
}

# submission-type: a choice has been selected
_submission_type_complete if {
	v := input.entity.fields["submission-type"].value
	print("[check:submission-type] value=", v)
	v != null
	count(v) > 0
	print("[check:submission-type] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _submission_type_complete
	print("[checklist:submission-type] FAIL value=", input.entity.fields["submission-type"].value)
	msg := {"level": "warning", "text": "Please select a submission type.", "field_slug": "submission-type"}
}

# area: a choice has been selected
_area_complete if {
	v := input.entity.fields.area.value
	print("[check:area] value=", v)
	v != null
	count(v) > 0
	print("[check:area] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _area_complete
	print("[checklist:area] FAIL value=", input.entity.fields.area.value)
	msg := {"level": "warning", "text": "Please select a workshop area.", "field_slug": "area"}
}

# photo: an image has been uploaded
_photo_complete if {
	print("[check:photo] value=", input.entity.fields.photo.value)
	input.entity.fields.photo.value != null
	print("[check:photo] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _photo_complete
	print("[checklist:photo] FAIL no image uploaded")
	msg := {"level": "warning", "text": "Please upload a proposal image (min. 1440×1080 px).", "field_slug": "photo"}
}

# photo-copyright-consent: uploading a new (non-null) image requires the
# copyright checkbox to be ticked.  Clearing the image (value → null) is
# always permitted so authors can remove an image without re-ticking.
error_messages contains msg if {
	input.action == "save"
	is_owner_or_editor
	not is_superuser_sudo
	"photo" in input.changed_fields
	print("[copyright] photo in changed_fields, post-write photo=", input.entity.fields.photo.value,
	      "consent=", input.entity.fields["photo-copyright-consent"].value,
	      "user=", input.user.username)
	input.entity.fields.photo.value != null
	not input.entity.fields["photo-copyright-consent"].value == true
	print("[copyright] BLOCK: upload without consent")
	msg := {
		"level": "critical",
		"text": "You must confirm copyright consent before uploading an image.",
		"field_slug": "photo-copyright-consent",
	}
}

# submission deadline: _deadline is defined in description.rego (same package)
_within_deadline if {
	print("[check:deadline] now_ns=", time.now_ns(), "deadline=", _deadline,
	      "deadline_ns=", time.parse_rfc3339_ns(_deadline))
	time.now_ns() <= time.parse_rfc3339_ns(_deadline)
	print("[check:deadline] PASS still within deadline")
}
error_messages contains msg if {
	_checklist_ctx
	current_status == "draft"
	not _within_deadline
	print("[checklist:deadline] FAIL deadline has passed, deadline=", _deadline)
	msg := {"level": "warning", "text": "The submission deadline has passed.", "field_slug": null}
}

# speakers: at least one speaker submodel must exist
_at_least_one_speaker if {
	print("[check:speakers] count=", count(input.entity.children.speakers))
	count(input.entity.children.speakers) >= 1
	print("[check:speakers] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _at_least_one_speaker
	print("[checklist:speakers] FAIL no speakers, count=", count(input.entity.children.speakers))
	msg := {"level": "warning", "text": "At least one speaker must be added.", "field_slug": "speakers"}
}

# speakersHaveBio: every speaker must have a non-empty biography
_all_speakers_have_bio if {
	print("[check:speakers-bio] checking", count(input.entity.children.speakers), "speakers")
	every s in input.entity.children.speakers {
		v := s.fields.biography.value
		v != null
		count(trim_space(v)) > 0
	}
	print("[check:speakers-bio] PASS all speakers have biography")
}
error_messages contains msg if {
	_checklist_ctx
	count(input.entity.children.speakers) > 0
	not _all_speakers_have_bio
	print("[checklist:speakers-bio] FAIL some speakers missing biography")
	msg := {"level": "warning", "text": "All speakers must have a biography.", "field_slug": "speakers"}
}
