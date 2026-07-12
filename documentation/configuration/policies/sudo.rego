package udm.udmframeworkv1.modules.sudo

import rego.v1

# input.user.sudo is the session-scoped sudo toggle: a superuser enables it
# via the navbar user menu (POST /api/v1/user/sudo) and the backend passes it
# through on the requesting user document. It can only ever be true for
# superusers, but we re-check is_superuser here for defense in depth.
is_superuser_sudo if {
	input.user.sudo == true
	input.user.is_superuser
	print("[role] is_superuser_sudo user=", input.user.username)
}

# ── SUDO notice ──
success_messages contains msg if {
	is_superuser_sudo
	msg := {
		"level": "info",
		"text": "SUDO mode active: all restrictions bypassed.",
		"field_slug": null,
	}
}

# ──── Allow all actions for sudo users ───────────────────────────────────────────────

allow if {
	input.action == "browse"
	is_superuser_sudo
	print("[allow:browse] sudo user=", input.user.username)
}

allow if {
	input.action == "save"
	is_superuser_sudo
	print("[allow:save] sudo user=", input.user.username)
}

allow if {
	input.action == "delete"
	is_superuser_sudo
	print("[allow:delete] sudo user=", input.user.username)
}

allow if {
	input.action == "create"
	is_superuser_sudo
	print("[allow:create] sudo user=", input.user.username)
}

allow if {
	input.action == "transition"
	is_superuser_sudo
	print(
		"[allow:transition] sudo user=", input.user.username,
		"transition=", input.transition,
	)
}
