---
type: frontend_documentation
title: UDM Admin Component
description: Documentation for the UDM Admin component
---

# UDM Admin Component

The UDM Admin component allows administrators to manage user-defined models.

## Overview

The UDM Admin page provides a comprehensive interface for managing UDM types, field configurations, policies, and workflows.

## Architecture

### Component Structure

```
UDMAdmin.tsx
├── UDMTypeList
│   ├── UDMTypeCard
│   │   ├── TypeTitle
│   │   ├── TypeDescription
│   │   ├── TypeActions
│   │   └── TypeStats
│   └── UDMTypeEditor
│       ├── BasicInfoForm
│       ├── FieldConfigSelector
│       └── WorkflowSelector
├── FieldConfigList
│   ├── FieldConfigCard
│   │   ├── ConfigName
│   │   ├── ConfigDescription
│   │   ├── ConfigVersions
│   │   └── ConfigActions
│   └── FieldConfigEditor
│       ├── VersionSelector
│       ├── FieldDefinitionList
│       └── LanguageSelector
├── PolicyList
│   ├── PolicyCard
│   │   ├── PolicyName
│   │   ├── PolicySource
│   │   ├── TypeAssociations
│   │   └── PolicyActions
│   └── PolicyEditor
│       ├── PolicyName
│       ├── PolicySourceEditor
│       └── Validation
└── WorkflowList
    ├── WorkflowCard
    │   ├── WorkflowName
    │   ├── WorkflowStates
    │   ├── WorkflowTransitions
│   │   └── WorkflowActions
    └── WorkflowEditor
        ├── BasicInfo
        ├── StateEditor
        └── TransitionEditor
```

## Components

### 1. UDMTypeList

Lists all UDM types with their details.

#### Props

- `types`: Array of UDMType objects
- `loading`: Loading state
- `error`: Error message
- `onCreate`: Callback for creating new type
- `onEdit`: Callback for editing type
- `onDelete`: Callback for deleting type

#### Features

- Display type name, description, and stats
- CRUD operations
- Search and filtering
- Loading and error states

### 2. UDMTypeCard

Card component for UDM types.

#### Props

- `type`: UDMType object
- `onEdit`: Callback for editing
- `onDelete`: Callback for deleting

#### Features

- Display type information
- Action buttons
- Type stats
- Status indicators

### 3. UDMTypeEditor

Editor component for UDM types.

#### Props

- `initialData`: Initial data for the type
- `onSave`: Callback for saving
- `onCancel`: Callback for canceling

#### Features

- Basic info form
- Field config selector
- Workflow selector
- Validation

### 4. FieldConfigList

Lists all field configurations with their details.

#### Props

- `configs`: Array of FieldConfig objects
- `loading`: Loading state
- `error`: Error message
- `onCreate`: Callback for creating new config
- `onEdit`: Callback for editing config
- `onDelete`: Callback for deleting config

#### Features

- Display config name, description, and versions
- CRUD operations
- Search and filtering
- Loading and error states

### 5. FieldConfigCard

Card component for field configurations.

#### Props

- `config`: FieldConfig object
- `onEdit`: Callback for editing
- `onDelete`: Callback for deleting

#### Features

- Display config information
- Action buttons
- Version status
- Type associations

### 6. FieldConfigEditor

Editor component for field configurations.

#### Props

- `initialData`: Initial data for the config
- `onSave`: Callback for saving
- `onCancel`: Callback for canceling

#### Features

- Version selector
- Field definition list
- Language selector
- Validation

### 7. PolicyList

Lists all policies with their details.

#### Props

- `policies`: Array of Policy objects
- `loading`: Loading state
- `error`: Error message
- `onCreate`: Callback for creating new policy
- `onEdit`: Callback for editing policy
- `onDelete`: Callback for deleting policy

#### Features

- Display policy name and source
- CRUD operations
- Search and filtering
- Loading and error states

### 8. PolicyCard

Card component for policies.

#### Props

- `policy`: Policy object
- `onEdit`: Callback for editing
- `onDelete`: Callback for deleting

