package udm.udmframeworkv1.modules.proposal_id

import data.udm.udmframeworkv1.modules.roles
import rego.v1

protected_fields := ["proposal-id", "owner", "editors"]

viewable_fields := protected_fields

editable_fields contains field_slug if {
	field_slug := "editors"
	roles.is_owner_or_editor
}

editable_fields contains field_slug if {
	field_slug := "owner"
	roles.superuser_sudo
}

editable_fields contains field_slug if {
	field_slug := "proposal-id"
	roles.superuser_sudo
}

error_messages contains msg if {
	input.action == "save"
	input.changed_fields["proposal-id"]
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
	print("[block:proposal-id] user=", input.user.username, "attempted to change proposal-id")
	msg := {
		"level": "critical",
		"text": "The proposal owner cannot be changed.",
		"field_slug": "owner",
	}
}
