package udm.udmframeworkv1.modules.create

import data.udm.udmframeworkv1.modules.config._deadline
import rego.v1

# ─── allow: create ─────────────────────────────────────────────────────────────
# Any active logged-in user may create a new proposal before the deadline.

default allow := false

allow if {
	input.action == "create"
	input.user.is_active
	print(
		"[allow:create] checking deadline for user=", input.user.username,
		"now_ns=", time.now_ns(), "deadline=", _deadline,
	)
	time.now_ns() <= time.parse_rfc3339_ns(_deadline)
	print("[allow:create] deadline ok, user=", input.user.username)
}
