package udm.udmframeworkv1.modules.validation_rules

import data.udm.udmframeworkv1.modules.config._deadline
import data.udm.udmframeworkv1.modules.roles.is_owner_or_editor
import data.udm.udmframeworkv1.modules.sudo.is_superuser_sudo
import data.udm.udmframeworkv1.modules.utils._proposal_ctx
import data.udm.udmframeworkv1.modules.workflow.current_status
import rego.v1

# ─── Submission checklist ───────────────────────────────────────────────────────
# Warning-level messages shown to owner/editor during view/save that flag fields
# not yet ready for submission.  Level "warning" never blocks save — only
# "critical" entries do (see no_critical_errors above).

_checklist_ctx if {
	_proposal_ctx
	is_owner_or_editor
}

# ─── Bilingual (de/en) fields: title, abstract, description ────────────────────
# Each is localized; a language value is either absent (both null and unset) or
# must satisfy the field's length bounds. Rules:
#  1. Each field needs a valid value in at least one language; the other
#     language may stay empty but, if present, must itself be valid.
#  2. Per language: once any of the three fields is filled in that language,
#     all three must be filled in that language.

_bilingual_langs := {"en", "de"}

_bilingual_bounds := {
	"title": {"min": 1, "max": 30},
	"abstract": {"min": 50, "max": 250},
	"description": {"min": 50, "max": 1000},
}

_lang_value(slug, lang) := v if {
	raw := input.entity.fields[slug].value
	is_object(raw)
	v := object.get(raw, lang, null)
}

default _lang_value(slug, lang) := null

_lang_empty(slug, lang) if trim_space(_field_lang_text(slug, lang)) == ""

_field_lang_text(slug, lang) := v if {
	v := _lang_value(slug, lang)
	v != null
}

default _field_lang_text(slug, lang) := ""

_lang_valid(slug, lang) if {
	bounds := _bilingual_bounds[slug]
	v := _lang_value(slug, lang)
	v != null
	count(trim_space(v)) >= bounds.min
	count(v) <= bounds.max
}

_lang_invalid(slug, lang) if {
	not _lang_empty(slug, lang)
	not _lang_valid(slug, lang)
}

_has_valid_value(slug) if {
	some lang in _bilingual_langs
	_lang_valid(slug, lang)
}

# Rule 2: for each language, "started" means at least one of the three fields
# is filled there; once started, every field must be filled there.
_lang_started(lang) if {
	some slug in object.keys(_bilingual_bounds)
	not _lang_empty(slug, lang)
}

_field_missing_while_lang_started(slug, lang) if {
	_lang_started(lang)
	_lang_empty(slug, lang)
}

_bilingual_field_complete(slug) if {
	print("[check:", slug, "] value=", input.entity.fields[slug].value)
	_has_valid_value(slug)
	not _lang_invalid(slug, "en")
	not _lang_invalid(slug, "de")
	not _field_missing_while_lang_started(slug, "en")
	not _field_missing_while_lang_started(slug, "de")
	print("[check:", slug, "] PASS")
}

_title_complete if _bilingual_field_complete("title")

_abstract_complete if _bilingual_field_complete("abstract")

_description_complete if _bilingual_field_complete("description")

# No valid value in either language.
error_messages contains msg if {
	_checklist_ctx
	some slug in object.keys(_bilingual_bounds)
	not _has_valid_value(slug)
	bounds := _bilingual_bounds[slug]
	print("[checklist:", slug, "] FAIL no valid value in either language, value=", input.entity.fields[slug].value)
	msg := {
		"level": "warning",
		"text": sprintf("%s must be %d–%d characters in at least one language (English or German).", [slug, bounds.min, bounds.max]),
		"field_slug": slug,
	}
}

# A provided (non-empty) language value that doesn't meet the length bounds.
error_messages contains msg if {
	_checklist_ctx
	some slug in object.keys(_bilingual_bounds)
	some lang in _bilingual_langs
	_lang_invalid(slug, lang)
	bounds := _bilingual_bounds[slug]
	print("[checklist:", slug, "] FAIL invalid ", lang, " value=", _lang_value(slug, lang))
	msg := {
		"level": "warning",
		"text": sprintf("%s (%s) must be %d–%d characters.", [slug, lang, bounds.min, bounds.max]),
		"field_slug": slug,
	}
}

