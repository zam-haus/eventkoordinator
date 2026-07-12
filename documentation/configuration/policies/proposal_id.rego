package udm.udmframeworkv1.modules.proposal_id

import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.sudo
import data.udm.udmframeworkv1.modules.view
import rego.v1

protected_fields := ["proposal-id", "owner", "editors"]

viewable_fields contains {"node": input.entity.id, "field": f} if {
	some f in protected_fields
}

editable_fields contains {"node": input.entity.id, "field": "editors"} if {
	roles.is_owner_or_editor
}

editable_fields contains {"node": input.entity.id, "field": "owner"} if {
	sudo.is_superuser_sudo
}

editable_fields contains {"node": input.entity.id, "field": "proposal-id"} if {
	sudo.is_superuser_sudo
}

error_messages contains msg if {
	input.action in {"save", "preview"}
	input.changed_fields["proposal-id"]
	print("[block:proposal-id] user=", input.user.username, "attempted to change proposal-id")
	msg := {
		"level": "critical",
		"text": "The proposal ID cannot be changed.",
		"field_slug": "proposal-id",
	}
}

# Gated on can_view so no field-level detail leaks to users without view
# access (view.rego already emits the generic "Access denied." for them).
error_messages contains msg if {
	input.action in {"save", "preview"}
	view.can_view
	input.changed_fields.owner
	print("[block:owner] user=", input.user.username, "attempted to change owner")
	msg := {
		"level": "critical",
		"text": "The owner cannot be changed.",
		"field_slug": "owner",
	}
}
