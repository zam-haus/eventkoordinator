---
type: backend_documentation
title: Mail Templates System
description: Documentation for the mail templates system in UserDefinedModel
---

# Mail Templates System

The mail templates system provides a way to send email notifications from Rego policies and workflow transitions using Jinja2 templates.

**Related Documentation**:
- [Policy Actions System](../backend/actions.md) - Policy actions including `send_notification`
- [Backend Overview](../backend/overview.md) - Backend components
- [UDM Overview](../api/udm_overview.md) - UDM API overview

## Overview

The mail templates system allows Rego policies and workflow transitions to send email notifications using:
1. **Database templates** (`MailTemplate` model) - Editable in UDM Admin
2. **File-based templates** (`.txt.j2` / `.html.j2` files) - Version-controlled

Templates are rendered in a **sandboxed Jinja2 environment** for security, and the context is round-tripped through JSON to prevent access to ORM relations.

## Mail Template Types

### UDM MailTemplate Model

Mail templates are stored in the database as `MailTemplate` instances:

```python
class MailTemplate(MetaBase):
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True, default="")
    subject = models.TextField(blank=True, default="")
    body_text = models.TextField(blank=True, default="")
    body_html = models.TextField(blank=True, default="")
    example_input = models.JSONField(default=dict, blank=True)
```

**Usage in UDM Admin**:
- Navigate to UDM Admin → UDM Templating
- Create or edit templates with a unique `slug`
- Edit subject, plain-text body, and HTML body
- Test with example input

### File-Based Templates

Template files live in `documentation/configuration/templates/`:
- `{slug}.txt.j2` - Plain text template
- `{slug}.html.j2` - HTML template
- `{slug}.json` - Example input for testing

**Available Templates**:
- `proposal-accepted` - Sent to submitter when proposal is accepted
- `proposal-rejected` - Sent to submitter when proposal is rejected
- `proposal-submitted-owner` - Sent to submitter on submission
- `proposal-submitted-contact` - Sent to call contact on submission
- `proposal-revision-requested` - Sent when revisions are requested
- `event-submitted-owner` - Sent to submitter on event submission
- `event-confirmed-owner` - Sent to submitter when event is confirmed
- `event-canceled-owner` - Sent to submitter when event is canceled
- `event-approved-contact` - Sent to call contact on event approval
- `event-confirmed-contact` - Sent to call contact on event confirmation
- `event-canceled-contact` - Sent to call contact on event cancellation
- `event-rejected-contact` - Sent to call contact on event rejection
- `review-requested` - Sent to reviewer when asked to review
- `review-given` - Sent to call contact when review is submitted

## Template Context

### Core Context Keys

The template context is built by `build_notification_context()` and includes:

| Key | Description |
|-----|-------------|
| `context` | Policy's own context JSON |
| `input` | Full policy input document |
| `entity` | `input.entity` convenience alias |
| `fields` | `{slug: value}` for all fields on the node |
| `node` | `{id, schema_id}` of the triggering node |
| `user` | The actor (from `input.user`) |
| `trigger` | Lifecycle event: `save`, `create`, `transition` |
| `phase` | Dispatch phase: `pre` or `post` |
| `action` | Action dictionary from input |
| `transition` | Transition dictionary from input |
| `field` | Field dictionary from input |
| `locale` | Locale from input |
| `type_id` | Type ID from input |
| `additional_result` | Policy's VIEW carry-over |
| `decision` | Calculated fields: `allow`, `messages`, `valid_transitions`, `additional_result` |
| `recipients` | Resolved recipient email addresses |
| `frontend_base_url` | Jinja global for frontend URLs |

### Template Globals

The following globals are available in all templates:

| Global | Description |
|--------|-------------|
| `frontend_base_url` | Base URL for frontend (e.g., `https://example.com`) |
| `site_name` | Site name from settings |
| `default_from_email` | Default sender email from settings |
| `now` | Current datetime |

### Jinja Filters

The system uses custom filters for safe rendering:

| Filter | Description | Example |
|--------|-------------|---------|
| `timezone(tz)` | Convert datetime to timezone | `{{ dt \| timezone("Europe/Berlin") }}` |
| `isoformat` | Format datetime as ISO string | `{{ dt \| timezone("Europe/Berlin") \| isoformat() }}` |
| `htmlquote` | Escape and format text for HTML | `{{ text \| htmlquote }}` |
| `userinput` | Format text with indentation for plain text | `{{ comment \| userinput }}` |

## Template Rendering

### Programmatic Rendering

```python
from userdefinedmodel.mailtemplates import render_source, render_mail_template, send_mail_template

# Render unsaved template sources
rendered = render_source(
    body_text="Hello {{ name }}",
    body_html="<p>Hello {{ name }}</p>",
    context={"name": "Ada"},
    subject="Welcome {{ name }}",
)

# Render a MailTemplate by slug
rendered = render_mail_template("proposal-accepted", context={"proposal": {...}})

# Render and send
rendered = send_mail_template(
    "proposal-accepted",
    context={"proposal": {...}},
    recipient_list=["user@example.com"],
)
```

### Rendering Process

