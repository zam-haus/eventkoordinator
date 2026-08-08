package udm.udmframeworkv1.modules.proposals_actions
import rego.v1

# ─────────────────────────────────────────────────────────────────────────────
# proposals-actions.rego
#
# Lifecycle actions for the Proposal UDM type.
# Loaded alongside proposals.rego — this file only defines the `actions` set;
# it does not define `allow`, `editable_fields`, etc.
#
# Built-in action types used here (registered in userdefinedmodel.actions):
#   create_submodel_item   Append a new item to a submodel_list field
#   send_notification      Send email; template_name is a MailTemplate slug
#                          (UDM Admin → UDM Templating)
#   set_field_value        Write a field value on the node or a submodel
#   trigger_transition     Fire a workflow transition on self or children
#
# MailTemplate slugs sent by the actions below (shipped in the bundle under
# templates/): proposal-submitted-owner, proposal-submitted-contact,
# proposal-accepted, proposal-rejected, proposal-revision-requested,
# review-requested, review-given.
#
# Each action passes a `context` object; the engine additionally injects the
# input document and the calculated decision fields. The templates read
# context.proposal.{title,call_title,owner_name,url}.
# ─────────────────────────────────────────────────────────────────────────────


# ─── CREATE ──────────────────────────────────────────────────────────────────
# Seed the speakers list with the submitter as the first speaker entry.
# $$user.* markers are interpolated by the handler before the submodel is written.

actions contains {
    "type": "create_submodel_item",
    "phase": "post",
    "field_slug": "speakers",
    "fields": {
        "display-name": "$$user.username",
        "email": "$$user.email",
    },
} if {
    input.action == "create"
}


# ─── TRANSITION: submit (status field / proposal workflow) ───────────────────
# Confirmation email to the proposal owner.

actions contains {
    "type": "send_notification",
    "phase": "post",
    "subject": sprintf(
        "Einreichung eingegangen / Submission received: %v",
        [input.entity.fields["title"].value],
    ),
    "template_name": "proposal-submitted-owner",
    "context": {"proposal": proposal_context},
    "recipient_field": "owner",
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "submit"
}

# Notification to the responsible programme contact.
# Replace extra_recipients with your actual contact address or derive it from a field.

actions contains {
    "type": "send_notification",
    "phase": "post",
    "subject": sprintf(
        "Neue Einreichung / New submission: %v",
        [input.entity.fields["title"].value],
    ),
    "template_name": "proposal-submitted-contact",
    "context": {"proposal": proposal_context},
    "recipient_field": null,
    "extra_recipients": ["programm@example.org"],
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "submit"
}


# ─── TRANSITION: accept (status field / proposal workflow) ───────────────────

EVENT_TYPE_ID := "a7f7dcd6-9272-437e-9bd1-07f526b5c7ba"

actions contains {
    "type": "send_notification",
    "phase": "post",
    "subject": sprintf(
        "Einreichung angenommen / Submission accepted: %v",
        [input.entity.fields["title"].value],
    ),
    "template_name": "proposal-accepted",
    "context": {"proposal": proposal_context},
    "recipient_field": "owner",
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "accept"
}


# ─── TRANSITION: add-event (add_event field / add-event workflow) ────────────
# A separate single-state workflow (events-and-sync.md §1.2): "add_event" only
# ever self-loops ready -> ready, and transitions.rego permits it only while
# the MAIN status field reads "accepted". Each firing creates one more linked
# Event — allow_multiple is true because repeatability is the whole point.

actions contains {
    "type": "create_linked_entity",
    "phase": "post",
    "target_type": EVENT_TYPE_ID,
    "reference_field": "origin",
    "initial_fields": {},
    "allow_multiple": true,
} if {
    input.action == "transition"
    input.field == "add_event"
    input.transition == "add-event"
}


# ─── TRANSITION: reject (status field / proposal workflow) ───────────────────

actions contains {
    "type": "send_notification",
    "phase": "post",
    "subject": sprintf(
        "Einreichung abgelehnt / Submission rejected: %v",
        [input.entity.fields["title"].value],
    ),
    "template_name": "proposal-rejected",
    "context": {"proposal": proposal_context},
    "recipient_field": "owner",
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "reject"
}



