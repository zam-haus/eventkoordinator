import { DayPilot } from '@daypilot/daypilot-lite-javascript'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { udmGetCalendar, type CalendarEntryOut } from '../apiUdm'
import { browserTimeZone, parseAppDatetime } from '../timezone'
import { getEventStatusColor } from '../eventStatus'

const BOUND_EVENT_ID = '__bound__'
const NAIVE_PATTERN = 'yyyy-MM-ddTHH:mm:ss'

function pad(n: number): string { return String(n).padStart(2, '0') }

/** `datetime` field values are timezone-aware ISO strings (see
 *  DateTimeEditors.tsx / timezone.ts). DayPilot.Date, when built from an ISO
 *  string via its default constructor, applies its OWN implicit UTC
 *  interpretation regardless of any offset in the string, which silently
 *  shifts the displayed time by the browser's UTC offset. Route every
 *  string→DayPilot.Date conversion through here instead: parseAppDatetime
 *  resolves the value to the correct instant (offset-aware natively, bare
 *  legacy-naive via the shared timezone fallback), then DayPilot.Date.parse
 *  with an explicit pattern reads its LOCAL Y/M/D/H/M/S with no further
 *  timezone math at all — matching what the picker (and the rest of the app)
 *  displays for the same instant. */
function toDayPilotDate(iso: string): DayPilot.Date {
  const d = parseAppDatetime(iso, browserTimeZone())
  const naive = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  return DayPilot.Date.parse(naive, NAIVE_PATTERN)
}

/** Inverse of toDayPilotDate — DayPilot.Date arithmetic from drag/resize is
 *  pure tick math over the local wall-clock components read in
 *  toDayPilotDate, so re-parsing those components as local time and
 *  converting to an offset-aware UTC ISO string round-trips exactly. */
function fromDayPilotDate(d: DayPilot.Date): string {
  const naive = d.toString(NAIVE_PATTERN)
  return new globalThis.Date(naive).toISOString()
}

export interface CalendarPreviewProps {
  sources: string[]
  /** Bump to force a refetch. */
  refreshToken?: number | string
  /** When set, dragging an empty slot (or moving/resizing the bound event)
   *  calls this with ISO start/end — used to let a `calendar` field's own
   *  bound start/end values be set interactively (mirrors date_range's
   *  onDirty). Absent → the widget is purely read-only aggregation. */
  onSelectRange?: (start: string, end: string) => void
  /** The bound field's current value (dirty or saved) — rendered as its own
   *  draggable/resizable event, distinct from the read-only aggregated
   *  entries, both while dirty and after it's been committed. */
  boundRange?: { start: string | null; end: string | null }
}

type ViewType = 'Week' | 'Month'

/** events-and-sync.md §6/Step 9: calendar view of aggregated UDM + sync_core
 *  CalendarSource entries. Defaults to a week view (switchable to month).
 *  Clicking an aggregated entry navigates to the underlying UDM entity
 *  (no-op for imported iCal/CalDAV entries, which have no entity_id). */
