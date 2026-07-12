package udm.udmframeworkv1.modules.view

import data.udmtree
import data.udm.udmframeworkv1.modules.config
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
