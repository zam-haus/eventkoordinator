---
type: sync_documentation
title: Sync Target Overview
description: Overview of sync targets infrastructure
---

# Sync Target Overview

The sync infrastructure enables synchronization with external systems.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Pretix Sync](pretix.md) - Pretix synchronization details
- [iCal Sync](ical.md) - iCal synchronization details
- [CalDAV Sync](caldav.md) - CalDAV synchronization details

## Architecture

### Base Classes

#### SyncBaseTarget

Base class for all sync targets.

**Key Features**:
- Polymorphic meta base class
- Supports multiple sync target types
- Public properties API (excludes secrets)
- Status tracking

**Methods**:
- `get_real_instance()`: Get concrete target instance
- `get_status(entity)`: Get sync status for entity
- `create_new_sync_item(entity)`: Create new sync item

**Properties**:
- `type`: Target type name
- `public_properties`: Non-secret properties

#### SyncBaseItem

Base class for sync items.

**Key Features**:
- Links entities to targets
- Status tracking
- Diff calculation

**Methods**:
- `get_status()`: Get sync status
- `get_diff()`: Calculate diff
- `push()`: Push to target
- `delete()`: Delete from target

### Sync Target Types

1. **Pretix**: Event ticketing system
2. **iCal**: Calendar format
3. **CalDAV**: Calendar protocol

## Sync Process

### 1. Status Check

```python
target = PretixSyncTarget.objects.get(pk=target_id)
status = target.get_status(entity)
# Returns: no entry exists | creation pending | status unknown | entry up-to-date | entry differs
```

### 2. Diff Calculation

```python
diff = target.get_diff(entity)
# Returns: list of field differences
```

### 3. Push Operation

```python
item = target.create_new_sync_item(entity)
item.push()
# Pushes entity data to target system
```

### 4. Delete Operation

```python
item.delete()
# Removes entry from target system
```

## Sync Targets

### 1. Pretix

#### Configuration

```json
{
  "url": "https://pretix.example.com",
  "api_token": "secret",
  "organizer_slug": "org",
  "event_slug": "event"
}
```

#### Features

- Sync event details
- Sync ticket information
- Sync attendee data
- Sync status updates

#### Status Mapping

- `no entry exists`: Event not in Pretix
- `creation pending`: Event creation in progress
- `status unknown`: Unknown status
- `entry up-to-date`: Event synchronized
- `entry differs`: Event needs update

### 2. iCal

#### Configuration

```json
{
  "url": "https://calendar.example.com",
  "calendar_id": "calendar",
  "username": "user",
  "password": "secret"
}
```

#### Features

- Sync event calendar entries
- Support iCal format
- Timezone handling
- Recurring events

#### Status Mapping

Same as Pretix.

### 3. CalDAV

#### Configuration

```json
{
  "url": "https://caldav.example.com",
  "username": "user",
  "password": "secret"
}
```

#### Features

- Sync to CalDAV servers
- Support calendar collections
- Event synchronization
- Property updates

#### Status Mapping

Same as Pretix.

## Sync Status

### Status Types

1. **NO_ENTRY_EXISTS**: No entry in target system
2. **CREATION_PENDING**: Entry creation in progress
3. **STATUS_UNKNOWN**: Unknown status
4. **ENTRY_UP_TO_DATE**: Entry synchronized
5. **ENTRY_DIFFERS**: Entry differs from target

### Status Aggregation

When multiple sync items exist, the highest severity status is used:

1. ENTRY_DIFFERS (highest)
2. STATUS_UNKNOWN
3. CREATION_PENDING
4. NO_ENTRY_EXISTS (lowest)

## Sync Implementation

### Implementation Example

```python
class PretixSyncTarget(SyncBaseTarget):
    secret_field_names = ["api_token", "password"]
    
    url = models.URLField()
    api_token = models.CharField(max_length=255)
    organizer_slug = models.CharField(max_length=255)
    event_slug = models.CharField(max_length=255)
    
    def create_new_sync_item(self, event):
        item = PretixSyncItem.objects.create(
            sync_target=self,
            related_event=event
        )
        item.push()
        return item
    
    def get_diff(self, event):
        # Compare event data with target
        pass
```

### Sync Item Implementation

```python
class PretixSyncItem(SyncBaseItem):
    def get_status(self):
        # Check status in Pretix
        try:
            pretix_data = self.target.api.get_event(self.pretix_id)
            if pretix_data == self.event_data:
                return SyncTargetStatus.ENTRY_UP_TO_DATE
            else:
                return SyncTargetStatus.ENTRY_DIFFERS
        except:
            return SyncTargetStatus.STATUS_UNKNOWN
    
    def push(self):
        # Push to Pretix
        self.pretix_id = self.target.api.create_event(self.event_data)
        self.save()
    
    def delete(self):
        # Delete from Pretix
        self.target.api.delete_event(self.pretix_id)
        self.delete()
```

## API Endpoints

### List Sync Targets

`GET /api/udm/sync/targets`

Returns all configured sync targets.

### Create Sync Item

`POST /api/udm/sync/create/{series_id}/{event_id}/{target_id}`

Creates a sync item for an event.

### Sync Status

`GET /api/udm/sync/status/{series_id}/{event_id}`

Returns sync status for an event.

### Push to Target

`POST /api/udm/sync/push/{series_id}/{event_id}/{target_id}`

Pushes an event to a target.

### Delete from Target

`DELETE /api/udm/sync/delete/{series_id}/{event_id}/{target_id}`

Deletes an event from a target.

### Compare Diff

`GET /api/udm/sync/diff/{series_id}/{event_id}/{target_id}`

Compares event data with target.

## Error Handling

### Network Errors

- Retry with exponential backoff
- Log errors
- Notify administrators

### Authentication Errors

- Renew tokens
- Notify administrators
- Mark as failed

### Data Errors

- Log errors
- Skip invalid data
- Continue with other items

## Performance

### Optimization

1. **Batch Sync**: Sync multiple events in batches
2. **Async Processing**: Use Celery for sync operations
3. **Status Caching**: Cache sync status
4. **Delta Sync**: Sync only changed data

### Monitoring

- Sync success rate
- Error rates
- Sync duration
- Data consistency

## Best Practices

### Sync Design

1. **Idempotent**: Sync operations should be idempotent
2. **Retry**: Implement retry logic
3. **Logging**: Log all sync operations
4. **Validation**: Validate data before sync

### Security

1. **Secrets**: Store secrets securely
2. **Authentication**: Use secure authentication
3. **Authorization**: Check permissions
4. **Auditing**: Audit sync operations

## Troubleshooting

### Common Issues

1. **Sync Stuck**
   - Check network connectivity
   - Verify credentials
   - Review logs

2. **Data Mismatch**
   - Compare data sources
   - Check field mappings
   - Review sync logic

3. **Authentication Errors**
   - Renew tokens
   - Check credentials
   - Review authentication flow

## Future Enhancements

### Planned Features

1. **Bi-directional Sync**: Sync from target to system
2. **Conflict Resolution**: Handle sync conflicts
3. **Advanced Filtering**: Filter sync data
4. **Analytics**: Track sync analytics
