---
type: architecture_documentation
title: Architecture Overview
description: High-level architecture overview of the OpenWiki application, including the UDM system, policy evaluation engine, frontend structure, and integration with external systems
resource: /openwiki/architecture/overview.md
tags: [architecture, system-design, udm, policy-engine]
timestamp: 2024-01-01T00:00:00Z
openwiki:
  roles: [architecture]
  change_kinds: [lifecycle]
  source_paths: [/openwiki/architecture/overview.md]
  invariants: [Architecture documentation must match actual implementation in source code]
  validation_commands: [grep -r "api/udm/" backend/]
---

# Architecture Overview

This document provides a high-level overview of the OpenWiki application architecture, including the UserDefinedModel (UDM) system, policy evaluation engine, frontend structure, and integration with external systems.

## System Architecture

The OpenWiki application follows a layered architecture with clear separation of concerns:

```mermaid
flowchart TD
    subgraph FrontendLayer["Frontend Layer"]
        direction TB
        A["React + TypeScript"] --> B["Redux State Management"]
    end
    
    subgraph APILayer["API Layer"]
        direction TB
        C["Django Ninja Routers"] --> D["Request Validation"]
        D --> E["Authentication & Authorization"]
    end
    
    subgraph BusinessLogic["Business Logic Layer"]
        direction TB
        F["Policy Engine"] --> G["Rego Policy Evaluation"]
        G --> H["Action System"]
    end
    
    subgraph DataLayer["Data Layer"]
        direction TB
        I["Django ORM"] --> J["PostgreSQL Database"]
    end
    
    subgraph ExternalIntegrations["External Integrations"]
        direction TB
        K["Sync Targets"] --> L["Pretix"]
        K --> M["iCal/CalDAV"]
        K --> N["Auth Providers"]
    end
    
    FrontendLayer --> APILayer
    APILayer --> BusinessLogic
    BusinessLogic --> DataLayer
    BusinessLogic --> ExternalIntegrations
```

```
+--------------------------------------------------+
|                  Frontend Layer                  |
|  (React + TypeScript + Django Ninja API)        |
+--------------------------------------------------+
|                  API Layer                       |
|  (Django Ninja Routers)                         |
+--------------------------------------------------+
|                 Business Logic                   |
|  (Policy Engine + Action System)                |
+--------------------------------------------------+
|                  Data Layer                      |
|  (Django ORM + PostgreSQL)                      |
+--------------------------------------------------+
|              External Integrations               |
|  (Sync Targets, Auth, etc.)                     |
+--------------------------------------------------+
```

## Core Components

### 1. UserDefinedModel (UDM) System

The UDM system provides a dynamic data modeling framework that allows users to define custom data structures without code changes.

**Key Features**:
- **Dynamic Schema**: Define custom field types and validation rules
- **Workflow Support**: Attach workflows to entities for state management
- **Policy Engine**: Embed Rego policies for business logic
- **Internationalization**: Support for multiple languages

**Architecture**:

```mermaid
graph LR
    A["UserDefinedModelType"] --> B["ConfigVersion"]
    B --> C["FieldDefinition"]
    B --> D["FormElementBinding"]
    B --> E["Workflow"]
    B --> F["Policy"]
    
    E --> G["WorkflowTransition"]
    
    A --> H["Entity"]
    H --> C
    H --> I["DataField"]
    H --> J["EntityNode"]
    
    C -- "mapping" --> D
    D -- "binds to" --> E
    F -- "applies to" --> A
```

```
UDM System
├── DataField (Schema Definition)
├── FormElement (UI Structure)
├── FormElementBinding (Mapping)
├── Workflow (State Machine)
├── Policy (Rego Rules)
└── ConfigVersion (Versioning)
```

### 2. Policy Evaluation Engine

The policy evaluation engine uses Rego (Open Policy Agent) to evaluate business rules against entities.

**Key Components**:
- **RegoSession**: Compiled Rego engine with caching
- **Policy Input Schema**: Structured input for policy evaluation
- **Action System**: Policy-driven actions (set field values, trigger transitions, send notifications)

