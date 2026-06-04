package udm
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
#   send_notification      Send email; uses a named Jinja2 template pair
#                          (<name>.txt.j2 / <name>.html.j2)
#   set_field_value        Write a field value on the node or a submodel
#   trigger_transition     Fire a workflow transition on self or children
#
# Template files expected under the Django TEMPLATES search path:
#   proposals/submit.{txt,html}.j2
#   proposals/submit_contact.{txt,html}.j2
#   proposals/accept.{txt,html}.j2
#   proposals/reject.{txt,html}.j2
#   proposals/request_revision.{txt,html}.j2
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
    "template_name": "proposals/submit",
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
    "template_name": "proposals/submit_contact",
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
    "template_name": "proposals/accept",
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
    "template_name": "proposals/reject",
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
    "template_name": "proposals/request_revision",
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
} if {
    input.action == "transition"
    input.field == "status"
    input.transition == "request-revision"
}
