---
type: sync_documentation
title: Pretix Synchronization
description: Documentation for Pretix synchronization
---

# Pretix Synchronization

Pretix is a ticketing system that can be synchronized with the UDM application.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Sync Overview](overview.md) - Sync infrastructure overview

## Overview

The Pretix sync target synchronizes event data between UDM and Pretix.

## Configuration

### Required Configuration

```json
{
  "url": "https://pretix.example.com",
  "api_token": "secret_api_token",
  "organizer_slug": "organizer_name",
  "event_slug": "event_slug"
}
```

### Secret Fields

- `api_token`: API token for authentication

### Public Properties

- `url`: Pretix URL
- `organizer_slug`: Organizer identifier
- `event_slug`: Event identifier

## Field Mapping

### Event Fields

| UDM Field | Pretix Field | Type |
|-----------|--------------|------|
| name | item.name | string |
| description | item.description | text |
| date_start | item.date_start | datetime |
| date_end | item.date_end | datetime |
| location | item.location | string |
| price | item.price | decimal |
| capacity | item.capacity | integer |

### Attendee Fields

| UDM Field | Pretix Field | Type |
|-----------|--------------|------|
| first_name | attendee.first_name | string |
| last_name | attendee.last_name | string |
| email | attendee.email | string |
| ticket_type | attendee.ticket_type | string |

## Sync Process

### 1. Event Creation

```python
target = PretixSyncTarget.objects.get(pk=target_id)
item = target.create_new_sync_item(event)
# Creates event in Pretix
```

### 2. Event Update

```python
item.push()
# Updates event in Pretix
```

### 3. Event Deletion

```python
item.delete()
# Deletes event from Pretix
```

### 4. Status Check

```python
status = target.get_status(event)
# Returns sync status
```

## API Integration

### Authentication

```python
import requests

class PretixAPI:
    def __init__(self, url, api_token):
        self.url = url
        self.headers = {
            "Authorization": f"Token {api_token}",
            "Content-Type": "application/json"
        }
    
    def create_event(self, data):
        response = requests.post(
            f"{self.url}/api/v1/organizers/{organizer}/events/",
            json=data,
            headers=self.headers
        )
        return response.json()
    
    def update_event(self, event_id, data):
        response = requests.patch(
            f"{self.url}/api/v1/organizers/{organizer}/events/{event_id}/",
            json=data,
            headers=self.headers
        )
        return response.json()
    
    def delete_event(self, event_id):
        response = requests.delete(
            f"{self.url}/api/v1/organizers/{organizer}/events/{event_id}/",
            headers=self.headers
        )
        return response.status_code
```

### Data Format

```json
{
  "name": "Event Name",
  "slug": "event-slug",
  "date_from": "2024-01-01T10:00:00Z",
  "date_to": "2024-01-02T18:00:00Z",
  "location": "Event Location",
  "currency": "EUR"
}
```

## Status Mapping

### Status Values

1. **NO_ENTRY_EXISTS**: Event not in Pretix
2. **CREATION_PENDING**: Event creation in progress
3. **STATUS_UNKNOWN**: Unknown status
4. **ENTRY_UP_TO_DATE**: Event synchronized
5. **ENTRY_DIFFERS**: Event needs update

### Status Detection

```python
def get_status(self, event):
    pretix_event = self.get_pretix_event(event.pretix_id)
    if pretix_event is None:
        return SyncTargetStatus.NO_ENTRY_EXISTS
    elif pretix_event == event_data:
        return SyncTargetStatus.ENTRY_UP_TO_DATE
    else:
        return SyncTargetStatus.ENTRY_DIFFERS
```

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

### Caching

```python
@cached(ttl=300)
def get_pretix_event(event_id):
    return api.get_event(event_id)
```

## Testing

### Unit Tests

```python
class PretixSyncTests(TestCase):
    def test_create_event(self):
        target = PretixSyncTarget.objects.create(...)
        item = target.create_new_sync_item(event)
        self.assertIsNotNone(item.pretix_id)
    
    def test_update_event(self):
        item.push()
        # Verify Pretix updated
```

### Integration Tests

```python
class PretixIntegrationTests(TestCase):
    def test_sync_event(self):
        event = EventFactory()
        target = PretixSyncTarget.objects.create(...)
        item = target.create_new_sync_item(event)
        item.push()
        
        # Verify event in Pretix
        pretix_event = api.get_event(item.pretix_id)
        self.assertEqual(pretix_event["name"], event.name)
```

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

1. **Bi-directional Sync**: Sync from Pretix to UDM
2. **Ticket Sync**: Sync ticket sales
3. **Attendee Sync**: Sync attendee data
4. **Payment Sync**: Sync payment information
