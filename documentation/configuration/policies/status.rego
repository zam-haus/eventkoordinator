package udm.udmframeworkv1.modules.status

import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.utils
import data.udm.udmframeworkv1.modules.workflow
import rego.v1

# ─── Status-dependent editability of proposal content ─────────────────────────

_reviewer_save_permitted if {
	roles.is_reviewer
	workflow.current_status == "submitted"
	print("[role] _reviewer_save_permitted user=", input.user.username)
}

error_messages contains msg if {
	input.action in {"save", "preview"}
	roles.is_owner_or_editor
	not roles.is_moderator
	not _reviewer_save_permitted
	not workflow.is_status_editable
	print(
		"[block:status-edit] user=", input.user.username,
		"status=", workflow.current_status, "not in editable statuses",
	)
	msg := {
		"level": "critical",
		"text": "Proposals can only be edited in draft or revise status.",
		"field_slug": null,
	}
}

# ─── success_messages: save confirmation per status ────────────────────────────
# Does NOT reference `allow` — the conditions here imply allow is true for each
# case, avoiding a messages→allow→no_critical_errors→error_messages scheduling
# cycle that confuses regorus's rule scheduler.

success_messages contains msg if {
	input.action == "save"
	not utils.is_preview
	roles.is_owner_or_editor
	workflow.current_status == "draft"
	msg := {
		"level": "info",
		"text": "Draft saved. Submit the proposal when all required fields are complete.",
		"field_slug": null,
	}
}

success_messages contains msg if {
	input.action == "save"
	not utils.is_preview
	roles.is_owner_or_editor
	workflow.current_status == "revise"
	msg := {
		"level": "info",
		"text": "Revisions saved. Resubmit the proposal when ready.",
		"field_slug": null,
	}
}
