package udm.udmframeworkv1.modules.delete

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

# ─── force_delete (events-and-sync.md §1.1) ───────────────────────────────────
# The engine already refuses deletion at the application level while
# input.backlink_summary.count > 0 (a normal `allow` above is not enough to
# delete a referenced entity). A module opts back into deleting despite
# backlinks by granting force_delete for the SAME action — here restricted to
# sudo, since a forced delete leaves the referencing ids dangling (they read
# back as null, never re-linked).
default force_delete := false

force_delete if {
	input.action == "delete"
	input.backlink_summary.count > 0
	input.user.sudo
	print("[delete:force_delete] sudo override, ", input.backlink_summary.count, " backlink(s) will dangle")
}
