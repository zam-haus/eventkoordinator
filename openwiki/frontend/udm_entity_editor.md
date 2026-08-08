---
type: frontend_documentation
title: UDM Entity Editor
description: Documentation for the UDM Entity Editor component
---

# UDM Entity Editor

The UDM Entity Editor component allows users to create and edit entity instances.

## Overview

The Entity Editor provides a comprehensive interface for managing entity data, workflows, and synchronization with external systems.

## Architecture

### Component Structure

```
UDMEntityEditor.tsx
├── EntityHeader
│   ├── EntityTitle
│   │   ├── EntityName
│   │   └── EntityId
│   ├── EntityActions
│   │   ├── SaveButton
│   │   ├── CancelButton
│   │   └── DeleteButton
│   └── EntityStatus
│       ├── StatusIndicator
│       └── StatusLabel
├── EntityForm
│   ├── FormField
│   │   ├── StringField
│   │   ├── LongTextField
│   │   ├── NumberField
│   │   ├── BooleanField
│   │   ├── DateField
│   │   ├── SelectField
│   │   ├── MultiSelectField
│   │   ├── SubModelSelectField
│   │   ├── SubModelListField
│   │   ├── WorkflowField
│   │   └── FileField
│   ├── SubModelSection
│   │   ├── SubModelList
│   │   └── SubModelForm
│   └── ValidationErrors
│       ├── FieldError
│       └── Summary
├── EntityValidation
│   ├── ValidationStatus
│   │   ├── ValidIndicator
│   │   └── InvalidIndicator
│   └── ValidationMessages
│       ├── PolicyMessages
│       └── FieldErrors
└── EntitySync
    ├── SyncStatus
    │   ├── StatusBadge
    │   └── LastSyncTime
    └── SyncActions
        ├── PushButton
        ├── DeleteButton
        └── CompareButton
```

## Components

### 1. EntityHeader

Header component for entities.

#### Props

- `entity`: Entity object
- `onSave`: Callback for saving
- `onCancel`: Callback for canceling
- `onDelete`: Callback for deleting
- `loading`: Loading state
- `hasChanges`: Whether there are changes

#### Features

- Entity title
- Action buttons
- Status display
- Loading states

### 2. EntityForm

Form component for entities.

#### Props

- `entity`: Entity object
- `onChange`: Callback for field changes
- `onValidate`: Callback for validation
- `errors`: Validation errors
- `policies`: Policy messages

#### Features

- Field rendering
- Validation
- Policy enforcement
- Submodel support

### 3. FormField

Base component for form fields.

#### Props

- `field`: FieldDefinition object
- `value`: Field value
- `onChange`: Callback for value changes
- `errors`: Validation errors
- `policies`: Policy messages

#### Features

- Field rendering
- Validation
- Policy enforcement
- Localization support

### 4. SubModelSection

Section component for submodels.

#### Props

- `field`: FieldDefinition object
- `value`: Submodel value
- `onChange`: Callback for value changes
- `onAdd`: Callback for adding submodel
- `onDelete`: Callback for deleting submodel
- `errors`: Validation errors

#### Features

- Submodel list rendering
- Add/delete operations
- Nested validation
- Policy enforcement

### 5. EntityValidation

Validation component for entities.

#### Props

- `policies`: Policy evaluation results
- `errors`: Validation errors
- `loading`: Loading state

#### Features

- Validation status
- Policy messages
- Field errors
- Summary display

### 6. EntitySync

Synchronization component for entities.

#### Props

- `entity`: Entity object
- `onPush`: Callback for pushing to target
- `onDelete`: Callback for deleting from target
- `onCompare`: Callback for comparing
- `status`: Sync status
- `loading`: Loading state

#### Features

- Sync status display
- Push operations
- Delete operations
- Compare functionality

## Field Types

### 1. StringField

Text input field.

```typescript
function StringField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <input
                type="text"
                value={value || ''}
                onChange={(e) => onChange(e.target.value)}
                className={errors ? 'error' : ''}
            />
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 2. LongTextField

Text area field for longer text.

```typescript
function LongTextField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <textarea
                value={value || ''}
                onChange={(e) => onChange(e.target.value)}
                className={errors ? 'error' : ''}
                rows={4}
            />
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 3. NumberField

Number input field.

```typescript
function NumberField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <input
                type="number"
                value={value || ''}
                onChange={(e) => onChange(Number(e.target.value))}
                className={errors ? 'error' : ''}
            />
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 4. BooleanField

Checkbox field.

```typescript
function BooleanField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>
                <input
                    type="checkbox"
                    checked={value || false}
                    onChange={(e) => onChange(e.target.checked)}
                />
                {field.label}
            </label>
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 5. DateField

Date picker field.

```typescript
function DateField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <input
                type="date"
                value={value || ''}
                onChange={(e) => onChange(e.target.value)}
                className={errors ? 'error' : ''}
            />
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 6. SelectField

Single select dropdown field.

```typescript
function SelectField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <select
                value={value || ''}
                onChange={(e) => onChange(e.target.value)}
                className={errors ? 'error' : ''}
            >
                {field.choices.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                        {choice.label}
                    </option>
                ))}
            </select>
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 7. MultiSelectField

