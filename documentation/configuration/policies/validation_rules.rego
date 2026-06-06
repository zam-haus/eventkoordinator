package udm.udmframeworkv1.validation_rules

import rego.v1
import data.udm.udmframeworkv1.config._deadline
import data.udm.udmframeworkv1.proposals._proposal_ctx
import data.udm.udmframeworkv1.proposals.is_owner_or_editor
import data.udm.udmframeworkv1.proposals.is_moderator
import data.udm.udmframeworkv1.proposals.is_superuser_sudo
import data.udm.udmframeworkv1.proposals.current_status
import data.udm.udmframeworkv1.proposals._can_view

# ─── Submission checklist ───────────────────────────────────────────────────────
# Warning-level messages shown to owner/editor during view/save that flag fields
# not yet ready for submission.  Level "warning" never blocks save — only
# "critical" entries do (see no_critical_errors above).

_checklist_ctx if { _proposal_ctx; is_owner_or_editor }

# title: 1–30 non-empty characters
_title_complete if {
	v := input.entity.fields.title.value
	print("[check:title] value=", v)
	v != null
	count(trim_space(v)) >= 1
	count(v) <= 30
	print("[check:title] PASS len=", count(v))
}
error_messages contains msg if {
	_checklist_ctx
	not _title_complete
	print("[checklist:title] FAIL title=", input.entity.fields.title.value)
	msg := {"level": "warning", "text": "Title is required (1–30 characters).", "field_slug": "title"}
}

# abstract: 50–250 characters
_abstract_complete if {
	v := input.entity.fields.abstract.value
	v != null
	print("[check:abstract] value_len=", count(v))
	count(v) >= 50
	count(v) <= 250
	print("[check:abstract] PASS len=", count(v))
}
error_messages contains msg if {
	_checklist_ctx
	not _abstract_complete
	v := input.entity.fields.abstract.value
	print("[checklist:abstract] FAIL v=", v)
	msg := {"level": "warning", "text": "Abstract must be 50–250 characters.", "field_slug": "abstract"}
}

# description: 50–1000 characters
_description_complete if {
	v := input.entity.fields.description.value
	v != null
	print("[check:description] value_len=", count(v))
	count(v) >= 50
	count(v) <= 1000
	print("[check:description] PASS len=", count(v))
}
error_messages contains msg if {
	_checklist_ctx
	not _description_complete
	v := input.entity.fields.description.value
	print("[checklist:description] FAIL v=", v)
	msg := {"level": "warning", "text": "Description must be 50–1000 characters.", "field_slug": "description"}
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
	print("[checklist:duration] FAIL days=", input.entity.fields["duration-days"].value,
	      "time=", input.entity.fields["duration-time-per-day"].value)
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

# photo: an image has been uploaded
_photo_complete if {
	print("[check:photo] value=", input.entity.fields.photo.value)
	input.entity.fields.photo.value != null
	print("[check:photo] PASS")
}
error_messages contains msg if {
	_checklist_ctx
	not _photo_complete
	print("[checklist:photo] FAIL no image uploaded")
	msg := {"level": "warning", "text": "Please upload a proposal image (min. 1440×1080 px).", "field_slug": "photo"}
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
	print("[copyright] BLOCK: photo present without consent user=", input.user.username,
	      "consent=", input.entity.fields["photo-copyright-consent"].value)
	msg := {
		"level": "critical",
		"text": "You must confirm copyright consent before uploading an image.",
		"field_slug": "photo-copyright-consent",
	}
}

# submission deadline: _deadline is defined in description.rego (same package)
_within_deadline if {
	print("[check:deadline] now_ns=", time.now_ns(), "deadline=", _deadline,
	      "deadline_ns=", time.parse_rfc3339_ns(_deadline))
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