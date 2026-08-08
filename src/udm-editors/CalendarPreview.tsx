import { DayPilot } from '@daypilot/daypilot-lite-javascript'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { udmGetCalendar, type CalendarEntryOut } from '../apiUdm'

export interface CalendarPreviewProps {
  sources: string[]
  /** Bump to force a refetch. */
  refreshToken?: number | string
}

type ViewType = 'Week' | 'Month'

/** events-and-sync.md §6/Step 9: calendar view of aggregated UDM + sync_core
 *  CalendarSource entries. Defaults to a week view (switchable to month).
 *  Read-only — clicking an entry navigates to the underlying UDM entity
 *  (no-op for imported iCal/CalDAV entries, which have no entity_id). */
export function CalendarPreview({ sources, refreshToken }: CalendarPreviewProps) {
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const calendarRef = useRef<InstanceType<typeof DayPilot.Calendar> | null>(null)
  const monthRef = useRef<InstanceType<typeof DayPilot.Month> | null>(null)
  const [view, setView] = useState<ViewType>('Week')
  const [anchor, setAnchor] = useState(() => DayPilot.Date.today())
  const [entries, setEntries] = useState<CalendarEntryOut[] | null>(null)
  const entriesRef = useRef<CalendarEntryOut[]>([])

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
    const [source, uid] = String(args.e.id()).split(':', 2)
    const row = entriesRef.current.find(e => e.source === source && e.uid === uid)
    if (row?.entity_id) navigate(`/udm-entity/${row.entity_id}`)
  }

  // Mount/unmount the active widget on view switch.
  useEffect(() => {
    if (!containerRef.current) return
    if (view === 'Week') {
      const calendar = new DayPilot.Calendar(containerRef.current, {
        viewType: 'Week',
        startDate: rangeStart,
        eventClickHandling: 'Disabled',
        onEventClick,
      })
      calendar.init()
      calendarRef.current = calendar
      return () => calendar.dispose()
    }
    const month = new DayPilot.Month(containerRef.current, {
      startDate: rangeStart,
      eventClickHandling: 'Disabled',
      onEventClick,
    })
    month.init()
    monthRef.current = month
    return () => month.dispose()
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
    widget.events.list = entries
      .filter(e => e.start)
      .map(e => ({
        id: `${e.source}:${e.uid}`,
        text: e.title,
        start: e.start as string,
        end: e.end ?? (e.start as string),
      }))
    widget.update()
  }, [entries])

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
      <div ref={containerRef} />
    </div>
  )
}
