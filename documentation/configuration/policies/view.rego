package udm.udmframeworkv1.modules.view

import data.udmtree
import data.udm.udmframeworkv1.modules.config
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.sudo
import data.udm.udmframeworkv1.modules.workflow
import rego.v1

# ─── can_view ──────────────────────────────────────────────────────────────────
# Single source of truth for "may this user view the entity" (replaces the old
# proposals._can_view, which duplicated the allow rules below).
#   view/browse            → the local allow rules
#   save/preview/transition → the Python-side VIEW pre-check, carried over as
#                             input.additional_result.view_allowed (udm.rego)
# Does not reference allow/no_critical_errors of data.udm, so there is no
# cyclic dependency.

can_view if allow

can_view if {
	input.additional_result.view_allowed == true
	print("[can_view] preflight view_allowed user=", input.user.username)
}

can_view if {
	sudo.is_superuser_sudo
	print("[can_view] sudo user=", input.user.username)
}

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

# ─── viewable_fields (per node — whole tree, one pass) ────────────────────────
# Default grant: every field of every node in the tree, minus the protected
# root fields (modules re-grant those explicitly, e.g. reviews.rego).

viewable_fields contains {"node": node.id, "field": f} if {
	some node in udmtree.tree_nodes
	some f, _ in node.fields
	not _protected(node, f)
}

_protected(node, f) if {
	node.id == input.entity.id
	f in config.PROTECTED_FIELDS
}

# ─── save/transition denial for non-viewers ────────────────────────────────────
# Produces a single generic critical message when the user cannot view the entity
# and attempts a save or transition.  This replaces the Python-level "Save denied
# by policy." fallback and prevents any other detailed error_messages from leaking
# state information to someone who has no view access.

error_messages contains msg if {
	input.action in {"save", "transition", "preview"}
	not can_view
	print(
		"[deny:save/transition] no view access user=", input.user.username,
		"action=", input.action, "status=", workflow.current_status,
	)
	msg := {
		"level": "critical",
		"text": "Access denied.",
		"field_slug": null,
	}
}

# ─── view-denial messages ──────────────────────────────────────────────────────
# All denial messages are gated on not can_view, which ensures they only fire
# when the user truly has no path to view access — regardless of how many roles
# they hold simultaneously.

# True when the user holds a role that would grant view access post-draft.
_has_limited_role if {
	roles.is_moderator
	print("[role] _has_limited_role (moderator) user=", input.user.username)
}

_has_limited_role if {
	roles.is_reviewer
	print("[role] _has_limited_role (reviewer) user=", input.user.username)
}

error_messages contains msg if {
	input.action == "view"
	not can_view
	_has_limited_role
	not roles.is_owner_or_editor
	print("[deny:view] draft-blocked limited-role user=", input.user.username, "status=", workflow.current_status)
	msg := {
		"level": "error",
		"text": "This proposal is in draft and not yet visible to moderators or reviewers.",
		"field_slug": null,
	}
}

error_messages contains msg if {
	input.action == "view"
	not can_view
	not _has_limited_role
	not roles.is_owner_or_editor
	print("[deny:view] no-role user=", input.user.username, "status=", workflow.current_status)
	msg := {
		"level": "error",
		"text": "You do not have permission to view this proposal.",
		"field_slug": null,
	}
}
