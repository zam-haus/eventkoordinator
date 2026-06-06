package udm.udmframeworkv1.description

import rego.v1
import data.udm.udmframeworkv1.config._deadline
import data.udm.udmframeworkv1.config._deadline_human

# ── Deadline ──────────────────────────────────────────────────────────────────

_days_left := round(
    (time.parse_rfc3339_ns(_deadline) - time.now_ns()) /
    (24 * 60 * 60 * 1000000000)
)

# ── Pill ──────────────────────────────────────────────────────────────────────

_pill_color := "red"    if { _days_left <  0 }
_pill_color := "yellow" if { _days_left >= 0; _days_left <= 7 }
_pill_color := "blue"   if { _days_left >  7 }

_pill_text := concat("", [sprintf("%v", [-_days_left]), " days overdue"]) if { _days_left <  0 }
_pill_text := "Due today"                                                 if  { _days_left == 0 }
_pill_text := concat("", [sprintf("%v", [_days_left]),  " days left"])    if { _days_left >  0 }

_pill := concat("", [
    `<pill color="`, _pill_color, `">`,
    _pill_text,
    `</pill>`,
])

# ── Localized description ─────────────────────────────────────────────────────

_desc_en := concat("", [
    "## Christmas Programme\n\n",
    "Submissions open until ", _deadline_human, " ", _pill, ".\n\n",
    "Please read the submission guidelines carefully.",
])

_desc_de := concat("", [
    "## Weihnachtsprogramm\n\n",
    "Einreichungen möglich bis ", _deadline_human, " ", _pill, ".\n\n",
    "Bitte lesen Sie die Einreichungsrichtlinien sorgfältig.",
])

TYPE_DESCRIPTION := {"en": _desc_en, "de": _desc_de}