# Per-language completeness: once one of title/abstract/description is filled
# in a language, all three must be — flagged on every field still missing there.
error_messages contains msg if {
	_checklist_ctx
	some lang in _bilingual_langs
	_lang_started(lang)
	some slug in object.keys(_bilingual_bounds)
	_field_missing_while_lang_started(slug, lang)
	print("[checklist:", slug, "] FAIL missing in ", lang, " while other fields filled in that language")
	msg := {
		"level": "warning",
		"text": sprintf("%s must also be filled in (%s), since other fields are already filled in that language.", [slug, lang]),
		"field_slug": slug,
	}
}

# duration: duration-days >= 1 and duration-time-per-day is a non-zero HH:MM value
_duration_complete if {
	days := input.entity.fields["duration-days"].value
	t := input.entity.fields["duration-time-per-day"].value
	print("[check:duration] days=", days, "time=", t)
	days != null
	days >= 1
	t != null
	parts := split(t, ":")
	count(parts) == 2
	t != "00:00"
	print("[check:duration] PASS time=", t)
}

error_messages contains msg if {
	_checklist_ctx
	not _duration_complete
	print(
		"[checklist:duration] FAIL days=", input.entity.fields["duration-days"].value,
		"time=", input.entity.fields["duration-time-per-day"].value,
	)
	msg := {"level": "warning", "text": "Duration must be at least 1 day with a non-zero time per day (HH:MM).", "field_slug": "duration-days"}
}

