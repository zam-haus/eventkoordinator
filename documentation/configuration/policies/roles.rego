package udm.udmframeworkv1.modules.roles

import data.udm.udmframeworkv1.modules.config.MODERATOR_GROUP_NAMES
import rego.v1

# ─── Role helpers ──────────────────────────────────────────────────────────────
# Field values hold raw PKs (contract §3.2-7); resolve details via the
# input.users / input.groups lookup maps when needed.

is_owner if {
	owner_val := input.entity.fields.owner.value
	owner_val != null
	owner_val == input.user.id
	print("[role] is_owner user=", input.user.username)
}

is_editor if {
	editors := input.entity.fields.editors.value
	editors != null
	some ed in editors
	ed == input.user.id
	print("[role] is_editor user=", input.user.username)
}

is_owner_or_editor if is_owner

is_owner_or_editor if is_editor

is_moderator if {
	some group_name in MODERATOR_GROUP_NAMES
	some ug in input.user.groups
	ug.name == group_name
	print("[role] is_moderator user=", input.user.username, "group=", ug.name)
}

is_direct_reviewer if {
	reviewer_users := input.entity.fields["requested-reviewer-users"].value
	reviewer_users != null
	some ru in reviewer_users
	ru == input.user.id
	print("[role] is_direct_reviewer user=", input.user.username)
}

is_group_reviewer if {
	reviewer_groups := input.entity.fields["requested-reviewer-groups"].value
	reviewer_groups != null
	some rg in reviewer_groups
	some ug in input.user.groups
	rg == ug.id
	print("[role] is_group_reviewer user=", input.user.username, "group=", ug.name)
}

is_reviewer if is_direct_reviewer

is_reviewer if is_group_reviewer
