---
type: frontend_documentation
title: Frontend API Client
description: Documentation for the frontend API client
---

# Frontend API Client

The frontend API client provides typed interfaces for interacting with the backend API.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Frontend Overview](overview.md) - Frontend architecture and components
- [API Endpoints Reference](../api/endpoints.md) - API endpoint documentation

## Entity Search

### Search Endpoint

```typescript
GET /api/udm/entities/
```

**Query Parameters**:
- `type_id` (required): UUID of the UDM type
- `page_size` (optional): Page size (default: 200, max: 200)

**Response**:
```typescript
interface EntityList {
  id: string;
  type_id: string;
  config_version_id: string;
  field_values: Record<string, any>;
  children: Record<string, EntityList[]>;
  workflow_state: string | null;
  policy_messages?: PolicyMessage[];
}
```

### Pagination

```typescript
interface EntityListResponse {
  results: EntityList[];
  count: number;
  next: string | null;
  previous: string | null;
}
```

**Pagination Logic**:
- Default page size: 200
- Maximum page size: 200
- Use `next` and `previous` URLs for pagination

### Example Usage

```typescript
import { fetchEntities } from './api/client';

const entities = await fetchEntities({
  type_id: 'c3b8e7a9-8f1d-4e92-9b3a-2d8c7f1e6a9b',
  page_size: 50,
});
```

## Autocomplete with Typed Filters

### Autocomplete Endpoint

```typescript
GET /api/udm/autocomplete/
```

**Query Parameters**:
- `type_id` (required): UUID of the UDM type
- `field_slug` (required): Field slug to autocomplete
- `search` (optional): Search term
- `page_size` (optional): Page size (default: 20)

**Response**:
```typescript
interface AutocompleteResult {
  results: AutocompleteOption[];
  count: number;
}

interface AutocompleteOption {
  value: string | number;
  label: string;
}
```

### Typed Filters

The autocomplete supports typed filters:

```typescript
interface AutocompleteFilters {
  type_id: string;
  field_slug: string;
  search?: string;
  page_size?: number;
  // Additional typed filters based on field type
}

// For user_select fields
interface UserAutocompleteFilters extends AutocompleteFilters {
  exclude_user_ids?: string[];
}

// For entity_select fields
interface EntityAutocompleteFilters extends AutocompleteFilters {
  exclude_entity_ids?: string[];
}
```

### Example Usage

```typescript
import { fetchAutocomplete } from './api/client';

const users = await fetchAutocomplete<UserAutocompleteFilters>({
  type_id: 'c3b8e7a9-8f1d-4e92-9b3a-2d8c7f1e6a9b',
  field_slug: 'owner',
  search: 'john',
  exclude_user_ids: ['current-user-id'],
});
```

## Pagination

### Standard Pagination

```typescript
interface PaginatedResponse<T> {
  results: T[];
  count: number;
  next: string | null;
  previous: string | null;
}
```

### Example Usage

```typescript
import { fetchPaginated } from './api/client';

const entities = await fetchPaginated<EntityList>('/api/udm/entities/', {
  type_id: 'c3b8e7a9-8f1d-4e92-9b3a-2d8c7f1e6a9b',
  page_size: 50,
});

// Load next page
if (entities.next) {
  const nextEntities = await fetchPaginated<EntityList>(entities.next);
}
```

## Error Handling

### Error Types

The client handles multiple error types:

#### 409 Conflict (Concurrent Modification)

```typescript
interface ConflictError {
  error: string;
}

// HTTP 409 - Concurrent modification detected
try {
  await api.patchEntity(entityId, patch);
} catch (error: any) {
  if (error.status === 409) {
    console.error('Concurrent modification detected');
    // Refresh entity and retry
  }
}
```

#### 422 Unprocessable Entity (Policy Errors)

```typescript
interface PolicyError {
  policy_messages: PolicyMessage[];
}

interface PolicyMessage {
  level: 'critical' | 'error' | 'warning' | 'info' | 'debug';
  text: string;
  highlight_fields: string[];
}

// HTTP 422 - Policy validation error
try {
  await api.patchEntity(entityId, patch);
} catch (error: any) {
  if (error.status === 422) {
    console.error('Policy errors:', error.response.policy_messages);
    // Display policy_messages to user
  }
}
```

#### 400 Bad Request (Validation Errors)

```typescript
interface ValidationError {
  errors: Record<string, string[]>;
}

// HTTP 400 - Validation error
try {
  await api.patchEntity(entityId, patch);
} catch (error: any) {
  if (error.status === 400) {
    console.error('Validation errors:', error.response.errors);
    // Display field errors
  }
}
```

### Error Response Examples

**409 Conflict**:
```json
{
  "error": "Concurrent modification detected."
}
```

