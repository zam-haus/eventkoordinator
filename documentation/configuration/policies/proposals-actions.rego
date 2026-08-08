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
# MailTemplate slugs used here (shipped in the bundle under templates/):
#   proposal-submitted-owner
#   proposal-submitted-contact
#   proposal-accepted
#   proposal-rejected
#   proposal-revision-requested
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
