---
type: backend_documentation
title: Management Commands
description: Documentation for Django management commands
---

# Management Commands

Django management commands for the UDM application.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Backend Overview](overview.md) - Backend components overview

## Available Commands

### 1. openapi_schema

Generates the OpenAPI schema for the API.

**Usage**:
```bash
python manage.py openapi_schema
```

**Options**:
- `--output FILE`: Output file path (default: stdout)

**Output**: OpenAPI 3.0 JSON schema

**Implementation**:
- Uses Django Ninja's OpenAPI generator
- Generates schema for all registered routers
- Includes all API endpoints

### 2. render_nginx_conf

Renders the Nginx configuration file.

**Usage**:
```bash
python manage.py render_nginx_conf
```

**Options**:
- `--template FILE`: Template file path
- `--output FILE`: Output file path (default: nginx.conf)
- `--env KEY=VALUE`: Environment variables

**Output**: Nginx configuration file

**Configuration Variables**:
- `APP_HOST`: Application host
- `APP_PORT`: Application port
- `STATIC_URL`: Static files URL
- `MEDIA_URL`: Media files URL

### 3. set_default_permissions

Sets default permissions for users and groups.

**Usage**:
```bash
python manage.py set_default_permissions
```

**Options**:
- `--user USERNAME`: Set permissions for specific user
- `--group NAME`: Set permissions for specific group
- `--all`: Set permissions for all users and groups

**Implementation**:
- Creates default groups (staff, user, anonymous)
- Assigns permissions to groups
- Sets default user permissions

### 4. render_rego_policies

Compiles and validates Rego policies.

**Usage**:
```bash
python manage.py render_rego_policies
```

**Options**:
- `--policy SLUG`: Validate specific policy
- `--all`: Validate all policies
- `--test`: Test policies with sample data

**Implementation**:
- Validates Rego syntax
- Compiles policies
- Tests policies with sample data

### 5. sync_data

Synchronizes data from external sources.

**Usage**:
```bash
python manage.py sync_data
```

**Options**:
- `--target TYPE`: Sync to specific target (pretix, ical, caldav)
- `--all`: Sync to all targets
- `--dry-run`: Dry run without changes

**Implementation**:
- Syncs events to external systems
- Updates sync status
- Handles sync errors

### 6. clear_cache

Clears all cached data.

**Usage**:
```bash
python manage.py clear_cache
```

**Options**:
- `--policy`: Clear policy cache
- `--entity`: Clear entity cache
- `--all`: Clear all caches

**Implementation**:
- Clears Rego policy cache
- Clears entity cache
- Clears Django cache

### 7. cleanup_staging_files

Cleans up expired staging files.

**Usage**:
```bash
python manage.py cleanup_staging_files
```

**Options**:
- `--days N`: Delete files older than N days
- `--dry-run`: Dry run without deletion

**Implementation**:
- Finds files older than 24 hours
- Deletes files from storage
- Updates database records

### 8. export_entities

Exports entities to a file.

**Usage**:
```bash
python manage.py export_entities
```

**Options**:
- `--type TYPE_ID`: Export specific type
- `--format FORMAT`: Export format (json, csv, yaml)
- `--output FILE`: Output file path

**Implementation**:
- Exports entity data
- Handles large datasets
- Supports multiple formats

### 9. import_entities

Imports entities from a file.

**Usage**:
```bash
python manage.py import_entities
```

**Options**:
- `--file FILE`: Input file path
- `--format FORMAT`: Input format (json, csv, yaml)
- `--type TYPE_ID`: Target type ID

**Implementation**:
- Imports entity data
- Handles validation
- Logs import errors

### 10. rebuild_index

Rebuilds search indexes.

**Usage**:
```bash
python manage.py rebuild_index
```

**Options**:
- `--type TYPE_ID`: Rebuild specific type
- `--all`: Rebuild all indexes

**Implementation**:
- Rebuilds Elasticsearch indexes
- Updates search data
- Handles large datasets

## Custom Management Commands

### User-defined Commands

Custom commands can be added in `management/commands/`:

```python
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Custom command description"
    
    def add_arguments(self, parser):
        parser.add_argument("--option", type=str, help="Option description")
    
    def handle(self, *args, **options):
        # Command logic here
        self.stdout.write("Command executed")
```

## Command Implementation Details

### Base Command Class

```python
from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = "Command description"
    
    def add_arguments(self, parser):
        # Add command-line arguments
        parser.add_argument("--verbose", action="store_true")
    
    def handle(self, *args, **options):
        # Command logic
        if options["verbose"]:
            self.stdout.write("Verbose output")
```

### Error Handling

```python
def handle(self, *args, **options):
    try:
        # Command logic
    except Exception as e:
        self.stderr.write(f"Error: {e}")
        raise CommandError("Command failed")
```

### Progress Reporting

```python
def handle(self, *args, **options):
    self.stdout.write("Starting...")
    
    for item in items:
        # Process item
        self.stdout.write(".", ending="")
    
    self.stdout.write("Done")
```

## Testing Commands

### Unit Tests

```python
from django.core.management import call_command
from django.test import TestCase

class ManagementCommandTests(TestCase):
    def test_openapi_schema(self):
        call_command("openapi_schema", output="schema.json")
        self.assertTrue(os.path.exists("schema.json"))
    
    def test_set_default_permissions(self):
        call_command("set_default_permissions")
        # Verify permissions set
```

### Integration Tests

```python
from django.test import TransactionTestCase

class CommandIntegrationTests(TransactionTestCase):
    def test_sync_data(self):
        call_command("sync_data", target="pretix", dry_run=True)
        # Verify no changes made
```

## Best Practices

### Command Design

1. **Single Responsibility**: Each command does one thing
2. **Idempotent**: Commands can be run multiple times
3. **Dry Run**: Support dry run mode
4. **Error Handling**: Comprehensive error handling

### Documentation

1. **Help Text**: Clear help text for all commands
2. **Options**: Well-documented command options
3. **Examples**: Usage examples

### Security

1. **Input Validation**: Validate all inputs
2. **Permission Checks**: Check user permissions
3. **Logging**: Log command execution

## Troubleshooting

### Common Issues

1. **Command Not Found**
   - Check command file name
   - Verify management directory structure
   - Check for syntax errors

2. **Command Fails**
   - Check error messages
   - Review command implementation
   - Verify dependencies

3. **Performance Issues**
   - Optimize database queries
   - Use batch processing
   - Add progress reporting

## Future Enhancements

### Planned Commands

1. **Data Migration**: Better data migration tools
2. **Backup/Restore**: Backup and restore functionality
3. **Health Checks**: Health check commands
4. **Metrics Export**: Export system metrics
