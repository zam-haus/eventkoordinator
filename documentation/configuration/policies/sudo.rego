package udm.udmframeworkv1.modules.sudo

import rego.v1
import data.udm.udmframeworkv1.modules.roles

SUDO_ACTIVE := false

is_superuser_sudo if {
    SUDO_ACTIVE
	input.user.is_superuser
	print("[role] is_superuser_sudo user=", input.user.username)
}


# ── SUDO notice ──
success_messages contains msg if {
	roles.is_superuser_sudo
	msg := {
		"level": "info",
		"text": "SUDO mode active: all restrictions bypassed.",
		"field_slug": null,
	}
}

# ──── Allow all actions for sudo users ───────────────────────────────────────────────

allow if {
	input.action == "browse"
	roles.is_superuser_sudo
	print("[allow:browse] sudo user=", input.user.username)
}

allow if {
	input.action == "save"
	roles.is_superuser_sudo
	print("[allow:save] sudo user=", input.user.username)
}

allow if {
	input.action == "delete"
	roles.is_superuser_sudo
	print("[allow:delete] sudo user=", input.user.username)
}


allow if {
	input.action == "create"
	roles.is_superuser_sudo
	print("[allow:create] sudo user=", input.user.username)
}


allow if {
	input.action == "transition"
	roles.is_superuser_sudo
	print(
		"[allow:transition] sudo user=", input.user.username,
		"transition=", input.transition,
	)
}