# ─── TRANSITION: request-revision (status field / proposal workflow) ─────────

actions contains {
    "type": "send_notification",
    "phase": "post",
    "subject": sprintf(
        "Überarbeitung angefordert / Revision requested: %v",
        [input.entity.fields["title"].value],
    ),
    "template_name": "proposal-revision-requested",
    "context": {"proposal": proposal_context},
    "recipient_field": "owner",
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "request-revision"
}

# Reset all review votes to "open" so reviewers can re-evaluate after revision.

actions contains {
    "type": "trigger_transition",
    "phase": "post",
    "field_slug": "vote",
    "transition_name": "reset",
    "target_scope": "children",
    "target_parent_field": "reviews",
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "request-revision"
}


# ─── Template context ────────────────────────────────────────────────────────
# The JSON handed to the mail templates as `context.proposal`. Keys match what
# the bundled templates expect; apiv1 builds the same shape in mailcontext.py.

proposal_context := {
    "title": object.get(input.entity.fields, ["title", "value"], ""),
    "call_title": object.get(input.entity.fields, ["call", "value"], ""),
    "owner_name": object.get(input.entity.fields, ["owner", "value"], ""),
    "moderation_comment": object.get(input.entity.fields, ["moderation_comment", "value"], ""),
    "url": sprintf("/entities/%v", [input.entity.id]),
}


# ─── Review notifications (reviews submodel / vote workflow) ─────────────────
# A vote transition fires on the review node itself, so `recipient_field`
# resolves against that node and input.node_id identifies which review it was.

# The review node the current transition is acting on.
_review_node := r if {
	some r in object.get(input.entity.children, "reviews", [])
	r.id == input.node_id
}

# Shape expected by the review-* templates.
_review_context := {
	"comment": object.get(_review_node.fields, ["comment", "value"], ""),
	# The input document predates the state change, so the vote field still
	# holds the old value here — the descriptor's target state is the new one.
	"status": input.transition_descriptor.to_state,
	"reviewer_name": object.get(_review_node.fields, ["author", "value"], ""),
	"reviewer_is_system": false,
}

# Re-opening a vote is a fresh request to that reviewer.

actions contains {
	"type": "send_notification",
	"phase": "post",
	"subject": sprintf(
		"Bitte um Gutachten / Review requested: %v",
		[object.get(input.entity.fields, ["title", "value"], "")],
	),
	"template_name": "review-requested",
	"recipient_field": "author",
	"context": {
		"proposal": proposal_context,
		"reviewer": {"username": object.get(_review_node.fields, ["author", "value"], "")},
	},
} if {
	input.action == "transition"
	input.field == "vote"
	input.transition == "reset"
}

# Any cast vote notifies the programme contact.

actions contains {
	"type": "send_notification",
	"phase": "post",
	"subject": sprintf(
		"Gutachten eingegangen / Review submitted: %v",
		[object.get(input.entity.fields, ["title", "value"], "")],
	),
	"template_name": "review-given",
	"recipient_field": null,
	"extra_recipients": ["programm@example.org"],
	"context": {
		"proposal": proposal_context,
		"review": _review_context,
	},
} if {
	input.action == "transition"
	input.field == "vote"
	input.transition in {"accept", "reject", "revise"}
}


# ─── Templates sent by application code ──────────────────────────────────────
# Everything above is sent by a send_notification post-action. These seven are
# not: events live in apiv1 (Event/EventFlow), not in the UDM model, so there is
# no entity, workflow or transition for a policy to hook. They are sent by
# apiv1/flows.py. They are named here only so bundle export still collects them
# — remove this list once events are modelled as a UDM type with its own
# workflow, and drive them from actions like the ones above.

apiv1_mail_templates := [
	"event-submitted-owner",
	"event-approved-contact",
	"event-rejected-contact",
	"event-confirmed-owner",
	"event-confirmed-contact",
	"event-canceled-owner",
	"event-canceled-contact",
]
