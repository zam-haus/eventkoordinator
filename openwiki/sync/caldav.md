---
type: sync_documentation
title: CalDAV Synchronization
description: Documentation for CalDAV synchronization
---

# CalDAV Synchronization

CalDAV synchronization enables event data to be synchronized with CalDAV servers.

**Related Documentation**:
- [Architecture Overview](../architecture/overview.md) - High-level system architecture
- [Sync Overview](overview.md) - Sync infrastructure overview

## Overview

The CalDAV sync target synchronizes calendar events between UDM and CalDAV servers.

## Configuration

### Required Configuration

```json
{
  "url": "https://caldav.example.com",
  "username": "user@example.com",
  "password": "secret_password"
}
```

### Secret Fields

- `password`: CalDAV password

### Public Properties

- `url`: CalDAV URL
- `username`: CalDAV username

## CalDAV Protocol

### Calendar Collections

```
https://caldav.example.com/calendars/username/
```

### Event URLs

```
https://caldav.example.com/calendars/username/events/event-uid/
```

## Sync Process

### 1. Event Creation

```python
target = CalDAVSyncTarget.objects.get(pk=target_id)
item = target.create_new_sync_item(event)
# Creates CalDAV calendar entry
```

### 2. Event Update

```python
item.push()
# Updates CalDAV calendar entry
```

### 3. Event Deletion

```python
item.delete()
# Deletes CalDAV calendar entry
```

### 4. Status Check

```python
status = target.get_status(event)
# Returns sync status
```

## API Integration

### CalDAV API

```python
import requests

class CalDAVAPI:
    def __init__(self, url, username, password):
        self.url = url
        self.auth = (username, password)
    
    def create_calendar(self, calendar_url, ical_data):
        response = requests.request(
            "PUT",
            calendar_url,
            data=ical_data,
            headers={"Content-Type": "text/calendar"},
            auth=self.auth
        )
        return response.status_code
    
    def update_calendar(self, event_url, ical_data):
        response = requests.request(
            "PUT",
            event_url,
            data=ical_data,
            headers={"Content-Type": "text/calendar"},
            auth=self.auth
        )
        return response.status_code
    
    def delete_calendar(self, event_url):
        response = requests.request(
            "DELETE",
            event_url,
            auth=self.auth
        )
        return response.status_code
    
    def get_calendar(self, event_url):
        response = requests.get(
            event_url,
            auth=self.auth
        )
        return response.text
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

1. **NO_ENTRY_EXISTS**: Event not in CalDAV
2. **CREATION_PENDING**: Event creation in progress
3. **STATUS_UNKNOWN**: Unknown status
4. **ENTRY_UP_TO_DATE**: Event synchronized
5. **ENTRY_DIFFERS**: Event needs update

### Status Detection

```python
def get_status(self, event):
    caldav_event = self.get_caldav_event(event.caldav_id)
    if caldav_event is None:
        return SyncTargetStatus.NO_ENTRY_EXISTS
    elif caldav_event == event_data:
        return SyncTargetStatus.ENTRY_UP_TO_DATE
    else:
        return SyncTargetStatus.ENTRY_DIFFERS
```

## Calendar Collections

### Default Calendar

```python
default_calendar = f"{self.url}/calendars/{username}/"
```

### Custom Calendar

```python
custom_calendar = f"{self.url}/calendars/{username}/custom_calendar/"
```

## Event Management

### Create Event

```python
def create_event(self, event, calendar_url):
    event_url = f"{calendar_url}{event.id}/"
    ical_data = self.generate_ical(event)
    
    status = self.api.create_calendar(event_url, ical_data)
    return status == 201
```

### Update Event

```python
def update_event(self, event, event_url):
    ical_data = self.generate_ical(event)
    
    status = self.api.update_calendar(event_url, ical_data)
    return status == 200
```

### Delete Event

```python
def delete_event(self, event_url):
    status = self.api.delete_calendar(event_url)
    return status == 200
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
def get_caldav_event(event_id):
    return api.get_event(event_id)
```

## Testing

### Unit Tests

```python
class CalDAVSyncTests(TestCase):
    def test_create_event(self):
        target = CalDAVSyncTarget.objects.create(...)
        item = target.create_new_sync_item(event)
        self.assertIsNotNone(item.caldav_id)
    
    def test_update_event(self):
        item.push()
        # Verify CalDAV updated
```

### Integration Tests

```python
class CalDAVIntegrationTests(TestCase):
    def test_sync_event(self):
        event = EventFactory()
        target = CalDAVSyncTarget.objects.create(...)
        item = target.create_new_sync_item(event)
        item.push()
        
        # Verify event in CalDAV
        caldav_event = api.get_event(item.caldav_id)
        self.assertEqual(caldav_event["SUMMARY"], event.name)
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

1. **Bi-directional Sync**: Sync from CalDAV to UDM
2. **Recurrence Support**: Sync recurring events
3. **Attendee Sync**: Sync attendee data
4. **Reminder Support**: Sync reminders