# max-participants: >= 1
_max_participants_complete if {
	v := input.entity.fields["max-participants"].value
	print("[check:max-participants] value=", v)
	v != null
	v >= 1
	print("[check:max-participants] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _max_participants_complete
	print("[checklist:max-participants] FAIL value=", input.entity.fields["max-participants"].value)
	msg := {"level": "warning", "text": "Maximum number of participants must be at least 1.", "field_slug": "max-participants"}
}

# occurrence-count: >= 1
_occurrence_count_complete if {
	v := input.entity.fields["occurrence-count"].value
	print("[check:occurrence-count] value=", v)
	v != null
	v >= 1
	print("[check:occurrence-count] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _occurrence_count_complete
	print("[checklist:occurrence-count] FAIL value=", input.entity.fields["occurrence-count"].value)
	msg := {"level": "warning", "text": "Occurrence count must be at least 1.", "field_slug": "occurrence-count"}
}

# preferred-dates: non-empty text
_preferred_dates_complete if {
	v := input.entity.fields["preferred-dates"].value
	print("[check:preferred-dates] value=", v)
	v != null
	count(trim_space(v)) >= 1
	print("[check:preferred-dates] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _preferred_dates_complete
	print("[checklist:preferred-dates] FAIL value=", input.entity.fields["preferred-dates"].value)
	msg := {"level": "warning", "text": "Please specify your preferred dates.", "field_slug": "preferred-dates"}
}

# language: a choice has been selected
_language_complete if {
	v := input.entity.fields.language.value
	print("[check:language] value=", v)
	v != null
	count(v) > 0
	print("[check:language] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _language_complete
	print("[checklist:language] FAIL value=", input.entity.fields.language.value)
	msg := {"level": "warning", "text": "Please select a language.", "field_slug": "language"}
}

# submission-type: a choice has been selected
_submission_type_complete if {
	v := input.entity.fields["submission-type"].value
	print("[check:submission-type] value=", v)
	v != null
	count(v) > 0
	print("[check:submission-type] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _submission_type_complete
	print("[checklist:submission-type] FAIL value=", input.entity.fields["submission-type"].value)
	msg := {"level": "warning", "text": "Please select a submission type.", "field_slug": "submission-type"}
}

# area: a choice has been selected
_area_complete if {
	v := input.entity.fields.area.value
	print("[check:area] value=", v)
	v != null
	count(v) > 0
	print("[check:area] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _area_complete
	print("[checklist:area] FAIL value=", input.entity.fields.area.value)
	msg := {"level": "warning", "text": "Please select a workshop area.", "field_slug": "area"}
}

# photo: an image has been uploaded …
_photo_uploaded if {
	print("[check:photo] value=", input.entity.fields.photo.value)
	input.entity.fields.photo.value != null
	print("[check:photo] uploaded")
}

# … with sufficient resolution. Dimensions come from input.files (null for
# attachments whose dimensions could not be determined — those fail closed).
_photo_resolution_ok if {
	f := input.files[input.entity.fields.photo.value]
	print("[check:photo] width=", f.image_width, "height=", f.image_height)
	f.image_width >= 1440
	f.image_height >= 1080
	print("[check:photo] PASS resolution ok")
}

_photo_complete if {
	_photo_uploaded
	_photo_resolution_ok
}

error_messages contains msg if {
	_checklist_ctx
	not _photo_uploaded
	print("[checklist:photo] FAIL no image uploaded")
	msg := {"level": "warning", "text": "Please upload a proposal image (min. 1440×1080 px).", "field_slug": "photo"}
}

error_messages contains msg if {
	_checklist_ctx
	_photo_uploaded
	not _photo_resolution_ok
	print("[checklist:photo] FAIL resolution too low")
	msg := {"level": "warning", "text": "The proposal image is too small — minimum resolution is 1440×1080 pixels.", "field_slug": "photo"}
}

# photo-copyright-consent: uploading a new (non-null) image requires the
# copyright checkbox to be ticked.  Clearing the image (value → null) is
# always permitted so authors can remove an image without re-ticking.
error_messages contains msg if {
	input.action == "save"
	is_owner_or_editor
	not is_superuser_sudo
	input.changed_fields.photo
	input.entity.fields.photo.value != null
	not input.entity.fields["photo-copyright-consent"].value == true
	print(
		"[copyright] BLOCK: photo present without consent user=", input.user.username,
		"consent=", input.entity.fields["photo-copyright-consent"].value,
	)
	msg := {
		"level": "critical",
		"text": "You must confirm copyright consent before uploading an image.",
		"field_slug": "photo",
	}
}

# submission deadline: _deadline is defined in description.rego (same package)
_within_deadline if {
	print(
		"[check:deadline] now_ns=", time.now_ns(), "deadline=", _deadline,
		"deadline_ns=", time.parse_rfc3339_ns(_deadline),
	)
	time.now_ns() <= time.parse_rfc3339_ns(_deadline)
	print("[check:deadline] PASS still within deadline")
}

error_messages contains msg if {
	_checklist_ctx
	current_status == "draft"
	not _within_deadline
	print("[checklist:deadline] FAIL deadline has passed, deadline=", _deadline)
	msg := {"level": "warning", "text": "The submission deadline has passed.", "field_slug": null}
}

_speakers := object.get(input.entity.children, "speakers", [])

# speakers: at least one speaker submodel must exist
_at_least_one_speaker if {
	print("[check:speakers] count=", count(_speakers))
	count(_speakers) >= 1
	print("[check:speakers] PASS")
}

error_messages contains msg if {
	_checklist_ctx
	not _at_least_one_speaker
	print("[checklist:speakers] FAIL no speakers, count=", count(_speakers))
	msg := {"level": "warning", "text": "At least one speaker must be added.", "field_slug": "speakers"}
}

# speakersHaveBio: every speaker must have a non-empty biography
_all_speakers_have_bio if {
	print("[check:speakers-bio] checking", count(_speakers), "speakers")
	every s in _speakers {
		v := s.fields.biography.value
		v != null
		count(trim_space(v)) > 0
	}
	print("[check:speakers-bio] PASS all speakers have biography")
}

error_messages contains msg if {
	_checklist_ctx
	count(_speakers) > 0
	not _all_speakers_have_bio
	print("[checklist:speakers-bio] FAIL some speakers missing biography")
	msg := {"level": "warning", "text": "All speakers must have a biography.", "field_slug": "speakers"}
}

# True when every content-completeness check passes — no warnings are attached to
# proposal fields.  Used to gate the submit/resubmit transitions.
default _checklist_complete := false

_checklist_complete if {
	_title_complete
	_abstract_complete
	_description_complete
	_duration_complete
	_max_participants_complete
	_occurrence_count_complete
	_preferred_dates_complete
	_language_complete
	_submission_type_complete
	_area_complete
	_photo_complete
	_at_least_one_speaker
	_all_speakers_have_bio
}