**Features**:
- Thread-safe evaluation with thread-local caching
- Deep integration with Django ORM
- Automatic transaction handling
- Comprehensive error reporting

**Policy Evaluation Flow**:

```mermaid
flowchart TD
    A["Entity Save/Workflow Transition"] --> B["Trigger Policy Evaluation"]
    B --> C["Build Policy Input"]
    C --> D["Compile Rego Session"]
    D --> E["Evaluate Policies"]
    E --> F{"Policy Results"}
    F -->|Allow| G["Execute Policy Actions"]
    F -->|Deny| H["Raise Validation Error"]
    G --> I["Set Field Values"]
    G --> J["Trigger Workflow Transition"]
    G --> K["Send Notifications"]
    I --> L["Commit Transaction"]
    J --> L
    K --> L
    L --> M["Update Entity"]
```

### 3. Frontend Components

The frontend is built with React and provides a user interface for managing UDMs.

**Tech Stack**:
- **React**: UI library
- **TypeScript**: Type safety
- **Django Ninja**: API integration
- **Redux**: State management (optional)
- **React Router**: Navigation

**Main Components**:
- **UDM Admin**: Manage model types, field definitions, policies, and workflows
- **UDM Entity Editor**: Create and edit entity instances
- **UDM Bundle Tab**: Import/export operations
- **UDM Migration**: Migration interface for schema changes

**Frontend Component Architecture**:

```mermaid
graph TD
    A["Main App Component"] --> B["UDM Admin Container"]
    A --> C["Entity Editor Container"]
    A --> D["Navigation Component"]
    
    B --> B1["UDM Type Manager"]
    B --> B2["Field Definition Editor"]
    B --> B3["Policy Editor"]
    B --> B4["Workflow Designer"]
    
    C --> C1["Entity Form"]
    C --> C2["Validation Manager"]
    C --> C3["Entity Tree Viewer"]
    
    D --> D1["Sidebar Navigation"]
    D --> D2["Breadcrumb Component"]
    
    B1 --> E["API Client"]
    B2 --> E
    B3 --> E
    B4 --> E
    C1 --> E
    C2 --> E
    C3 --> E
```

### 4. API Layer

The API layer exposes all functionality through RESTful endpoints using Django Ninja.

**API Endpoint Structure**:

```mermaid
graph TD
    A["API Gateway"] --> B["/api/udm/Configs"]
    A --> C["/api/udm/Types"]
    A --> D["/api/udm/Workflows"]
    A --> E["/api/udm/Policies"]
    A --> F["/api/udm/Entities"]
    A --> G["/api/udm/StagingFiles"]
    A --> H["/api/udm/Bundle"]
    A --> I["/api/udm/Autocomplete"]
    
    B --> B1["Config CRUD"]
    B --> B2["Version Management"]
    
    C --> C1["Type CRUD"]
    C --> C2["Schema Validation"]
    
    D --> D1["Workflow CRUD"]
    D --> D2["Transition Management"]
    
    E --> E1["Policy CRUD"]
    E --> E2["Rego Validation"]
    
    F --> F1["Entity CRUD"]
    F --> F2["Bulk Operations"]
    F --> F3["Search & Filter"]
    
    G --> G1["File Upload"]
    G --> G2["File Staging"]
    
    H --> H1["Bundle Export"]
    H --> H2["Bundle Import"]
    
    I --> I1["Search Suggestions"]
    I --> I2["Autocomplete"]
```

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

### 5. External Integrations

The application integrates with external systems through sync targets.

**Sync Targets**:
- **Pretix**: Event ticketing system synchronization
- **iCal**: Calendar format synchronization
- **CalDAV**: Calendar protocol synchronization

**Sync Architecture**:

