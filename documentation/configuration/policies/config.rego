package udm.udmframeworkv1.modules.config

import rego.v1

_deadline_human := "2026-12-31"
_deadline := "2026-12-31T23:59:59Z"

# ─── Configuration ─────────────────────────────────────────────────────────────

# Deadline after which new proposals may no longer be created (ISO-8601 UTC).
# Must match the deadline in description.rego.
SUBMISSION_DEADLINE := "2026-12-31T23:59:59Z"

# Group names whose members act as proposal moderators.
MODERATOR_GROUP_NAMES := ["moderators"]


