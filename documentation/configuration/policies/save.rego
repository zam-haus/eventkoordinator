package udm.udmframeworkv1.modules.save

import data.udm
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.view
import data.udm.udmframeworkv1.modules.workflow

import rego.v1

# ─── Edit policy ───────────────────────────────────────────────────────────────

# Owner/editors can edit content fields; reviewer assignment is moderator-only.
editable_fields contains f if {
	roles.is_owner_or_editor
	workflow.is_status_editable
	input.entity.fields[f]
	not f in udm.protected_fields
}

# ─── allow ─────────────────────────────────────────────────────────────────────
default allow := false

allow if {
	input.action == "save"
	view.view_was_allowed
	changed_fields := input.changed_fields
	changed_fields != null
    some field in changed_fields
	every field in changed_fields {
		editable_fields[field]
	}
	print("[edit:allow] owner/editor user=", input.user.username, "status=", workflow.current_status, "changed_fields=", changed_fields, "editable_fields=", editable_fields)
}