Multi-select dropdown field.

```typescript
function MultiSelectField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <select
                multiple
                value={value || []}
                onChange={(e) => onChange(
                    Array.from(e.target.selectedOptions, option => option.value)
                )}
                className={errors ? 'error' : ''}
            >
                {field.choices.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                        {choice.label}
                    </option>
                ))}
            </select>
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 8. SubModelSelectField

Submodel selection field.

```typescript
function SubModelSelectField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <select
                value={value || ''}
                onChange={(e) => onChange(e.target.value)}
                className={errors ? 'error' : ''}
            >
                <option value="">Select...</option>
                {field.submodels.map((submodel) => (
                    <option key={submodel.id} value={submodel.id}>
                        {submodel.name}
                    </option>
                ))}
            </select>
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 9. SubModelListField

List of submodels field.

```typescript
function SubModelListField({ field, value, onChange, errors }) {
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <div className="submodel-list">
                {value?.map((submodel, index) => (
                    <SubModelForm
                        key={index}
                        field={field}
                        value={submodel}
                        onChange={(val) => {
                            const newValues = [...value];
                            newValues[index] = val;
                            onChange(newValues);
                        }}
                        onDelete={() => {
                            const newValues = value.filter((_, i) => i !== index);
                            onChange(newValues);
                        }}
                    />
                ))}
                <button onClick={() => {
                    onChange([...(value || []), {}]);
                }}>
                    Add Submodel
                </button>
            </div>
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 10. WorkflowField

Workflow state field.

```typescript
function WorkflowField({ field, value, onChange, errors }) {
    const transitions = field.transitions || [];
    
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <select
                value={value || ''}
                onChange={(e) => onChange(e.target.value)}
                className={errors ? 'error' : ''}
            >
                <option value="">Select...</option>
                {transitions.map((transition) => (
                    <option key={transition.id} value={transition.target_state}>
                        {transition.label}
                    </option>
                ))}
            </select>
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

### 11. FileField

File upload field.

```typescript
function FileField({ field, value, onChange, errors }) {
    const [file, setFile] = useState(null);
    
    const handleFileChange = (e) => {
        const file = e.target.files[0];
        if (file) {
            setFile(file);
            onChange(file);
        }
    };
    
    return (
        <div className="form-group">
            <label>{field.label}</label>
            <input
                type="file"
                onChange={handleFileChange}
                className={errors ? 'error' : ''}
            />
            {value && <span>{value.name}</span>}
            {errors && <span className="error">{errors}</span>}
        </div>
    );
}
```

## State Management

### 1. Entity State

```typescript
interface EntityState {
    entity: Entity | null;
    changes: Record<string, any>;
    loading: boolean;
    error: string | null;
}

const useEntityStore = create<EntityState>((set) => ({
    entity: null,
    changes: {},
    loading: false,
    error: null,
    
    setEntity: (entity) => set({ entity }),
    setChanges: (changes) => set({ changes }),
    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    
    fetchEntity: async (entityId) => {
        set({ loading: true });
        try {
            const response = await api.get(`/entities/${entityId}/`);
            const entity = await response.json();
            set({ entity, loading: false });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    },
    
    updateField: (fieldSlug, value) => {
        set(state => ({
            changes: { ...state.changes, [fieldSlug]: value }
        }));
    },
    
    clearChanges: () => set({ changes: {} })
}));
```

### 2. Validation State

```typescript
interface ValidationState {
    isValid: boolean;
    errors: Record<string, string>;
    policyMessages: PolicyMessage[];
    loading: boolean;
}

const useValidationStore = create<ValidationState>((set) => ({
    isValid: false,
    errors: {},
    policyMessages: [],
    loading: false,
    
    setValid: (valid) => set({ isValid: valid }),
    setErrors: (errors) => set({ errors }),
    setPolicyMessages: (messages) => set({ policyMessages: messages }),
    setLoading: (loading) => set({ loading }),
    
    validate: async (entity, changes) => {
        set({ loading: true });
        try {
            const response = await api.post('/entities/validate/', {
                entity,
                changes
            });
            const result = await response.json();
            set({
                isValid: result.valid,
                errors: result.errors,
                policyMessages: result.policy_messages,
                loading: false
            });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

### 3. Sync State

```typescript
interface SyncState {
    status: SyncStatus | null;
    lastSync: string | null;
    loading: boolean;
}

const useSyncStore = create<SyncState>((set) => ({
    status: null,
    lastSync: null,
    loading: false,
    
    setStatus: (status) => set({ status }),
    setLastSync: (lastSync) => set({ lastSync }),
    setLoading: (loading) => set({ loading }),
    
    getStatus: async (entityId) => {
        set({ loading: true });
        try {
            const response = await api.get(`/sync/status/${entityId}/`);
            const status = await response.json();
            set({
                status: status.status,
                lastSync: status.last_sync,
                loading: false
            });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

## API Integration

### 1. Entity API

```typescript
// GET /api/udm/entities/{entity_id}/
async function fetchEntity(entityId) {
    const response = await api.get(`/entities/${entityId}/`);
    return await response.json();
}

// PATCH /api/udm/entities/{entity_id}/
async function updateEntity(entityId, data) {
    const response = await api.patch(`/entities/${entityId}/`, data);
    return await response.json();
}

// POST /api/udm/entities/
async function createEntity(data) {
    const response = await api.post('/entities/', data);
    return await response.json();
}

// DELETE /api/udm/entities/{entity_id}/
async function deleteEntity(entityId) {
    await api.delete(`/entities/${entityId}/`);
}
```

### 2. Validation API

```typescript
// POST /api/udm/entities/validate/
async function validateEntity(entity, changes) {
    const response = await api.post('/entities/validate/', {
        entity,
        changes
    });
    return await response.json();
}
```

### 3. Sync API

```typescript
// GET /api/udm/sync/status/{entity_id}/
async function getSyncStatus(entityId) {
    const response = await api.get(`/sync/status/${entityId}/`);
    return await response.json();
}

// POST /api/udm/sync/push/{entity_id}/{target_id}/
async function pushToTarget(entityId, targetId) {
    await api.post(`/sync/push/${entityId}/${targetId}/`);
}

// DELETE /api/udm/sync/delete/{entity_id}/{target_id}/
async function deleteFromTarget(entityId, targetId) {
    await api.delete(`/sync/delete/${entityId}/${targetId}/`);
}
```

## Testing

### 1. Unit Tests

```typescript
describe('EntityEditor', () => {
    it('renders correctly', () => {
        render(<EntityEditor />);
        expect(screen.getByText('Entity Editor')).toBeInTheDocument();
    });
    
    it('loads entity data', async () => {
        render(<EntityEditor />);
        expect(screen.getByText('Loading...')).toBeInTheDocument();
        
        await waitFor(() => {
            expect(screen.getByText('Entity Name')).toBeInTheDocument();
        });
    });
    
    it('validates field values', async () => {
        render(<EntityEditor />);
        
        // Fill in form field with invalid value
        fireEvent.change(screen.getByLabelText('Name'), {
            target: { value: '' }
        });
        
        // Click validate button
        fireEvent.click(screen.getByText('Validate'));
        
        // Verify validation error
        expect(screen.getByText('Name is required')).toBeInTheDocument();
    });
});

describe('SubModelListField', () => {
    it('adds submodel', () => {
        const value = [{ name: 'Submodel 1' }];
        const onChange = jest.fn();
        
        render(<SubModelListField value={value} onChange={onChange} />);
        
        fireEvent.click(screen.getByText('Add Submodel'));
        expect(onChange).toHaveBeenCalledWith([
            { name: 'Submodel 1' },
            {}
        ]);
    });
});
```

### 2. Integration Tests

```typescript
describe('EntityEditor Integration', () => {
    it('creates a new entity', async () => {
        render(<EntityEditor />);
        
        // Fill in form
        fireEvent.change(screen.getByLabelText('Name'), {
            target: { value: 'New Entity' }
        });
        
        // Click save button
        fireEvent.click(screen.getByText('Save'));
        
        // Verify entity was created
        expect(screen.getByText('Entity created')).toBeInTheDocument();
    });
    
    it('transitions workflow', async () => {
        render(<EntityEditor />);
        
        // Click workflow transition button
        fireEvent.click(screen.getByText('Submit'));
        
        // Verify workflow was transitioned
        expect(screen.getByText('Entity submitted')).toBeInTheDocument();
    });
});
```

## Best Practices

### Component Design

1. **Single Responsibility**: Components should have one responsibility
2. **Reusability**: Design components to be reusable
3. **Testability**: Design components to be testable
4. **Performance**: Optimize component rendering

### State Management

1. **Centralized**: Use centralized state management
2. **Immutable**: Keep state immutable
3. **Normalized**: Normalize data in state
4. **Caching**: Cache API responses

### API Integration

1. **Error Handling**: Handle errors gracefully
2. **Loading States**: Show loading states
3. **Validation**: Validate API responses
4. **Caching**: Cache API responses

### Field Validation

1. **Required Fields**: Mark required fields
2. **Validation Rules**: Implement validation rules
3. **Policy Enforcement**: Enforce policies
4. **Error Messages**: Show clear error messages

## Troubleshooting

### Common Issues

1. **Field Not Rendering**
   - Check field type
   - Verify field configuration
   - Review component code

2. **API Errors**
   - Check network connectivity
   - Verify API credentials
   - Review error messages

3. **Performance Issues**
   - Profile component rendering
   - Optimize component rendering
   - Use caching

## Future Enhancements

### Planned Features

1. **Real-time Validation**: Real-time field validation
2. **Drag and Drop**: Improve field ordering
3. **Advanced Search**: Enhanced search functionality
4. **Analytics**: User analytics
