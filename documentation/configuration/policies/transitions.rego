package udm.udmframeworkv1.transitions

import rego.v1
import data.udm.udmframeworkv1.reviews
import data.udm.udmframeworkv1.reviews._accepting_user_ids
import data.udm.udmframeworkv1.reviews._accepting_group_ids
import data.udm.udmframeworkv1.reviews._reviews
import data.udm.udmframeworkv1.reviews.all_reviews_accepted
import data.udm.udmframeworkv1.proposals._proposal_ctx
import data.udm.udmframeworkv1.proposals.is_moderator
import data.udm.udmframeworkv1.proposals.is_owner_or_editor
import data.udm.udmframeworkv1.proposals.is_reviewer
import data.udm.udmframeworkv1.proposals.is_superuser_sudo
import data.udm.udmframeworkv1.proposals.current_status
import data.udm.udmframeworkv1.proposals._can_view
import data.udm.udmframeworkv1.validation_rules._checklist_complete

default allow := false

# ─── allow: transition ─────────────────────────────────────────────────────────
allow if {
	input.action == "transition"
	input.transition in {"submit", "resubmit"}
	is_owner_or_editor
	_checklist_complete
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
	reviews.all_reviews_accepted
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
	some r in _reviews
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

# ── View/Save/Transition: pending reviews summary for moderator ──
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	pending_count := count([r |
		some r in _reviews
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
	reviews.all_reviews_accepted
	msg := {
		"level": "info",
		"text": "All requested reviewers have voted accept. You may accept this proposal.",
		"field_slug": "reviews",
	}
}


# moderator, submitted, accept allowed
success_messages contains msg if {
	_proposal_ctx
	is_moderator
	current_status == "submitted"
	reviews.all_reviews_accepted
	msg := {
		"level": "info",
		"text": "↑ accept: all requested reviewers have voted accept.",
		"field_slug": "status",
	}
}


error_messages contains msg if {
	input.action == "transition"
	input.field == "status"
	input.transition == "accept"
	is_moderator
	_can_view
	not reviews.all_reviews_accepted
	print("[block:accept] not all reviews accepted, user=", input.user.username,
	      "accepting_user_ids=", _accepting_user_ids,
	      "accepting_group_ids=", _accepting_group_ids)
	msg := {
		"level": "error",
		"text": "Acceptance requires all requested reviewers to have voted accept.",
		"field_slug": null,
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