**422 Policy Error**:
```json
{
  "policy_messages": [
    {
      "level": "error",
      "text": "Cannot transition to 'published' without manager approval",
      "highlight_fields": ["manager_approval"]
    }
  ]
}
```

**400 Validation Error**:
```json
{
  "errors": {
    "name": ["This field is required."],
    "email": ["Enter a valid email address."]
  }
}
```

## Schema Validation

### Validation Rules

The client enforces schema validation based on field types:

1. **Text Fields**: Length limits
2. **Number Fields**: Range validation
3. **Select Fields**: Valid choices validation
4. **User/Group/Entity Select**: Valid PK validation
5. **Workflow Fields**: State machine validation

### Validation Schema

```typescript
interface FieldDefinition {
  slug: string;
  data_type: FieldDataType;
  is_localized: boolean;
  type_config?: any;
}

interface FieldDataType {
  TEXT_SHORT: 'text_short';
  TEXT_LONG: 'text_long';
  TEXT_MARKDOWN: 'text_markdown';
  INTEGER: 'integer';
  FLOAT: 'float';
  BOOLEAN: 'boolean';
  DATE: 'date';
  DATETIME: 'datetime';
  SELECT_SINGLE: 'select_single';
  SELECT_MULTI: 'select_multi';
  USER_SELECT: 'user_select';
  USER_SELECT_MULTI: 'user_select_multi';
  GROUP_SELECT: 'group_select';
  GROUP_SELECT_MULTI: 'group_select_multi';
  ENTITY_SELECT: 'entity_select';
  ENTITY_SELECT_MULTI: 'entity_select_multi';
  WORKFLOW: 'workflow';
  IMAGE: 'image';
  FILE: 'file';
}
```

### Validation Examples

**Text Field**:
```typescript
const maxLength = fieldDefinition.type_config?.max_length || 255;
if (value.length > maxLength) {
  throw new ValidationError('Value exceeds maximum length');
}
```

**Select Field**:
```typescript
const validChoices = fieldDefinition.type_config?.choices || [];
if (!validChoices.includes(value)) {
  throw new ValidationError('Invalid choice');
}
```

**Workflow Field**:
```typescript
const validTransitions = workflowDefinition.transitions.map(t => t.name);
if (!validTransitions.includes(transitionName)) {
  throw new ValidationError('Invalid transition');
}
```

## Type IDs and IDs Parameters

### Malformed UUID Handling

The client validates UUIDs in type_ids and ids parameters:

```typescript
function isValidUUID(id: string): boolean {
  const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  return uuidRegex.test(id);
}

// In API calls
if (!isValidUUID(typeId)) {
  throw new ValidationError('Invalid UUID format for type_id');
}
```

### Error for Malformed UUID

```typescript
interface InvalidUUIDError {
  error: string;
  field: string;
  value: string;
}

// HTTP 400 - Malformed UUID
try {
  await api.getEntity(entityId);
} catch (error: any) {
  if (error.status === 400 && error.response.field === 'entity_id') {
    console.error('Invalid UUID:', error.response.value);
  }
}
```

## Schema Validation Constraints

### Field Validation

1. **Required Fields**: Check for required fields before submission
2. **Format Validation**: Validate format (email, URL, etc.)
3. **Length Validation**: Check min/max length for text fields
4. **Numeric Validation**: Check min/max for numeric fields
5. **Choice Validation**: Check allowed values for select fields

### Workflow Validation

1. **State Machine**: Only allow valid transitions from current state
2. **Required Fields**: Check required fields for transition
3. **Field Dependencies**: Validate field dependencies

### Example Validation

```typescript
function validateEntity(entity: EntityCreateInput, type: UDMType): ValidationError[] {
  const errors: ValidationError[] = [];
  
  // Validate required fields
  for (const field of type.field_definitions) {
    if (field.is_required && !entity.field_values[field.slug]) {
      errors.push({
        field: field.slug,
        message: 'This field is required',
      });
    }
  }
  
  // Validate field types
  for (const [slug, value] of Object.entries(entity.field_values)) {
    const field = type.field_definitions.find(f => f.slug === slug);
    if (!field) {
      errors.push({
        field: slug,
        message: 'Unknown field',
      });
      continue;
    }
    
    // Validate based on field type
    if (!validateFieldValue(value, field)) {
      errors.push({
        field: slug,
        message: `Invalid value for ${field.data_type} field`,
      });
    }
  }
  
  return errors;
}
```

## Summary

**Key Points**:
1. **Entity Search**: Supports pagination with page_size limit
2. **Autocomplete**: Typed filters for user, entity, group selection
3. **Error Handling**: 409 (concurrent), 422 (policy), 400 (validation)
4. **UUID Validation**: Malformed UUIDs return 400 error
5. **Schema Validation**: Enforce field type constraints
