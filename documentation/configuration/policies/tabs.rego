package udm.udmframeworkv1.tabs

import rego.v1


# ─── Tab-level message propagation ─────────────────────────────────────────────
# For each tab that contains fields with active error/warning messages, emit an
# additional message whose field_slug is the tab's own slug so the UI can display
# a severity icon and tooltip on the tab button.
#
# Conditions are duplicated from the field-level rules to avoid a self-referencing
# cycle through error_messages.

# ── tab-general ────────────────────────────────────────────────────────────────
error_messages contains msg if {
	_checklist_ctx; not _abstract_complete
	msg := {"level": "warning", "text": "Abstract must be 50–250 characters.", "field_slug": "tab-general"}
}
error_messages contains msg if {
	_checklist_ctx; not _description_complete
	msg := {"level": "warning", "text": "Description must be 50–1000 characters.", "field_slug": "tab-general"}
}
error_messages contains msg if {
	_checklist_ctx; not _language_complete
	msg := {"level": "warning", "text": "Please select a language.", "field_slug": "tab-general"}
}
error_messages contains msg if {
	_checklist_ctx; not _submission_type_complete
	msg := {"level": "warning", "text": "Please select a submission type.", "field_slug": "tab-general"}
}
error_messages contains msg if {
	_checklist_ctx; not _area_complete
	msg := {"level": "warning", "text": "Please select a workshop area.", "field_slug": "tab-general"}
}
error_messages contains msg if {
	_checklist_ctx; not _photo_complete
	msg := {"level": "warning", "text": "Please upload a proposal image (min. 1440×1080 px).", "field_slug": "tab-general"}
}
error_messages contains msg if {
	input.action == "save"
	is_owner_or_editor
	not is_superuser_sudo
	input.changed_fields.photo
	input.entity.fields.photo.value != null
	not input.entity.fields["photo-copyright-consent"].value == true
	msg := {"level": "critical", "text": "You must confirm copyright consent before uploading an image.", "field_slug": "tab-general"}
}

# ── tab-participants ───────────────────────────────────────────────────────────
error_messages contains msg if {
	_checklist_ctx; not _max_participants_complete
	msg := {"level": "warning", "text": "Maximum number of participants must be at least 1.", "field_slug": "tab-participants"}
}

# ── tab-scheduling ─────────────────────────────────────────────────────────────
error_messages contains msg if {
	_checklist_ctx; not _duration_complete
	msg := {"level": "warning", "text": "Duration must be at least 1 day with a non-zero time per day (HH:MM).", "field_slug": "tab-scheduling"}
}
error_messages contains msg if {
	_checklist_ctx; not _occurrence_count_complete
	msg := {"level": "warning", "text": "Occurrence count must be at least 1.", "field_slug": "tab-scheduling"}
}
error_messages contains msg if {
	_checklist_ctx; not _preferred_dates_complete
	msg := {"level": "warning", "text": "Please specify your preferred dates.", "field_slug": "tab-scheduling"}
}

# ── tab-submission ─────────────────────────────────────────────────────────────
error_messages contains msg if {
	input.action == "save"
	_can_view
	not is_moderator
	not is_superuser_sudo
	_changing_reviewer_assignments
	msg := {"level": "critical", "text": "Only moderators may change reviewer assignments.", "field_slug": "tab-submission"}
}
