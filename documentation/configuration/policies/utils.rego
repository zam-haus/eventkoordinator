package udm.udmframeworkv1.modules.utils

import rego.v1

# ─── Utilities ─────────────────────────────────────────────────────────────────

# True when the engine is doing a dry-run (validate_only=true from the API).
is_validation if input.validate_only == true