#### Features

- Display policy information
- Action buttons
- Type associations
- Validation status

### 9. PolicyEditor

Editor component for policies.

#### Props

- `initialData`: Initial data for the policy
- `onSave`: Callback for saving
- `onCancel`: Callback for canceling

#### Features

- Policy name
- Source code editor
- Validation
- Syntax highlighting

### 10. WorkflowList

Lists all workflows with their details.

#### Props

- `workflows`: Array of Workflow objects
- `loading`: Loading state
- `error`: Error message
- `onCreate`: Callback for creating new workflow
- `onEdit`: Callback for editing workflow
- `onDelete`: Callback for deleting workflow

#### Features

- Display workflow name, states, and transitions
- CRUD operations
- Search and filtering
- Loading and error states

### 11. WorkflowCard

Card component for workflows.

#### Props

- `workflow`: Workflow object
- `onEdit`: Callback for editing
- `onDelete`: Callback for deleting

#### Features

- Display workflow information
- Action buttons
- State count
- Transition count

### 12. WorkflowEditor

Editor component for workflows.

#### Props

- `initialData`: Initial data for the workflow
- `onSave`: Callback for saving
- `onCancel`: Callback for canceling

#### Features

- Basic info form
- State editor
- Transition editor
- Validation

## State Management

### 1. Type State

```typescript
interface TypeState {
    types: UDMType[];
    currentType: UDMType | null;
    loading: boolean;
    error: string | null;
}

const useTypeStore = create<TypeState>((set) => ({
    types: [],
    currentType: null,
    loading: false,
    error: null,
    
    setTypes: (types) => set({ types }),
    setCurrentType: (type) => set({ currentType: type }),
    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    
    fetchTypes: async () => {
        set({ loading: true });
        try {
            const response = await api.get('/types/');
            const types = await response.json();
            set({ types, loading: false });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

### 2. Config State

```typescript
interface ConfigState {
    configs: FieldConfig[];
    currentConfig: FieldConfig | null;
    loading: boolean;
    error: string | null;
}

