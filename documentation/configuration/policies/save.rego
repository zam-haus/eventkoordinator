package udm.udmframeworkv1.modules.save

import data.udm
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.timemachine
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
changed_fields := input.changed_fields

some_field_changed if {
	changed_fields
	some field in changed_fields
	print("[save:some_field_changed] field=", field)
}

field_was_editable[field] if {
	x := udm.editable_fields with input as timemachine.old_input
	#print("[save:field_was_editable] checking against", x)
	x[field]
	print("[save:field_was_editable] field ", field, " was editable")
}

every_field_changed_was_editable if {
	every field in object.keys(changed_fields) {
		print("[save:allow] checking field=", field, "against", field_was_editable)
		field_was_editable[field]
		print("[save:allow] field ", field, " is editable")
	}
} else := false if {
	some field in object.keys(changed_fields)
	not field_was_editable[field]
	print("[save:allow] some field ", field, " is not editable")
}

default allow := false

allow if {
	input.action == "save"
	print("[save:allow] save action")
	view.view_was_allowed
	some_field_changed
	every_field_changed_was_editable
	print("[save:allow] owner/editor user=", input.user.username, "status=", workflow.current_status, "changed_fields=", changed_fields, "editable_fields=", editable_fields)
}
