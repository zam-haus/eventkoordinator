package udm.udmframeworkv1.modules.utils

import rego.v1

# ─── Utilities ─────────────────────────────────────────────────────────────────

# True when this evaluation is the validation preview (no side effects; the
# save/transition button states are being computed). Replaces the removed
# input.validate_only flag.
is_preview if input.action == "preview"

# Guard used by proposal-level context messages: true for view/save and for
# transitions on the proposal status field. Suppresses proposal-level noise when
# the action is a review vote transition on a subfield.
_proposal_ctx if input.action in {"save", "preview"}

_proposal_ctx if {
	input.action == "transition"
	input.field == "status"
}
