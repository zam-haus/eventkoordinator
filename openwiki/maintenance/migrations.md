---
type: maintenance_documentation
title: Migration System
description: Documentation for the entity migration system
---

# Migration System

The migration system allows moving entities between different UDM types and config versions while preserving data and maintaining integrity.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Backend Overview](../backend/overview.md) - Backend components

## Overview

The migration system supports:
- Moving entities between different UDM types
- Updating entities to new config versions
- Field mapping between source and target schemas
- Preview before execution
- Partial failure recovery

## Migration Record Structure

### UserDefinedModelEntityMigration

The migration record tracks the entire migration operation:

```python
class UserDefinedModelEntityMigration(MetaBase):
    class Action(models.TextChoices):
        MAP = "map"
        DISCARD = "discard"
        OVERFLOW = "overflow"
    
    user_defined_model_entity = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelEntity",
        on_delete=models.CASCADE,
        related_name="migrations",
    )
    source_version = models.ForeignKey(
        "userdefinedmodel.ConfigVersion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    target_user_defined_model_type = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelType",
        on_delete=models.PROTECT,
        related_name="received_entity_migrations",
    )
    target_version = models.ForeignKey(
        "userdefinedmodel.ConfigVersion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    executed_by = models.ForeignKey(
        "openid_user_management.OpenIDUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    bulk_plan = models.ForeignKey(
        "userdefinedmodel.BulkMigrationPlan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entity_migrations",
    )
```

### MigrationFieldMapping

Each field mapping records the action taken:

```python
class MigrationFieldMapping(MetaBase):
    migration = models.ForeignKey(
        UserDefinedModelEntityMigration,
        on_delete=models.CASCADE,
        related_name="field_mappings",
    )
    source_field = models.ForeignKey(
        "userdefinedmodel.DataField",
        on_delete=models.PROTECT,
        related_name="+",
    )
    action = models.CharField(max_length=10, choices=UserDefinedModelEntityMigration.Action)
    target_field = models.ForeignKey(
        "userdefinedmodel.DataField",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
```

**Action Types**:
- `map`: Field value mapped to target field
- `discard`: Field value discarded (no mapping)
- `overflow`: Field value moved to overflow field

## Migration Preview

### Preview Endpoint

```json
POST /api/udm/entities/{entity_id}/migration-preview/
```

**Response**:
```json
{
  "source_version_id": "uuid",
  "target_version_id": "uuid",
  "field_previews": [
    {
      "source_slug": "name",
      "source_data_type": "text_short",
      "suggested_action": "map",
      "suggested_target_slug": "name",
      "conflict_reason": null
    },
    {
      "source_slug": "custom_field",
      "source_data_type": "text_long",
      "suggested_action": "overflow",
      "suggested_target_slug": null,
      "conflict_reason": "Incompatible: text_long → integer"
    }
  ]
}
```

### Preview Logic

1. **Fetch source and target fields**
2. **Compare field types**:
   - Exact match → `map` action
   - Compatible types → `map` action
   - Incompatible → `overflow` action
3. **Generate field mapping suggestions**

### Compatible Type Mappings

The system allows these type conversions:
- `integer` → `float`
- `text_short` → `text_long`
- `text_long` → `text_markdown`
- `select_single` → `select_multi`
- `user_select` → `user_select_multi`
- `group_select` → `group_select_multi`
- `entity_select` → `entity_select_multi`

## Migration Execution

### Execute Endpoint

```json
POST /api/udm/entities/{entity_id}/migrate/
```

**Request**:
```json
{
  "target_user_defined_model_type_id": "uuid",
  "target_version_id": "uuid"
}
```

**Response**:
```json
{
  "id": "uuid",
  "config_version_id": "uuid",
  "field_values": [...]
}
```

### Execution Flow

1. **Lock entity** with `select_for_update(nowait=True)`
2. **Validate permissions** (superuser required)
3. **Create migration record** in database
4. **Process field mappings**:
   - For each mapped field: copy value to target
   - For overflow fields: store in overflow field
5. **Update entity** with new config version
6. **Record field mappings** in `MigrationFieldMapping`

### Transaction Handling

The entire migration runs in a single transaction:

```python
with transaction.atomic():
    # 1. Lock entity
    entity = (UserDefinedModelEntity.objects
              .select_for_update(nowait=True, of=("self",))
              .get(id=entity_id))
    
    # 2. Create migration record
    migration = UserDefinedModelEntityMigration.objects.create(...)
    
    # 3. Process field mappings
    for field_mapping in field_mappings:
        # Copy value, handle type conversion
        pass
    
    # 4. Update entity
    entity.config_version = target_version
    entity.save()
```