export function CalendarPreview({ sources, refreshToken, onSelectRange, boundRange }: CalendarPreviewProps) {
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const calendarRef = useRef<InstanceType<typeof DayPilot.Calendar> | null>(null)
  const monthRef = useRef<InstanceType<typeof DayPilot.Month> | null>(null)
  const [view, setView] = useState<ViewType>('Week')
  const [anchor, setAnchor] = useState(() => DayPilot.Date.today())
  const [entries, setEntries] = useState<CalendarEntryOut[] | null>(null)
  const entriesRef = useRef<CalendarEntryOut[]>([])
  const onSelectRangeRef = useRef(onSelectRange)
  onSelectRangeRef.current = onSelectRange

  const rangeStart = useMemo(
    () => (view === 'Week' ? anchor.firstDayOfWeek() : anchor.firstDayOfMonth()),
    [anchor, view],
  )
  const rangeEnd = useMemo(
    () => (view === 'Week' ? rangeStart.addDays(7) : rangeStart.addMonths(1)),
    [rangeStart, view],
  )

  useEffect(() => {
    let cancelled = false
    setEntries(null)
    udmGetCalendar(rangeStart.toString(), rangeEnd.toString(), sources).then(rows => {
      if (!cancelled) setEntries(rows)
    }).catch(() => {
      if (!cancelled) setEntries([])
    })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeStart.toString(), rangeEnd.toString(), sources.join(','), refreshToken])

  const onEventClick = (args: { e: DayPilot.Event }) => {
    const id = String(args.e.id())
    if (id === BOUND_EVENT_ID) return
    const [source, uid] = id.split(':', 2)
    const row = entriesRef.current.find(e => e.source === source && e.uid === uid)
    if (row?.entity_id) navigate(`/udm-entity/${row.entity_id}`)
  }

  const onTimeRangeSelected = (args: { start: DayPilot.Date; end: DayPilot.Date; control: { clearSelection(): void } }) => {
    onSelectRangeRef.current?.(fromDayPilotDate(args.start), fromDayPilotDate(args.end))
    args.control.clearSelection()
  }

  const onBoundEventChanged = (args: { e: { start(): DayPilot.Date; end(): DayPilot.Date } }) => {
    onSelectRangeRef.current?.(fromDayPilotDate(args.e.start()), fromDayPilotDate(args.e.end()))
  }

  // Mount/unmount the active widget on view switch. Both refs are kept null
  // except for the currently-mounted widget's own ref — otherwise the other
  // ref keeps pointing at a disposed instance and `a ?? b` below picks it.
  useEffect(() => {
    if (!containerRef.current) return
    const selectable = !!onSelectRangeRef.current
    const dragHandling = selectable ? 'Update' : 'Disabled'
    if (view === 'Week') {
      const calendar = new DayPilot.Calendar(containerRef.current, {
        viewType: 'Week',
        startDate: rangeStart,
        // Weekday name alongside the date, e.g. "Mon 6/15".
        headerDateFormat: 'ddd M/d',
        eventClickHandling: 'Disabled',
        timeRangeSelectedHandling: selectable ? 'Enabled' : 'Disabled',
        eventMoveHandling: dragHandling,
        eventResizeHandling: dragHandling,
        onEventClick,
        onTimeRangeSelected,
        onEventMoved: onBoundEventChanged,
        onEventResized: onBoundEventChanged,
      })
      calendar.init()
      calendarRef.current = calendar
      return () => { calendar.dispose(); calendarRef.current = null }
    }
    const month = new DayPilot.Month(containerRef.current, {
      startDate: rangeStart,
      eventClickHandling: 'Disabled',
      timeRangeSelectedHandling: selectable ? 'Enabled' : 'Disabled',
      eventMoveHandling: dragHandling,
      eventResizeHandling: dragHandling,
      onEventClick,
      onTimeRangeSelected,
      onEventMoved: onBoundEventChanged,
      onEventResized: onBoundEventChanged,
    })
    month.init()
    monthRef.current = month
    return () => { month.dispose(); monthRef.current = null }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view])

  useEffect(() => {
    const widget = calendarRef.current ?? monthRef.current
    widget?.update({ startDate: rangeStart })
  }, [rangeStart])

  useEffect(() => {
    entriesRef.current = entries ?? []
    const widget = calendarRef.current ?? monthRef.current
    if (!widget || entries === null) return
    // Aggregated entries are read-only (moveDisabled/resizeDisabled are
    // untyped but real DayPilot per-event flags); the bound event — if any —
    // is the only one draggable/resizable, and is visually distinct.
    const list: (DayPilot.EventData & Record<string, unknown>)[] = entries
      .filter(e => e.start)
      .map(e => ({
        id: `${e.source}:${e.uid}`,
        text: e.title,
        start: toDayPilotDate(e.start as string),
        end: toDayPilotDate(e.end ?? (e.start as string)),
        moveDisabled: true,
        resizeDisabled: true,
        // events-and-sync.md Step 11: color by workflow state — reuses the
        // same palette as EventStatusBadge for visual consistency between
        // the calendar and elsewhere workflow state is shown.
        backColor: getEventStatusColor(e.workflow_state ?? ''),
      }))
    if (boundRange?.start && boundRange?.end) {
      list.push({
        id: BOUND_EVENT_ID,
        text: 'This event',
        start: toDayPilotDate(boundRange.start),
        end: toDayPilotDate(boundRange.end),
        backColor: '#2563eb',
        moveDisabled: !onSelectRangeRef.current,
        resizeDisabled: !onSelectRangeRef.current,
      })
    }
    widget.events.list = list
    widget.update()
  }, [entries, boundRange?.start, boundRange?.end])

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.5rem' }}>
        <button type="button" onClick={() => setAnchor(d => (view === 'Week' ? d.addDays(-7) : d.addMonths(-1)))}>←</button>
        <span style={{ fontWeight: 600 }}>
          {view === 'Week' ? `Week of ${rangeStart.toString('d MMM yyyy')}` : rangeStart.toString('MMMM yyyy')}
        </span>
        <button type="button" onClick={() => setAnchor(d => (view === 'Week' ? d.addDays(7) : d.addMonths(1)))}>→</button>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
          <button type="button" disabled={view === 'Week'} onClick={() => setView('Week')}>Week</button>
          <button type="button" disabled={view === 'Month'} onClick={() => setView('Month')}>Month</button>
        </span>
      </div>
      {entries === null && <p style={{ color: '#9ca3af', fontSize: '0.85rem', margin: 0 }}>Loading…</p>}
      {/* key={view} forces a fresh DOM node per widget type, so switching
          views can never leave the previous widget's elements behind
          regardless of how thoroughly DayPilot's own dispose() cleans up. */}
      <div key={view} ref={containerRef} />
    </div>
  )
}
