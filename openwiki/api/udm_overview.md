---
type: api_documentation
title: UDM API Overview
description: Overview of the UserDefinedModel API endpoints and architecture
---

# UDM API Overview

The UDM (UserDefinedModel) API provides a comprehensive set of endpoints for managing dynamic data models, workflows, policies, and entities. The API is mounted at `/api/udm/` and uses Django Ninja with typed schemas for OpenAPI compatibility.

## Architecture

The API is organized into domain-specific routers:

- **Configs**: Configuration management (types, drafts, versions)
- **Types**: UserDefinedModelType definitions
- **Workflows**: Workflow definitions and transitions
- **Policies**: Rego policy management
- **Entities**: CRUD operations for user-defined entities
- **Staging**: File staging for uploads
- **Bundles**: Import/export operations
- **Autocomplete**: Search endpoints

All endpoints use Django authentication by default and support typed response schemas for automatic OpenAPI documentation.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Policy Evaluation Engine](../backend/policy_engine.md) - Rego policy evaluation details
- [Backend Overview](../backend/overview.md) - Backend components

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Policy Evaluation Engine](../backend/policy_engine.md) - Rego policy evaluation details

## Architecture

The API follows a modular router-based architecture with Django Ninja:

```
/api/udm/
├── /configs/          - Configuration management
├── /types/            - UDM Type definitions
├── /workflows/        - Workflow management
├── /policies/         - Policy management
├── /entities/         - Entity CRUD operations
├── /staging-files/    - File staging for uploads
├── /bundle/           - Import/export operations
└── /autocomplete/     - Search endpoints
```

## Authentication

All endpoints require Django authentication by default. Authentication is provided via Django's session authentication middleware with optional OIDC support.

## Error Handling

The API uses a custom `ApiError` exception handler defined in `userdefinedmodel.api_helpers`. This handler allows exceptions raised inside `transaction.atomic()` blocks to abort the transaction automatically. Error responses return JSON with appropriate HTTP status codes.

### Error Response Format

```json
{
  "detail": "Error description",
  "code": "error.code.identifier"
}
```

## Response Codes

- `200 OK`: Successful GET/PUT/PATCH requests
- `201 Created`: Successful POST requests
- `204 No Content`: Successful DELETE requests
- `400 Bad Request`: Invalid request parameters
- `401 Unauthorized`: Not authenticated
- `403 Forbidden`: Authentication OK but insufficient permissions
- `404 Not Found`: Resource not found
- `409 Conflict`: Concurrent modification or validation conflict
- `422 Unprocessable Entity`: Policy validation errors or schema validation errors

## OpenAPI Documentation

The API automatically generates OpenAPI documentation accessible at `/api/udm/docs/` when the Django development server is running.

## Missing Endpoints (from skeleton review)

The following endpoints were identified as missing in the skeleton review:

### Config Version Lifecycle
- `POST /configs/{id}/publish/` - Publish a config version (publish endpoint)
- `POST /configs/drafts/{id}/replace/` - Replace a draft with new version

### Entity History
- `GET /entities/{id}/history/` - Get entity history with pagination

### Workflow Transitions
- `POST /entities/{id}/transition/` - Trigger a workflow transition
- `GET /entity-flow-diagram/` - Get the event flow diagram

### Policy Evaluation
- `POST /policies/evaluate/` - Evaluate policy on a specific entity
- `POST /types/{id}/evaluate/` - Evaluate type policies

### Bulk Migration
- `POST /entities/migrate/` - Create bulk migration job
- `GET /entities/migrations/` - List migration jobs
- `GET /entities/migrations/{id}/` - Get migration status
- `POST /entities/migrations/{id}/execute/` - Execute a migration

### Staging File Upload
- `POST /staging-files/` - Upload a file to staging
- `DELETE /staging-files/{id}/` - Delete a staging file

### Policy Document
- `GET /policies/{slug}/source/` - Get policy source code
- `GET /policies/{slug}/evaluation/` - Get policy evaluation results

## API Endpoints by Category

### Configuration Endpoints
- `GET /configs/` - List all configs
- `GET /configs/{id}/` - Get a specific config
- `POST /configs/` - Create a new config
- `PATCH /configs/{id}/` - Update a config
- `DELETE /configs/{id}/` - Delete a config
- `POST /configs/{id}/publish/` - Publish a config version

### Type Endpoints
- `GET /types/` - List all UDM types
- `GET /types/{id}/` - Get a specific type
- `POST /types/` - Create a new UDM type
- `PATCH /types/{id}/` - Update a UDM type
- `DELETE /types/{id}/` - Delete a UDM type

### Workflow Endpoints
- `GET /workflows/` - List all workflows
- `GET /workflows/{id}/` - Get a specific workflow
- `POST /workflows/` - Create a new workflow
- `PATCH /workflows/{id}/` - Update a workflow
- `DELETE /workflows/{id}/` - Delete a workflow

### Policy Endpoints
- `GET /policies/` - List all policies
- `GET /policies/{slug}/` - Get a specific policy
- `POST /policies/` - Create a new policy
- `PUT /policies/{slug}/` - Update a policy
- `DELETE /policies/{slug}/` - Delete a policy

### Entity Endpoints
- `GET /entities/` - List entities for a type
- `GET /entities/{id}/` - Get a specific entity
- `GET /entities/{id}/history/` - Get entity history
- `POST /entities/` - Create a new entity
- `PATCH /entities/{id}/` - Update an entity
- `DELETE /entities/{id}/` - Delete an entity
- `POST /entities/{id}/transition/` - Trigger a workflow transition

### Staging Endpoints
- `POST /staging-files/` - Upload a staging file
- `DELETE /staging-files/{id}/` - Delete a staging file

### Bundle Endpoints
- `GET /bundle/export/` - Export bundle (GET)
- `POST /bundle/import/` - Import bundle (POST)

### Autocomplete Endpoints
- `GET /autocomplete/users/` - Search users
- `GET /autocomplete/groups/` - Search groups
- `GET /autocomplete/entities/` - Search entities

## API Contract

All endpoints use typed response schemas for OpenAPI compatibility:
- Success responses return the appropriate schema type with status code
- Error responses return JSON with appropriate status codes
- Error responses pass through Ninja unchanged for automatic OpenAPI generation

## Thread Safety

The Rego evaluation engine is thread-local due to PyO3 restrictions. The engine cache is managed per-thread to ensure thread safety.
