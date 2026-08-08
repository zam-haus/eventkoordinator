---
type: frontend_documentation
title: Frontend Overview
description: Overview of frontend components and architecture
---

# Frontend Overview

The frontend is built with React and provides the user interface for the UDM application.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [API Client](api_client.md) - API client integration
- [UDM Admin](udm_admin.md) - UDM Admin page documentation
- [UDM Entity Editor](udm_entity_editor.md) - UDM Entity Editor documentation

## Architecture

### Tech Stack

- **React**: UI library
- **TypeScript**: Type safety
- **Django Ninja**: API integration
- **CSS Modules**: Styling
- **Redux**: State management (optional)
- **React Router**: Navigation

### Component Structure

```
src/
├── components/
│   ├── common/
│   │   ├── Button.tsx
│   │   ├── Input.tsx
│   │   ├── Modal.tsx
│   │   └── Table.tsx
│   ├── udm/
│   │   ├── EntityEditor.tsx
│   │   ├── WorkflowEditor.tsx
│   │   └── PolicyEditor.tsx
│   └── ui/
│       ├── Header.tsx
│       └── Sidebar.tsx
├── pages/
│   ├── UDMAdmin.tsx
│   ├── UDMEntityEditor.tsx
│   ├── UDMBundleTab.tsx
│   └── UDMMigration.tsx
├── hooks/
│   ├── useAPI.ts
│   ├── useEntity.ts
│   └── useWorkflow.ts
├── services/
│   ├── api.ts
│   ├── auth.ts
│   └── sync.ts
├── utils/
│   ├── formatters.ts
│   └── validators.ts
└── types/
    ├── udm.ts
    └── api.ts
```

## Components

### 1. UDM Admin Page

The UDM Admin page allows administrators to manage user-defined models.

#### Features

- **Model Types**: Create and manage model types
- **Field Definitions**: Configure field definitions
- **Policies**: Manage Rego policies
- **Workflows**: Configure workflows
- **Permissions**: Manage permissions

#### Component Structure

```
UDMAdmin.tsx
├── UDMTypeList
│   ├── UDMTypeCard
│   └── UDMTypeEditor
├── FieldConfigList
│   ├── FieldConfigCard
│   └── FieldConfigEditor
├── PolicyList
│   ├── PolicyCard
│   └── PolicyEditor
└── WorkflowList
    ├── WorkflowCard
    └── WorkflowEditor
```

### 2. UDM Entity Editor

The UDM Entity Editor allows users to create and edit entity instances.

#### Features

- **Field Editing**: Edit field values
- **Workflow Transitions**: Trigger workflow transitions
- **Entity Validation**: Validate entity data
- **Policy Enforcement**: Enforce policies
- **Synchronization**: Sync to external systems

#### Component Structure

```
UDMEntityEditor.tsx
├── EntityHeader
│   ├── EntityTitle
│   └── EntityActions
├── EntityForm
│   ├── FormField
│   ├── SubModelField
│   └── WorkflowField
├── EntityValidation
│   ├── ValidationError
│   └── ValidationSummary
└── EntitySync
    ├── SyncStatus
    └── SyncActions
```

### 3. UDM Bundle Tab

The UDM Bundle Tab allows users to manage bundles for export/import.

#### Features

- **Export Bundles**: Export entity data
- **Import Bundles**: Import entity data
- **Bundle Validation**: Validate bundles
- **Bundle Status**: Track bundle status

#### Component Structure

```
UDMBundleTab.tsx
├── BundleList
│   ├── BundleCard
│   └── BundleEditor
├── ExportForm
│   ├── ExportSettings
│   └── ExportPreview
└── ImportForm
    ├── ImportSettings
    └── ImportPreview
```

### 4. UDM Migration

The UDM Migration interface allows users to migrate entity data.

#### Features

- **Migration Preview**: Preview migration changes
- **Execute Migration**: Execute migrations
- **Migration Status**: Track migration status
- **Rollback**: Rollback migrations

#### Component Structure

```
UDMMigration.tsx
├── MigrationList
│   ├── MigrationCard
│   └── MigrationEditor
├── MigrationPreview
│   ├── PreviewSummary
│   └── PreviewDetails
└── MigrationExecute
    ├── ExecutionSettings
    └── ExecutionStatus
```

