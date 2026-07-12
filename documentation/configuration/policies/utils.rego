package udm.udmframeworkv1.modules.utils

import rego.v1

# ─── Utilities ─────────────────────────────────────────────────────────────────

# True when this evaluation is the validation preview (no side effects; the
# save/transition button states are being computed). Replaces the removed
# input.validate_only flag.
is_preview if input.action == "preview"
