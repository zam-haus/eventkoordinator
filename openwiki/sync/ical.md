---
type: sync_documentation
title: iCal Synchronization
description: Documentation for iCal synchronization
---

# iCal Synchronization

iCal synchronization enables event data to be synchronized with iCal calendar files.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Sync Overview](overview.md) - Sync infrastructure overview

## Overview

The iCal sync target synchronizes event calendar entries between UDM and iCal format.

## Configuration

### Required Configuration

```json
{
  "url": "https://calendar.example.com",
  "calendar_id": "calendar_id",
  "username": "user@example.com",
  "password": "secret_password"
}
```

### Secret Fields

- `password`: Calendar password

### Public Properties

- `url`: Calendar URL
- `calendar_id`: Calendar identifier
- `username`: Calendar username

## iCal Format

### Event Format

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Example//EN
BEGIN:VEVENT
UID:event-uid
DTSTART:20240101T100000Z
DTEND:20240101T180000Z
SUMMARY:Event Name
DESCRIPTION:Event Description
LOCATION:Event Location
END:VEVENT
END:VCALENDAR
```

### Field Mapping

| UDM Field | iCal Field | Type |
|-----------|------------|------|
| name | SUMMARY | string |
| description | DESCRIPTION | text |
| date_start | DTSTART | datetime |
| date_end | DTEND | datetime |
| location | LOCATION | string |

## Sync Process

### 1. Event Creation

```python
target = iCalSyncTarget.objects.get(pk=target_id)
item = target.create_new_sync_item(event)
# Creates iCal entry
```

### 2. Event Update

```python
item.push()
# Updates iCal entry
```

### 3. Event Deletion

```python
item.delete()
# Deletes iCal entry
```

### 4. Status Check

```python
status = target.get_status(event)
# Returns sync status
```

## API Integration

### iCal API

```python
import requests

class iCalAPI:
    def __init__(self, url, username, password):
        self.url = url
        self.auth = (username, password)
    
    def create_event(self, ical_data):
        response = requests.post(
            f"{self.url}/calendar",
            data=ical_data,
            headers={"Content-Type": "text/calendar"},
            auth=self.auth
        )
        return response.json()
    
    def update_event(self, event_id, ical_data):
        response = requests.put(
            f"{self.url}/calendar/{event_id}",
            data=ical_data,
            headers={"Content-Type": "text/calendar"},
            auth=self.auth
        )
        return response.json()
    
    def delete_event(self, event_id):
        response = requests.delete(
            f"{self.url}/calendar/{event_id}",
            auth=self.auth
        )
        return response.status_code
```

### Data Format

```ical
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example//Example//EN
BEGIN:VEVENT
UID:event-uuid
DTSTART:20240101T100000Z
DTEND:20240101T180000Z
SUMMARY:Event Name
DESCRIPTION:Event Description
LOCATION:Event Location
END:VEVENT
END:VCALENDAR
```

## Status Mapping

### Status Values

1. **NO_ENTRY_EXISTS**: Event not in iCal
2. **CREATION_PENDING**: Event creation in progress
3. **STATUS_UNKNOWN**: Unknown status
4. **ENTRY_UP_TO_DATE**: Event synchronized
5. **ENTRY_DIFFERS**: Event needs update

### Status Detection

```python
def get_status(self, event):
    ical_event = self.get_ical_event(event.ical_uid)
    if ical_event is None:
        return SyncTargetStatus.NO_ENTRY_EXISTS
    elif ical_event == event_data:
        return SyncTargetStatus.ENTRY_UP_TO_DATE
    else:
        return SyncTargetStatus.ENTRY_DIFFERS
```

## Timezone Handling

### Timezone Support

```python
from datetime import datetime
import pytz

def format_datetime(dt):
    # Convert to UTC
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    else:
        dt = dt.astimezone(pytz.UTC)
    
    return dt.strftime("%Y%m%dT%H%M%SZ")
```

### Recurring Events

```ical
BEGIN:VEVENT
UID:recurring-event-uid
DTSTART:20240101T100000Z
DTEND:20240101T180000Z
SUMMARY:Recurring Event
RRULE:FREQ=WEEKLY;COUNT=10
END:VEVENT
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
def get_ical_event(event_id):
    return api.get_event(event_id)
```

## Testing

### Unit Tests

```python
class iCalSyncTests(TestCase):
    def test_create_event(self):
        target = iCalSyncTarget.objects.create(...)
        item = target.create_new_sync_item(event)
        self.assertIsNotNone(item.ical_uid)
    
    def test_update_event(self):
        item.push()
        # Verify iCal updated
```

### Integration Tests

```python
class iCalIntegrationTests(TestCase):
    def test_sync_event(self):
        event = EventFactory()
        target = iCalSyncTarget.objects.create(...)
        item = target.create_new_sync_item(event)
        item.push()
        
        # Verify event in iCal
        ical_event = api.get_event(item.ical_uid)
        self.assertEqual(ical_event["SUMMARY"], event.name)
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

1. **Bi-directional Sync**: Sync from iCal to UDM
2. **Recurrence Support**: Sync recurring events
3. **Attendee Sync**: Sync attendee data
4. **Reminder Support**: Sync reminders
