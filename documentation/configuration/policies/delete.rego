package udm.udmframeworkv1.modules.delete

import data.udm
import data.udm.udmframeworkv1.modules.roles
import data.udm.udmframeworkv1.modules.view
import data.udm.udmframeworkv1.modules.workflow

import rego.v1

# ─── Delete policy ───────────────────────────────────────────────────────────────

# ─── allow ─────────────────────────────────────────────────────────────────────
default allow := false

allow if {
	input.action == "delete"
	roles.is_owner
	workflow.current_status == "draft"
	print("[delete:allow] owner")
}