```mermaid
flowchart LR
    A["Entity Changes"] --> B["Sync Queue"]
    B --> C["Sync Worker 1"]
    B --> D["Sync Worker 2"]
    B --> E["Sync Worker N"]
    
    C --> F["Pretix Sync"]
    C --> G["iCal Sync"]
    C --> H["CalDAV Sync"]
    
    D --> F
    D --> G
    D --> H
    
    E --> F
    E --> G
    E --> H
    
    F --> I["External Event System"]
    G --> J["Calendar Applications"]
    H --> J
```

## Data Flow

### Entity Lifecycle

```mermaid
flowchart TD
    A["Entity Creation Request"] --> B["Frontend Validation"]
    B --> C["API Endpoint"]
    C --> D["Request Validation"]
    D --> E["Policy Evaluation Trigger"]
    E --> F["Policy Engine"]
    F --> G{"Validation Result"}
    G -->|Pass| H["Database Storage"]
    G -->|Fail| I["Validation Error Response"]
    H --> J["Entity Published"]
    J --> K["Sync Trigger"]
    K --> L["External Sync"]
    L --> M["Sync to External Systems"]
    
    subgraph UpdateFlow
        N["Entity Update Request"] --> B
    end
```

### Policy Evaluation

```mermaid
flowchart TD
    A["Entity Save/Workflow Transition"] --> B["Trigger Policy Evaluation"]
    B --> C["Build Policy Input"]
    C --> D["Load Rego Session"]
    D --> E["Evaluate Policies"]
    E --> F["Policy Results"]
    F --> G["Policy Actions"]
    G --> H["Set Field Values"]
    G --> I["Trigger Transitions"]
    G --> J["Send Notifications"]
    H --> K["Transaction Commit"]
    I --> K
    J --> K
    K --> L["Entity Updated"]
```

## Data Models

### Core Models

```mermaid
graph TD
    A["UserDefinedModelType"] --> B["ConfigVersion"]
    B --> C["FieldDefinition"]
    B --> D["FormElementBinding"]
    B --> E["Workflow"]
    B --> F["Policy"]
    
    E --> G["WorkflowTransition"]
    
    A --> H["Entity"]
    H --> C
    H --> I["DataField"]
    H --> J["EntityNode"]
    
    C -- "mapping" --> D
    D -- "binds to" --> E
    F -- "applies to" --> A
    
    style A fill:#f9f,stroke:#333
    style H fill:#9ff,stroke:#333
```

## Security

**Authentication**: Django session authentication with optional OIDC support

**Authorization**: Fine-grained permissions at the entity level

**Input Validation**: Comprehensive validation at all layers (API, model, policy)

**Security Flow**:

```mermaid
flowchart TD
    A["Request Incoming"] --> B["Authentication Layer"]
    B --> C{"Valid Session?"}
    C -->|Yes| D["Session Validation"]
    C -->|No| E["Reject Request"]
    D --> F["Permission Check"]
    F --> G{"Authorized?"}
    G -->|Yes| H["Request Processing"]
    G -->|No| I["403 Forbidden"]
    H --> J["API Validation"]
    J --> K["Policy Validation"]
    K --> L["Database Operation"]
```

## Scalability

**Caching**: Thread-local caching of compiled Rego policies

**Database**: PostgreSQL with Django ORM optimizations

**API**: Stateless API layer with efficient query patterns

**Horizontal Scaling Architecture**:

```mermaid
flowchart LR
    A["Load Balancer"] --> B["Web Server 1"]
    A --> C["Web Server 2"]
    A --> D["Web Server N"]
    
    B --> E["PostgreSQL Cluster"]
    C --> E
    D --> E
```


## Documentation References

For more detailed information on specific components, see:

- **[Backend Components](../backend/overview.md)** - Backend architecture and components
- **[Policy Evaluation Engine](../backend/policy_engine.md)** - Rego policy evaluation details
- **[Frontend Overview](../frontend/overview.md)** - Frontend architecture and components
- **[API Endpoints Reference](../api/endpoints.md)** - API endpoint documentation
- **[Testing Overview](../testing/overview.md)** - Testing strategy and documentation
- **[Sync Overview](../sync/overview.md)** - External system integration
- **[Publishing System](../concepts/publishing.md)** - Version and publishing management

