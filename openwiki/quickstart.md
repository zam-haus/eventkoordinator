---
type: quickstart
title: OpenWiki Quickstart
description: Quick start guide for OpenWiki documentation and development
---

# OpenWiki Quickstart

Welcome to OpenWiki! This quickstart guide will help you get up and running with the documentation and development environment.

- **[README](../README.md)** - Main documentation index
- **[Quickstart Guide](quickstart.md)** - Get started with OpenWiki quickly

## Overview

OpenWiki is a documentation and knowledge base system built around the UserDefinedModel (UDM) framework. It provides:

- **Dynamic data modeling**: Create custom entities without code changes
- **Rego policy engine**: Embed business logic using Rego policies
- **Workflow management**: Define state transitions and approval workflows
- **Synchronization**: Connect with external systems (Pretix, CalDAV, iCal)

## Documentation Structure

The OpenWiki documentation is organized into the following sections:

### Quick Start
- **[Quickstart Guide](quickstart.md)** - Get started with OpenWiki quickly

### Core Concepts
- **[Form Tree and Data Fields](concepts/form_tree_and_data_fields.md)** - Form tree vs data fields
- **[Mail Templates](concepts/mail_templates.md)** - Email notification system
- **[Publishing](concepts/publishing.md)** - Configuration and workflow publishing

### API Documentation
- **[UDM API Overview](api/udm_overview.md)** - Overview and endpoints
- **[Configuration API](api/configs.md)** - Configuration management
- **[Entities API](api/entities.md)** - Entity CRUD operations
- **[Policies API](api/policies.md)** - Policy management
- **[Workflows API](api/workflows.md)** - Workflow management
- **[Bundle API](api/bundle.md)** - Import/export operations
- **[Staging API](api/staging.md)** - File staging for uploads
- **[Autocomplete API](api/autocomplete.md)** - Search endpoints

### Architecture
- **[Architecture Overview](architecture/overview.md)** - High-level system design
- **[Backend Components](backend/overview.md)** - Backend architecture
- **[Frontend Overview](frontend/overview.md)** - Frontend components

### Sync Targets
- **[Sync Overview](sync/overview.md)** - Synchronization infrastructure
- **[Pretix](sync/pretix.md)** - Pretix synchronization
- **[CalDAV](sync/caldav.md)** - CalDAV synchronization
- **[iCal](sync/ical.md)** - iCal synchronization

## Getting Started

### For Documentation Readers

1. **Start with the architecture overview** to understand the system design
2. **Learn the core concepts** to understand how UDM works
3. **Explore the API documentation** to understand available endpoints
4. **Check the backend/frontend overviews** to understand implementation details

### For Developers

1. **Set up the development environment** (see below)
2. **Understand the architecture** to know where to make changes
3. **Explore the codebase** to understand implementation
4. **Contribute to documentation** to improve understanding

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- Docker and Docker Compose (optional, for containerized setup)

### Quick Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

2. **Set up the environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Install dependencies**:
   ```bash
   npm install
   uv sync  # if using uv
   ```

4. **Run the development server**:
   ```bash
   # Start Docker containers (optional)
   docker compose up -d db redis
   
   # Start backend
   cd backend
   uv run manage.py runserver
   
   # Start frontend (in a separate terminal)
   npm run dev
   ```

5. **Access the application**:
   - Frontend: `http://localhost:5173`
   - Backend API: `http://localhost:8000/api/udm/`
   - Admin interface: `http://localhost:8000/admin/`

## Documentation Generation

This documentation is automatically generated from the codebase. To regenerate:

```bash
# Run the documentation generation script
./generate_docs.sh
```

## Contributing to Documentation

1. **Update the codebase** with new features or changes
2. **Regenerate the documentation** to reflect changes
3. **Review generated documentation** for accuracy
4. **Add manual documentation** for complex concepts
5. **Update the skeleton** to include new documentation

## Key Topics

### UserDefinedModel (UDM)

UDM is a dynamic data modeling framework that allows creating custom entities without code changes:

- Define custom field types and validation rules
- Attach workflows for state management
- Embed Rego policies for business logic
- Support internationalization

### Rego Policy Engine

The Rego policy engine evaluates policies against entities:

- Embed business logic in policies
- Evaluate policies during entity operations
- Use policy actions to trigger side effects
- Support policy versioning and migration

### Synchronization

The synchronization system connects with external systems:

- **Pretix**: Synchronize events and ticket sales
- **CalDAV**: Synchronize calendar events
- **iCal**: Synchronize calendar feeds

## Next Steps

1. **Read the architecture overview** to understand the system design
2. **Explore the core concepts** to understand UDM and policies
3. **Check the API documentation** to understand available endpoints
4. **Review the backend/frontend documentation** to understand implementation
5. **Set up the development environment** to start contributing

## Support

For issues or questions:

- Check the documentation for existing answers
- Review the codebase for implementation details
- Contact the development team

## Changelog

- **2024-01-01**: Initial quickstart guide created