## Concurrent Execution Handling

### Locking Strategy

**Optimistic Locking**:
```python
entity = (UserDefinedModelEntity.objects
          .select_for_update(nowait=True, of=("self",))
          .get(id=entity_id))
except OperationalError:
    return _http409_concurrent()  # 409 Conflict
```

**Behavior**:
- If another migration is running → 409 Conflict
- Retry after waiting for lock
- `nowait=True` prevents blocking

### Duplicate Migration Prevention

**Pre-migration Check**:
1. Check if entity is already in a migration
2. Verify source version matches expected
3. Prevent concurrent migrations of same entity

**Example**:
```python
# Check if entity already has pending migration
existing = UserDefinedModelEntityMigration.objects.filter(
    user_defined_model_entity=entity,
    executed_at__isnull=True
).first()
if existing:
    raise ApiError(409, {"detail": "Entity already in migration"})
```

## Partial Failure Recovery

### Error Handling

The migration system handles partial failures:

```python
try:
    with transaction.atomic():
        # ... migration logic ...
except Exception as e:
    # Log error, but don't partial commit
    logger.error("Migration failed: %s", e)
    raise ApiError(500, {"detail": "Migration failed", "error": str(e)})
```

### Rollback on Failure

**Atomic Transaction**: If any step fails, the entire migration rolls back.

**No Partial Migrations**: The system never leaves an entity in a partially migrated state.

### Error Responses

```json
{
  "error": "Migration failed",
  "details": {
    "field": "name",
    "reason": "Invalid value for target field type"
  }
}
```

## Bulk Migration Plans

### Bulk Migration Structure

```python
class BulkMigrationPlan(MetaBase):
    class Status(models.TextChoices):
        DRAFT = "draft"
        RUNNING = "running"
        DONE = "done"
        PARTIAL = "partial"
    
    source_version = models.ForeignKey(
        "userdefinedmodel.ConfigVersion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    target_version = models.ForeignKey(
        "userdefinedmodel.ConfigVersion",
        on_delete=models.PROTECT,
        related_name="+",
    )
    user_defined_model_type_filter = models.ForeignKey(
        "userdefinedmodel.UserDefinedModelType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(max_length=10, choices=Status, default=Status.DRAFT)
    created_by = models.ForeignKey(...)
```

### Bulk Migration Flow

1. **Create bulk plan** (draft status)
2. **Preview entities** matching the filter
3. **Start migration** (status = RUNNING)
4. **Process entities** in batches
5. **Update status** to DONE or PARTIAL

### Migration Status

- **DRAFT**: Plan created but not started
- **RUNNING**: Migration in progress
- **DONE**: All entities migrated successfully
- **PARTIAL**: Some entities failed, but not all

## Submodel Field Validation

### Submodel Mapping

When migrating submodels, the system validates:

1. **Parent field exists** in target schema
2. **Submodel field types** are compatible
3. **Required fields** are present

### Validation Rules

```python
def _validate_submodel_mapping(source_submodel, target_parent_field):
    # Check parent field exists
    if not target_parent_field:
        raise MigrationError("Parent field not found in target")
    
    # Check field type compatibility
    for source_field in source_submodel.field_values.all():
        target_field = get_target_field(source_field)
        if not is_compatible(source_field, target_field):
            raise MigrationError(f"Incompatible field: {source_field.slug}")
```

## Best Practices

### Migration Best Practices

1. **Preview First**: Always run preview before executing
2. **Test with Sample**: Test migration on a few entities first
3. **Check Permissions**: Ensure superuser access
4. **Monitor Progress**: Check migration logs
5. **Handle Errors**: Review failed entities after migration

### Error Handling

1. **Retry Failed Migrations**: Run migration again for failed entities
2. **Manual Review**: Manually fix problematic entities
3. **Rollback Plan**: Have a plan to rollback if needed

## Summary

**Key Points**:
1. **Preview First**: Use `/migration-preview/` before executing
2. **Atomic Transaction**: Migration is all-or-nothing
3. **Locking**: Prevents concurrent migrations with `select_for_update`
4. **Error Recovery**: Partial failures don't leave entities in bad state
5. **Bulk Migration**: Support for migrating many entities at once