1. **Context Preparation**: The context is JSON-serialized and deserialized to ensure only plain data is passed
2. **Sandboxed Environment**: Jinja2 renders in a `SandboxedEnvironment` to prevent security issues
3. **Filter Application**: Custom filters (`timezone`, `isoformat`, `htmlquote`, `userinput`) are applied
4. **Template Output**: Returns `RenderedMail` with `subject`, `text`, and `html`

### Security Considerations

- **Sandboxed Environment**: Prevents access to dangerous attributes and methods
- **JSON Round-trip**: Ensures templates only see plain data, not ORM objects
- **Settings Not Exposed**: Django settings are NOT exposed to templates (unlike in `project.jinja2`)

## Send Notification Action

The `send_notification` action triggers email sending from Rego policies:

```python
class SendNotificationOutput(BaseModel):
    type: Literal["send_notification"]
    phase: Literal["pre", "post"]
    subject: str = ""
    template_name: str = ""
    context: dict = {}
    body_text: str = ""
    body_html: str = ""
    recipient_field: str | None = None
    extra_recipients: list[str] = []
```

### Template-Based Sending

```rego
data.udm.result.actions[_] := action if {
    input.trigger == "transition"
    input.transition_name == "accept"
    action := {
        "type": "send_notification",
        "phase": "post",
        "template_name": "proposal-accepted",
        "context": {"proposal": input.entity}
    }
}
```

### Inline Sending

```rego
data.udm.result.actions[_] := action if {
    input.trigger == "create"
    action := {
        "type": "send_notification",
        "phase": "post",
        "subject": "New entity created",
        "body_text": "A new entity was created by {{ user.username }}",
        "extra_recipients": ["admin@example.com"]
    }
}
```

### Recipient Resolution

Recipients are resolved in order:
1. `recipient_field` - User from a user_select field
2. `extra_recipients` - Additional email addresses

## Template Examples

### Simple Template

```
Hello {{ entity.name }},

Your proposal "{{ entity.title }}" has been accepted.

 {{ frontend_base_url }}/proposals/{{ entity.id }}

The team
```

### Template with DateTime

```
The event "{{ event.name }}" has been confirmed:

Start: {{ event.start_time | timezone("Europe/Berlin") | isoformat() }}
End: {{ event.end_time | timezone("Europe/Berlin") | isoformat() }}
```

### Template with Review Comments

```
A review has been submitted for "{{ proposal.title }}":

Reviewer: {{ review.reviewer_name }}
Status: {{ review.status }}
Comment:
{{ review.comment | userinput }}
```

## Migration and Bundles

### UDM Bundle Import

Mail templates are included in UDM bundles as `udm_mailtemplates`:

```json
{
  "version": 1,
  "udm_mailtemplates": [
    {
      "slug": "proposal-accepted",
      "description": "Sent to submitter when proposal is accepted",
      "subject": "Einreichung angenommen / Submission accepted: {{ proposal.title }}",
      "body_text": "...",
      "body_html": "...",
      "example_input": {...}
    }
  ]
}
```

### Migration Commands

```bash
# Export UDM bundle with templates
python manage.py export_udm_bundle <path>

# Import UDM bundle
python manage.py import_udm_bundle <path>
```

## Best Practices

### Template Design

1. **Keep templates simple** - Use Jinja2's control structures sparingly
2. **Localization support** - Include both German and English versions
3. **Test with examples** - Verify templates with `example_input`
4. **Use filters** - Apply `userinput` for plain text, `htmlquote` for HTML

### Policy Integration

1. **Use `post` phase** for notifications - Ensures the transaction commits before sending
2. **Provide context** - Pass relevant data in the policy context
3. **Handle failures** - Use `on_error: "log"` to prevent notification failures from blocking transactions

### Security

1. **Never expose secrets** - Templates should not access sensitive data
2. **Validate inputs** - Ensure all required data is present in the context
3. **Sanitize output** - Use appropriate filters for HTML vs plain text

## Troubleshooting

### Common Issues

1. **Template not found**
   - Check `MailTemplate` exists in database or file exists in `documentation/configuration/templates/`
   - Verify slug matches exactly

2. **Template render errors**
   - Check Jinja2 syntax
   - Verify context keys match template references
   - Ensure required filters are applied correctly

3. **Email not sent**
   - Check `phase` is `post` if using transaction
   - Verify recipient emails are resolved
   - Check Django email configuration

### Debugging

```python
# Check template exists
from userdefinedmodel.models import MailTemplate
MailTemplate.objects.filter(slug="proposal-accepted").exists()

# Test render
from userdefinedmodel.mailtemplates import render_mail_template
rendered = render_mail_template("proposal-accepted", {"proposal": {...}})
print(rendered.subject, rendered.text)
```

## Summary

**Key Points**:
1. **Two template sources**: Database (`MailTemplate`) and files (`.j2`)
2. **Sandboxed rendering**: Security via Jinja2 SandboxedEnvironment
3. **JSON context**: Round-trip ensures plain data only
4. **Send via action**: `send_notification` from Rego policies
5. **Filter support**: `timezone`, `isoformat`, `htmlquote`, `userinput`
6. **Bundle support**: Templates included in UDM bundles