## Services

### 1. API Service

The API service handles communication with the backend.

```typescript
class APIService {
    async get(url: string, options?: RequestInit): Promise<Response> {
        return fetch(`${API_BASE_URL}${url}`, options);
    }
    
    async post(url: string, body?: any, options?: RequestInit): Promise<Response> {
        return fetch(`${API_BASE_URL}${url}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            ...options
        });
    }
    
    async patch(url: string, body?: any, options?: RequestInit): Promise<Response> {
        return fetch(`${API_BASE_URL}${url}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            ...options
        });
    }
    
    async delete(url: string, options?: RequestInit): Promise<Response> {
        return fetch(`${API_BASE_URL}${url}`, {
            method: 'DELETE',
            ...options
        });
    }
}
```

### 2. Auth Service

The Auth service handles authentication.

```typescript
class AuthService {
    async login(username: string, password: string): Promise<void> {
        const response = await fetch('/api/auth/login/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        
        if (!response.ok) {
            throw new Error('Login failed');
        }
    }
    
    async logout(): Promise<void> {
        await fetch('/api/auth/logout/', { method: 'POST' });
    }
    
    async getToken(): Promise<string> {
        const session = await fetch('/api/auth/session/');
        const data = await session.json();
        return data.token;
    }
    
    async refreshToken(): Promise<void> {
        await fetch('/api/auth/token-refresh/', { method: 'POST' });
    }
}
```

### 3. Sync Service

The Sync service handles synchronization with external systems.

```typescript
class SyncService {
    async getTargets(): Promise<SyncTarget[]> {
        const response = await api.get('/sync/targets/');
        return await response.json();
    }
    
    async createSyncItem(seriesId: string, eventId: string, targetId: string): Promise<SyncItem> {
        const response = await api.post(
            `/sync/create/${seriesId}/${eventId}/${targetId}/`
        );
        return await response.json();
    }
    
    async getStatus(seriesId: string, eventId: string): Promise<EventSyncInfo> {
        const response = await api.get(`/sync/status/${seriesId}/${eventId}/`);
        return await response.json();
    }
    
    async pushToTarget(seriesId: string, eventId: string, targetId: string): Promise<void> {
        await api.post(`/sync/push/${seriesId}/${eventId}/${targetId}/`);
    }
    
    async deleteFromTarget(seriesId: string, eventId: string, targetId: string): Promise<void> {
        await api.delete(`/sync/delete/${seriesId}/${eventId}/${targetId}/`);
    }
}
```

## State Management

### 1. Entity State

```typescript
interface EntityState {
    entities: Entity[];
    currentEntity: Entity | null;
    loading: boolean;
    error: string | null;
}

const useEntityStore = create<EntityState>((set) => ({
    entities: [],
    currentEntity: null,
    loading: false,
    error: null,
    
    setEntities: (entities) => set({ entities }),
    setCurrentEntity: (entity) => set({ currentEntity: entity }),
    setLoading: (loading) => set({ loading }),
    setError: (error) => set({ error }),
    
    fetchEntities: async (typeId) => {
        set({ loading: true });
        try {
            const response = await api.get(`/entities/?type_id=${typeId}`);
            const entities = await response.json();
            set({ entities, loading: false });
        } catch (error) {
            set({ error: error.message, loading: false });
        }
    }
}));
```

### 2. Workflow State

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

## Hooks

### 1. useAPI

```typescript
function useAPI() {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    
    const request = useCallback(async (url: string, options?: RequestInit) => {
        setLoading(true);
        setError(null);
        
        try {
            const response = await fetch(`${API_BASE_URL}${url}`, options);
            const data = await response.json();
            
            if (!response.ok) {
                throw new Error(data.detail || 'Request failed');
            }
            
            return data;
        } catch (error) {
            setError(error.message);
            throw error;
        } finally {
            setLoading(false);
        }
    }, []);
    
    return { request, loading, error };
}
```

### 2. useEntity

```typescript
function useEntity(entityId: string) {
    const [entity, setEntity] = useState<Entity | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    
    useEffect(() => {
        async function fetchEntity() {
            setLoading(true);
            setError(null);
            
            try {
                const response = await api.get(`/entities/${entityId}/`);
                const entityData = await response.json();
                setEntity(entityData);
            } catch (error) {
                setError(error.message);
            } finally {
                setLoading(false);
            }
        }
        
        fetchEntity();
    }, [entityId]);
    
    return { entity, loading, error };
}
```

### 3. useWorkflow

```typescript
function useWorkflow(workflowId: string) {
    const [workflow, setWorkflow] = useState<Workflow | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    
    useEffect(() => {
        async function fetchWorkflow() {
            setLoading(true);
            setError(null);
            
            try {
                const response = await api.get(`/workflows/${workflowId}/`);
                const workflowData = await response.json();
                setWorkflow(workflowData);
            } catch (error) {
                setError(error.message);
            } finally {
                setLoading(false);
            }
        }
        
        fetchWorkflow();
    }, [workflowId]);
    
    return { workflow, loading, error };
}
```

## Styling

### 1. CSS Modules

```typescript
// Button.module.css
.button {
    padding: 10px 20px;
    border: none;
    border-radius: 4px;
    background-color: #007bff;
    color: white;
    cursor: pointer;
}

.button:hover {
    background-color: #0056b3;
}

.button:disabled {
    background-color: #ccc;
    cursor: not-allowed;
}

// Button.tsx
import styles from './Button.module.css';

export function Button({ children, onClick, disabled }: ButtonProps) {
    return (
        <button
            className={styles.button}
            onClick={onClick}
            disabled={disabled}
        >
            {children}
        </button>
    );
}
```

### 2. Theme

```typescript
// theme.ts
export const theme = {
    colors: {
        primary: '#007bff',
        secondary: '#6c757d',
        success: '#28a745',
        danger: '#dc3545',
        warning: '#ffc107',
        info: '#17a2b8',
        light: '#f8f9fa',
        dark: '#343a40'
    },
    spacing: {
        xs: 4,
        sm: 8,
        md: 16,
        lg: 24,
        xl: 32
    },
    borderRadius: {
        sm: 4,
        md: 8,
        lg: 16
    }
};
```

## Testing

### 1. Unit Tests

```typescript
describe('Button', () => {
    it('renders correctly', () => {
        render(<Button>Click me</Button>);
        expect(screen.getByText('Click me')).toBeInTheDocument();
    });
    
    it('calls onClick when clicked', () => {
        const handleClick = jest.fn();
        render(<Button onClick={handleClick}>Click me</Button>);
        
        fireEvent.click(screen.getByText('Click me'));
        expect(handleClick).toHaveBeenCalledTimes(1);
    });
});
```

### 2. Integration Tests

```typescript
describe('EntityEditor', () => {
    it('loads entity data', async () => {
        render(<EntityEditor entityId="123" />);
        
        expect(screen.getByText('Loading...')).toBeInTheDocument();
        
        await waitFor(() => {
            expect(screen.getByText('Entity Name')).toBeInTheDocument();
        });
    });
    
    it('saves entity data', async () => {
        render(<EntityEditor entityId="123" />);
        
        // Fill in form fields
        fireEvent.change(screen.getByLabelText('Name'), {
            target: { value: 'Updated Name' }
        });
        
        // Click save button
        fireEvent.click(screen.getByText('Save'));
        
        // Verify save was called
        expect(api.patch).toHaveBeenCalledWith('/entities/123/', {
            name: 'Updated Name'
        });
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
4. **Caching**: Implement caching for performance

### API Integration

1. **Error Handling**: Handle errors gracefully
2. **Loading States**: Show loading states
3. **Validation**: Validate API responses
4. **Caching**: Cache API responses

### Styling

1. **Consistency**: Maintain consistency in styling
2. **Responsiveness**: Design for responsiveness
3. **Accessibility**: Ensure accessibility
4. **Performance**: Optimize for performance

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

1. **Real-time Updates**: WebSocket integration
2. **Drag and Drop**: Improved UI interactions
3. **Advanced Search**: Enhanced search functionality
4. **Analytics**: User analytics
