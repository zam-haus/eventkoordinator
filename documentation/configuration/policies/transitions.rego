package udm.udmframeworkv1.modules.transitions

import data.udm.udmframeworkv1.modules.proposals._can_view
import data.udm.udmframeworkv1.modules.proposals._proposal_ctx
import data.udm.udmframeworkv1.modules.proposals.current_status
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.reviews
import data.udm.udmframeworkv1.modules.reviews._accepting_group_ids
import data.udm.udmframeworkv1.modules.reviews._accepting_user_ids
import data.udm.udmframeworkv1.modules.reviews._reviews
import data.udm.udmframeworkv1.modules.reviews.all_reviews_accepted
import data.udm.udmframeworkv1.modules.validation_rules._checklist_complete
import rego.v1

default allow := false

# ─── Shared authorization predicates ─────────────────────────────────────────
# ONE predicate authorizes both the real transition (input.transition_descriptor)
# and the preview matrix (input.candidate_transitions), so they cannot diverge.
# `_` is the descriptor — match on descriptor.properties/to_state where the
# workflow configures them; the demo workflow is matched by name.

_transition_permitted(node_id, field_slug, name, _) if {
	node_id == input.entity.id
	field_slug == "status"
	name in {"submit", "resubmit"}
	roles.is_owner_or_editor
	_checklist_complete
}

_transition_permitted(node_id, field_slug, name, _) if {
	node_id == input.entity.id
	field_slug == "status"
	name in {"reject", "request-revision", "allow-revision"}
	roles.is_moderator
}

_transition_permitted(node_id, field_slug, name, _) if {
	node_id == input.entity.id
	field_slug == "status"
	name == "accept"
	roles.is_moderator
	reviews.all_reviews_accepted
}

# ─── allow: transition (real execution) ───────────────────────────────────────
allow if {
	input.action == "transition"
	_transition_permitted(input.node_id, input.field, input.transition, input.transition_descriptor)
	print(
		"[allow:transition] user=", input.user.username,
		"transition=", input.transition, "status=", current_status,
	)
}

# ─── valid_transitions: preview matrix (§4, single evaluation) ────────────────
valid_transitions contains {"node": node_id, "field": field_slug, "name": name} if {
	some node_id, wf_fields in input.candidate_transitions
	some field_slug, wf in wf_fields
	some name, descriptor in wf.transitions
	_transition_permitted(node_id, field_slug, name, descriptor)
}

# ── View/Save/Transition: pending reviews summary for moderator ──
success_messages contains msg if {
	_proposal_ctx
	roles.is_moderator
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
	roles.is_moderator
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
	roles.is_moderator
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
	roles.is_moderator
	_can_view
	not reviews.all_reviews_accepted
	print(
		"[block:accept] not all reviews accepted, user=", input.user.username,
		"accepting_user_ids=", _accepting_user_ids,
		"accepting_group_ids=", _accepting_group_ids,
	)
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
	roles.is_owner_or_editor
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
	roles.is_owner_or_editor
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
	roles.is_moderator
	current_status == "submitted"
	not all_reviews_accepted
	missing_users := count({u | some u in reviews._reviewer_users} - _accepting_user_ids)
	missing_groups := count({g | some g in reviews._reviewer_groups} - _accepting_group_ids)
	msg := {
		"level": "warning",
		"text": sprintf("↑ accept: blocked — %v direct reviewer(s) and %v group(s) have not yet accepted.", [missing_users, missing_groups]),
		"field_slug": "status",
	}
}

# moderator, submitted: reject and request-revision are always available
success_messages contains msg if {
	_proposal_ctx
	roles.is_moderator
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
	roles.is_moderator
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
	roles.is_reviewer
	not roles.is_moderator
	not roles.is_owner_or_editor
	msg := {
		"level": "info",
		"text": "No transitions available for reviewers. Submit your review and the moderator will decide.",
		"field_slug": "status",
	}
}

error_messages contains msg if {
	input.action == "transition"
	input.transition in {"submit", "resubmit"}
	roles.is_owner_or_editor
	not _checklist_complete
	print(
		"[block:submit] checklist incomplete, user=", input.user.username,
		"transition=", input.transition,
	)
	msg := {
		"level": "error",
		"text": "All required fields must be completed before submitting.",
		"field_slug": null,
	}
}
