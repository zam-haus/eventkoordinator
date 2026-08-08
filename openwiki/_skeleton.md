---
type: wiki_skeleton
title: Wiki Skeleton for UDM Application
description: Skeleton structure for the UDM application documentation wiki
---

# Wiki Skeleton for UDM Application

## Quick Start
- /openwiki/quickstart.md - High-level overview, navigation, and task routing

## Core Concepts
- /openwiki/architecture/overview.md - System architecture, layers, and technology stack
- /openwiki/concepts/udm.md - UserDefinedModel (UDM) concept and data model
- /openwiki/concepts/policies.md - Rego policy engine and evaluation model
- /openwiki/concepts/form_tree_and_data_fields.md - Form tree vs data fields split

## UDM API Documentation
- /openwiki/api/udm_overview.md - UDM API overview and endpoints
- /openwiki/api/configs.md - Configuration API (types, drafts, versions)
- /openwiki/api/entities.md - Entity CRUD and workflow API
- /openwiki/api/policies.md - Policy management API
- /openwiki/api/workflows.md - Workflow management API
- /openwiki/api/bundle.md - Bundle API for exports/imports
- /openwiki/api/staging.md - Staging and migration API
- /openwiki/api/autocomplete.md - Autocomplete API endpoints

## Backend Components
- /openwiki/backend/overview.md - Backend architecture and components
- /openwiki/backend/policy_engine.md - Policy evaluation engine
- /openwiki/backend/actions.md - Policy actions system
- /openwiki/backend/models/overview.md - Model overview and relationships
  - /openwiki/backend/models/udmtype.md - UserDefinedModelType
  - /openwiki/backend/models/config.md - Configuration models (FieldConfig, ConfigVersion, etc.)
  - /openwiki/backend/models/node.md - Entity node and field value models
  - /openwiki/backend/models/workflow.md - Workflow definition and version models
  - /openwiki/backend/models/policy.md - Policy models
  - /openwiki/backend/models/rules.md - Validation rule models
  - /openwiki/backend/models/migration.md - Migration models

## Frontend Components
- /openwiki/frontend/overview.md - Frontend architecture and structure
- /openwiki/frontend/udm_admin.md - UDM Admin page
- /openwiki/frontend/udm_entity_editor.md - UDM Entity Editor
- /openwiki/frontend/udm_bundle_tab.md - UDM Bundle Tab
- /openwiki/frontend/udm_migration.md - UDM Migration interface
- /openwiki/frontend/event_editor.md - Event Editor
- /openwiki/frontend/workflow_editor.md - Workflow Editor

## Sync Targets (high-level)
- /openwiki/sync/overview.md - Sync infrastructure overview (Pretix, CalDAV, iCal)
- /openwiki/sync/pretix.md - Pretix synchronization documentation
- /openwiki/sync/ical.md - iCal synchronization documentation
- /openwiki/sync/caldav.md - CalDAV synchronization documentation

## User Management
- /openwiki/openid_users.md - OpenID User Management API

## Testing
- /openwiki/testing/overview.md - Testing strategy and approach
- /openwiki/testing/backend_tests.md - Backend test suite
- /openwiki/testing/frontend_tests.md - Frontend/Playwright tests

## Migration and Maintenance
- /openwiki/maintenance/migrations.md - Migration system documentation
- /openwiki/maintenance/metrics.md - Metrics and monitoring

## Backlog
- /openwiki/backlog.md - Deferred or future work items