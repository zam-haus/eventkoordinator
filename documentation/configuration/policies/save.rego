package udm.udmframeworkv1.modules.save

import data.udmtree
import data.udm.udmframeworkv1.modules.config
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.workflow

import rego.v1

# ─── Edit policy (per node — whole tree, one pass) ────────────────────────────

# Owner/editors can edit content fields of every node while the status allows
# editing; protected root fields (reviewer assignment etc.) are excluded and
# re-granted by their owning modules.
editable_fields contains {"node": node.id, "field": f} if {
	roles.is_owner_or_editor
	workflow.is_status_editable
	some node in udmtree.tree_nodes
	some f, entry in node.fields
	not entry.data_type in config.STRUCTURAL_TYPES # clickability: structural.rego
	not _protected(node, f)
}

_protected(node, f) if {
	node.id == input.entity.id
	f in config.PROTECTED_FIELDS
}

# ─── allow ─────────────────────────────────────────────────────────────────────
changed_fields := input.changed_fields

some_field_changed if {
	changed_fields
	some field in changed_fields
	print("[save:some_field_changed] field=", field)
}

# input.additional_result is the VIEW pre-check carry-over computed by the
# engine against the PERSISTED (pre-patch) entity: view_allowed plus the
# per-node editable grant that held before this save.
field_was_editable(field) if {
	{"node": input.entity.id, "field": field} in input.additional_result.editable
	print("[save:field_was_editable] field ", field, " was editable")
}

every_field_changed_was_editable if {
	every field in object.keys(changed_fields) {
		field_was_editable(field)
	}
} else := false if {
	some field in object.keys(changed_fields)
	not field_was_editable(field)
	print("[save:allow] some field ", field, " is not editable")
}

default allow := false

allow if {
	input.action == "save"
	input.additional_result.view_allowed == true
	some_field_changed
	every_field_changed_was_editable
	print("[save:allow] user=", input.user.username, "status=", workflow.current_status, "changed_fields=", changed_fields)
}

# Preview: the save button asks "would a save of these pending edits pass?".
# Same predicate chain; an empty changed_fields set previews as saveable.
allow if {
	input.action == "preview"
	input.additional_result.view_allowed == true
	every_field_changed_was_editable
	print("[save:allow] preview user=", input.user.username)
}
