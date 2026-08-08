---
type: summary
title: Documentation Creation Summary
description: Summary of all documentation created
---

# Documentation Created Summary

## Mail Templates Documentation Added

The following documentation has been created to address the mail templates and notification system:

### 1. `/openwiki/concepts/mail_templates.md` - Mail Templates System Documentation

Comprehensive documentation of the mail templates system including:
- Overview of mail templates in UserDefinedModel
- Two template sources: Database (`MailTemplate`) and files (`.j2`)
- Template context structure and available keys
- Jinja2 filters and globals
- Template rendering process
- Security considerations (sandboxed environment)
- Send notification action from Rego policies
- Template examples and best practices
- Troubleshooting guide

**Key Topics Covered**:
- Mail template types (UDM MailTemplate vs file-based)
- Template context structure (core keys, globals, filters)
- Template rendering process (sandboxed Jinja2)
- Send notification action (`send_notification`)
- Recipient resolution (user_select fields and extra_recipients)
- Template examples (simple, datetime, review comments)
- Migration and bundle support
- Best practices and security considerations
- Troubleshooting common issues

### 2. `/openwiki/_skeleton.md` - Updated Skeleton

Updated skeleton structure to include mail templates documentation under "Core Concepts".

## Files Created/Updated

### Documentation Files

1. **`/openwiki/concepts/mail_templates.md`** - Mail templates system documentation
2. **`/openwiki/_skeleton.md`** - Updated to include mail_templates link

## Documentation Coverage

The mail templates documentation covers:

### 1. Template System Architecture

- **Database templates** (`MailTemplate` model)
- **File-based templates** (`.j2` files in `documentation/configuration/templates/`)
- **Two-phase rendering** (JSON round-trip for security)

### 2. Template Context

- **Core keys**: `context`, `input`, `entity`, `fields`, `node`, `user`, `trigger`, `phase`, etc.
- **Globals**: `frontend_base_url`, `site_name`, `default_from_email`, `now`
- **Filters**: `timezone`, `isoformat`, `htmlquote`, `userinput`

### 3. Integration with Rego Policies

- **Send notification action** (`send_notification`)
- **Template-based vs inline sending**
- **Recipient resolution** (user_select fields and extra_recipients)

### 4. Security

- **Sandboxed environment** (Jinja2 SandboxedEnvironment)
- **JSON round-trip** (ensures plain data only)
- **Settings not exposed** (prevents secret access)

### 5. Migration and Bundles

- **UDM bundle support** (`udm_mailtemplates`)
- **Migration commands** (export/import)
- **Version control** for templates

## Documentation Structure

```
/openwiki/
├── concepts/             # Core concept documentation
│   ├── udm.md            # UserDefinedModel overview
│   ├── policies.md       # Rego policy engine
│   ├── form_tree_and_data_fields.md
│   ├── mail_templates.md # Mail templates system (NEW)
│   └── publishing.md     # Publishing system
├── api/                  # API documentation
│   ├── udm_overview.md   # Overview
│   ├── configs.md        # Configuration API
│   ├── entities.md       # Entity API
│   ├── policies.md       # Policy API
│   ├── workflows.md      # Workflow API
│   └── endpoints.md      # Comprehensive API reference
├── backend/              # Backend documentation
│   ├── overview.md       # Overview
│   ├── actions.md        # Policy actions
│   ├── policy_engine.md  # Policy engine
│   ├── management_commands.md
│   └── models/
│       └── overview.md   # Model overview
├── sync/                 # Sync documentation
│   ├── overview.md       # Overview
│   ├── pretix.md         # Pretix sync
│   ├── ical.md           # iCal sync
│   └── caldav.md         # CalDAV sync
├── openid_users.md       # OpenID User Management
├── frontend/             # Frontend documentation
│   ├── overview.md       # Overview
│   ├── udm_admin.md      # UDM Admin
│   └── udm_entity_editor.md
├── testing/              # Testing documentation
│   ├── overview.md       # Overview
│   ├── backend_tests.md  # Backend tests
│   └── frontend_tests.md # Frontend tests
└── _skeleton.md          # Updated skeleton
```

## Next Steps

The updated documentation provides:

1. **Mail Templates Reference** - Complete reference for template system
2. **Rego Integration** - How to use `send_notification` action
3. **Security Best Practices** - Proper sandboxing and data handling
4. **Migration Support** - Bundle export/import for templates

All documentation follows the existing skeleton structure and includes proper YAML front matter for consistency.

