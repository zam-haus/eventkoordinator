package udm.udmframeworkv1.modules.view

import data.udm
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.workflow
import rego.v1

# ─── allow ─────────────────────────────────────────────────────────────────────
default allow := false

allow if {
	input.action in ["view", "browse"]
	roles.is_owner_or_editor
	print("[view:allow] owner/editor")
}

allow if {
	input.action in ["view", "browse"]
	roles.is_moderator
	workflow.is_status_post_draft
	print("[view:allow] moderator post-draft")
}

allow if {
	input.action in ["view", "browse"]
	roles.is_reviewer
	workflow.is_status_post_draft
	print("[view:allow] reviewer post-draft")
}

# ─── viewable_fields ───────────────────────────────────────────────────────────────

viewable_fields contains f if {
	input.entity.fields[f]
	not f in udm.protected_fields
}

old_entity := input.input.old_entity if input.old_entity

else := input.entity

view_was_allowed if {
	print("[view:was_allowed:ENTER]")
	allow with input.action as "view" with input.entity as old_entity
	print("[view:was_allowed:SUCCESS]")
} else := false if print("[view:was_allowed:FAIL]")
