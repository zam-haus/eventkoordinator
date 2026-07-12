package udm.udmframeworkv1.modules.messages

import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.utils._proposal_ctx
import data.udm.udmframeworkv1.modules.workflow
import data.udm.udmframeworkv1.modules.workflow.current_status
import rego.v1

# ─── Proposal context messages ─────────────────────────────────────────────────
# Informational messages shown alongside allowed actions (status label and the
# current user's role).  Lives in its own module so roles.rego and workflow.rego
# stay pure predicate/constant modules without importing each other.
# Does NOT reference `allow` — the conditions here imply allow is true for each
# case, avoiding a messages→allow→no_critical_errors→error_messages scheduling
# cycle that confuses regorus's rule scheduler.

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