const useConfigStore = create<ConfigState>((set) => ({
    configs: [],
    currentConfig: null,
    loading: false,
    error: null,
    
    setConfigs: (configs) => set({ configs }),
    setCurrentConfig: (config) => set({ currentConfig: config }),
    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    
    fetchConfigs: async () => {
        set({ loading: true });
        try {
            const response = await api.get('/configs/');
            const configs = await response.json();
            set({ configs, loading: false });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

### 3. Policy State

```typescript
interface PolicyState {
    policies: Policy[];
    currentPolicy: Policy | null;
    loading: boolean;
    error: string | null;
}

const usePolicyStore = create<PolicyState>((set) => ({
    policies: [],
    currentPolicy: null,
    loading: false,
    error: null,
    
    setPolicies: (policies) => set({ policies }),
    setCurrentPolicy: (policy) => set({ currentPolicy: policy }),
    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    
    fetchPolicies: async () => {
        set({ loading: true });
        try {
            const response = await api.get('/policies/');
            const policies = await response.json();
            set({ policies, loading: false });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

### 4. Workflow State

```typescript
interface WorkflowState {
    workflows: Workflow[];
    currentWorkflow: Workflow | null;
    loading: boolean;
    error: string | null;
}

const useWorkflowStore = create<WorkflowState>((set) => ({
    workflows: [],
    currentWorkflow: null,
    loading: false,
    error: null,
    
    setWorkflows: (workflows) => set({ workflows }),
    setCurrentWorkflow: (workflow) => set({ currentWorkflow: workflow }),
    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    
    fetchWorkflows: async () => {
        set({ loading: true });
        try {
            const response = await api.get('/workflows/');
            const workflows = await response.json();
            set({ workflows, loading: false });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

## API Integration

### 1. Type API

```typescript
// GET /api/udm/types/
async function fetchTypes() {
    const response = await api.get('/types/');
    return await response.json();
}

// POST /api/udm/types/
async function createType(data) {
    const response = await api.post('/types/', data);
    return await response.json();
}

// PATCH /api/udm/types/{id}/
async function updateType(id, data) {
    const response = await api.patch(`/types/${id}/`, data);
    return await response.json();
}

// DELETE /api/udm/types/{id}/
async function deleteType(id) {
    await api.delete(`/types/${id}/`);
}
```

### 2. Config API

```typescript
// GET /api/udm/configs/
async function fetchConfigs() {
    const response = await api.get('/configs/');
    return await response.json();
}

// POST /api/udm/configs/
async function createConfig(data) {
    const response = await api.post('/configs/', data);
    return await response.json();
}

// PATCH /api/udm/configs/{id}/
async function updateConfig(id, data) {
    const response = await api.patch(`/configs/${id}/`, data);
    return await response.json();
}

// DELETE /api/udm/configs/{id}/
async function deleteConfig(id) {
    await api.delete(`/configs/${id}/`);
}
```

### 3. Policy API

```typescript
// GET /api/udm/policies/
async function fetchPolicies() {
    const response = await api.get('/policies/');
    return await response.json();
}

// POST /api/udm/policies/
async function createPolicy(data) {
    const response = await api.post('/policies/', data);
    return await response.json();
}

// PUT /api/udm/policies/{slug}/
async function updatePolicy(slug, data) {
    const response = await api.put(`/policies/${slug}/`, data);
    return await response.json();
}

// DELETE /api/udm/policies/{slug}/
async function deletePolicy(slug) {
    await api.delete(`/policies/${slug}/`);
}
```

### 4. Workflow API

```typescript
// GET /api/udm/workflows/
async function fetchWorkflows() {
    const response = await api.get('/workflows/');
    return await response.json();
}

// POST /api/udm/workflows/
async function createWorkflow(data) {
    const response = await api.post('/workflows/', data);
    return await response.json();
}

// PATCH /api/udm/workflows/{id}/
async function updateWorkflow(id, data) {
    const response = await api.patch(`/workflows/${id}/`, data);
    return await response.json();
}

// DELETE /api/udm/workflows/{id}/
async function deleteWorkflow(id) {
    await api.delete(`/workflows/${id}/`);
}
```

## Testing

### 1. Unit Tests

```typescript
describe('UDMAdmin', () => {
    it('renders correctly', () => {
        render(<UDMAdmin />);
        expect(screen.getByText('UDM Admin')).toBeInTheDocument();
    });
    
    it('loads types on mount', async () => {
        render(<UDMAdmin />);
        expect(screen.getByText('Loading...')).toBeInTheDocument();
        
        await waitFor(() => {
            expect(screen.getByText('Type 1')).toBeInTheDocument();
        });
    });
});

describe('UDMTypeEditor', () => {
    it('saves type data', async () => {
        render(<UDMTypeEditor />);
        
        fireEvent.change(screen.getByLabelText('Name'), {
            target: { value: 'New Type' }
        });
        
        fireEvent.click(screen.getByText('Save'));
        
        expect(api.post).toHaveBeenCalledWith('/types/', {
            name: 'New Type'
        });
    });
});
```

### 2. Integration Tests

```typescript
describe('UDMAdmin Integration', () => {
    it('creates a new type', async () => {
        render(<UDMAdmin />);
        
        // Click create button
        fireEvent.click(screen.getByText('Create Type'));
        
        // Fill in form
        fireEvent.change(screen.getByLabelText('Name'), {
            target: { value: 'New Type' }
        });
        
        // Submit form
        fireEvent.click(screen.getByText('Save'));
        
        // Verify type was created
        expect(screen.getByText('New Type')).toBeInTheDocument();
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

## Troubleshooting

### Common Issues

1. **Component Not Rendering**
   - Check component props
   - Verify component registration
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

1. **Drag and Drop**: Improve UI interactions
2. **Advanced Search**: Enhance search functionality
3. **Analytics**: User analytics
4. **Real-time Updates**: WebSocket integration
