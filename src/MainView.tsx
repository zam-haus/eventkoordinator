import { useState, useEffect, useRef } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { SelectionPanel, type SelectionItem } from './SelectionPanel'
import { ContentRenderer } from './ContentRenderer'
import { Tooltip } from './Tooltip'
import { loadSeriesFromAPI, fetchSeriesById, createSeries, createEvent, deleteSeries, deleteEvent, type Event, type Series, type Event as ApiEvent, type Series as ApiSeries } from './api'
import { EventStatusBadge } from './EventStatusBadge'
import { usePermissions } from './usePermissions'
import { useTranslation } from 'react-i18next'
import styles from './MainView.module.css'

export interface EventItem extends SelectionItem {
  id: string
  name: string
  startTime?: Date
  endTime?: Date
  tag?: string
  status?: string
  useFullDays?: boolean
  proposal_id?: string | null
}

export interface SeriesItem extends SelectionItem {
  id: string
  name: string
  description?: string
  events: EventItem[]
  eventsLoaded?: boolean
}

function toOptionalStatus(status: unknown): string | undefined {
  return typeof status === 'string' ? status : undefined
}

function toEventItem(event: ApiEvent): EventItem {
  return {
    id: event.id,
    name: event.name,
    startTime: event.startTime,
    endTime: event.endTime,
    tag: event.tag,
    status: toOptionalStatus(event.status),
    useFullDays: event.useFullDays,
    proposal_id: event.proposal_id,
  }
}

function toSeriesItem(series: ApiSeries): SeriesItem {
  return {
    id: series.id,
    name: series.name,
    description: series.description,
    eventsLoaded: true,
    events: series.events.map((event) => toEventItem(event)),
  }
}

export function MainView() {
  const { t } = useTranslation()
  const [series, setSeries] = useState<SeriesItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { canBrowse, canAdd, canChange, loading: permissionsLoading } = usePermissions()

  const navigate = useNavigate()
  const { seriesId: urlSeriesId, eventId: urlEventId } = useParams<{ seriesId?: string; eventId?: string }>()

  useEffect(() => {
    const fetchSeries = async () => {
      try {
        setLoading(true)
        const apiSeries = await loadSeriesFromAPI()
        const convertedSeries: SeriesItem[] = apiSeries.map((s) => ({
          id: s.id,
          name: s.name,
          description: s.description,
          events: [],
          eventsLoaded: false,
        }))
        setSeries(convertedSeries)
        setError(null)
      } catch (err) {
        console.error('Failed to load series from API:', err)
        setError(t('mainView.errorLoadingSeries'))
        setSeries([])
      } finally {
        setLoading(false)
      }
    }
    fetchSeries()
  }, [])

  useEffect(() => {
    const loadSelectedSeriesEvents = async () => {
      const targetSeriesId = (urlSeriesId && series.some((s) => s.id === urlSeriesId))
        ? urlSeriesId
        : series[0]?.id

      if (!targetSeriesId) {
        return
      }

      const targetSeries = series.find((s) => s.id === targetSeriesId)
      if (!targetSeries || targetSeries.eventsLoaded) {
        return
      }

      try {
        const detailedSeries = await fetchSeriesById(targetSeriesId)
        setSeries((prev) => prev.map((s) => {
          if (s.id !== targetSeriesId) return s
          return {
            ...s,
            events: detailedSeries.events.map((event) => toEventItem(event)),
            eventsLoaded: true,
          }
        }))
      } catch (err) {
        console.error('Failed to load series events:', err)
      }
    }

    void loadSelectedSeriesEvents()
  }, [series, urlSeriesId])

  if (loading || permissionsLoading) {
    return (
      <div className={styles.loadingContainer}>
        <div className={styles.loadingContent}>
          <div className={styles.loadingText}>{t('mainView.loadingSeries')}</div>
          <div className={styles.loadingSubtext}>{t('mainView.fetchingCalendarData')}</div>
        </div>
      </div>
    )
  }

  // Check browse permission for series
  if (!canBrowse('series')) {
    return (
      <div className={styles.errorContainer}>
        <div className={styles.errorContent}>
          <div className={styles.errorTitle}>{t('mainView.accessDenied')}</div>
          <div className={styles.errorMessage}>{t('mainView.noPermissionBrowse')}</div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={styles.errorContainer}>
        <div className={styles.errorContent}>
          <div className={styles.errorTitle}>{t('mainView.errorLoadingSeries')}</div>
          <div className={styles.errorMessage}>{error}</div>
        </div>
      </div>
    )
  }

  // Determine effective selection from URL, falling back to first series / general
  const selectedSeriesId = (urlSeriesId && series.some((s) => s.id === urlSeriesId))
    ? urlSeriesId
    : series[0]?.id ?? ''

  const selectedEventId = urlEventId ?? 'general'

  // If the URL seriesId is absent or invalid and we have series, redirect to the first one
  if (series.length > 0 && selectedSeriesId !== urlSeriesId) {
    return <Navigate to={`/coordinator/${selectedSeriesId}`} replace />
  }

  const handleSeriesChange = (newSeriesId: string) => {
    navigate(`/coordinator/${newSeriesId}`)
  }

  const handleEventChange = (newEventId: string) => {
    if (newEventId === 'general' || !newEventId) {
      navigate(`/coordinator/${selectedSeriesId}`)
    } else {
      navigate(`/coordinator/${selectedSeriesId}/${newEventId}`)
    }
  }

  const handleCreateSeries = async () => {
    try {
      const newSeries = await createSeries()
      setSeries((prev) => [...prev, toSeriesItem(newSeries)])
      navigate(`/coordinator/${newSeries.id}`)
    } catch (err) {
      console.error('Failed to create series:', err)
    }
  }

  const handleCreateEvent = async () => {
    if (!selectedSeriesId) return
    try {
      const newEvent = await createEvent({ seriesId: selectedSeriesId })
      setSeries((prev) => prev.map((s) => {
        if (s.id !== selectedSeriesId) return s
        const updatedEvents = [...s.events, toEventItem(newEvent)]
          .sort((a, b) => (a.startTime?.getTime() ?? 0) - (b.startTime?.getTime() ?? 0))
        return { ...s, events: updatedEvents }
      }))
      navigate(`/coordinator/${selectedSeriesId}/${newEvent.id}`)
    } catch (err) {
      console.error('Failed to create event:', err)
    }
  }

  const handleSeriesUpdate = (updated: ApiSeries) => {
    setSeries((prev) => prev.map((s) => {
      if (s.id !== updated.id) return s
      return { id: updated.id, name: updated.name, description: updated.description, events: s.events, eventsLoaded: s.eventsLoaded }
    }))
  }

  const handleEventUpdate = (seriesId: string, updated: ApiEvent) => {
    setSeries((prev) => prev.map((s) => {
      if (s.id !== seriesId) return s
      return {
        ...s,
        events: s.events.map((e) => {
          if (e.id !== updated.id) return e
          return toEventItem(updated)
        }),
      }
    }))
  }

  const handleSeriesDelete = async (seriesId: string) => {
    await deleteSeries(seriesId)

    const remainingSeries = series.filter((item) => item.id !== seriesId)
    setSeries(remainingSeries)

    if (remainingSeries.length === 0) {
      navigate('/coordinator')
      return
    }

    navigate(`/coordinator/${remainingSeries[0].id}`)
  }

  const handleEventDelete = async (seriesId: string, eventId: string) => {
    await deleteEvent(seriesId, eventId)

    setSeries((prev) => prev.map((item) => {
      if (item.id !== seriesId) {
        return item
      }

      return {
        ...item,
        events: item.events.filter((event) => event.id !== eventId),
      }
    }))

    navigate(`/coordinator/${seriesId}`)
  }

  return (
    <CoordinatorInnerView
      series={series}
      selectedSeriesId={selectedSeriesId}
      selectedEventId={selectedEventId}
      onSeriesChange={handleSeriesChange}
      onEventChange={handleEventChange}
      onCreateSeries={handleCreateSeries}
      onCreateEvent={handleCreateEvent}
      onSeriesUpdate={handleSeriesUpdate}
      onEventUpdate={handleEventUpdate}
      onSeriesDelete={handleSeriesDelete}
      onEventDelete={handleEventDelete}
      canAddSeries={canAdd('series')}
      canAddEvent={canAdd('event')}
      canChangeSeries={canChange('series')}
      canChangeEvent={canChange('event')}
    />
  )
}

interface CoordinatorInnerViewProps {
  series: SeriesItem[]
  selectedSeriesId: string
  selectedEventId: string
  onSeriesChange: (seriesId: string) => void
  onEventChange: (eventId: string) => void
  onCreateSeries: () => Promise<void>
  onCreateEvent: () => Promise<void>
  onSeriesUpdate: (updated: Series) => void
  onEventUpdate: (seriesId: string, updated: Event) => void
  onSeriesDelete: (seriesId: string) => Promise<void>
  onEventDelete: (seriesId: string, eventId: string) => Promise<void>
  canAddSeries: boolean
  canAddEvent: boolean
  canChangeSeries: boolean
  canChangeEvent: boolean
}

function CoordinatorInnerView({
  series,
  selectedSeriesId,
  selectedEventId,
  onSeriesChange,
  onEventChange,
  onCreateSeries,
  onCreateEvent,
  onSeriesUpdate,
  onEventUpdate,
  onSeriesDelete,
  onEventDelete,
  canAddSeries,
  canAddEvent,
  canChangeSeries,
  canChangeEvent,
}: CoordinatorInnerViewProps) {
  const selectedSeries = series.find((s) => s.id === selectedSeriesId)
  const confirmNavigationRef = useRef<(() => Promise<boolean>) | null>(null)
  const { t } = useTranslation()

  const handleBeforeSeriesChange = async (newSeriesId: string): Promise<boolean> => {
    void newSeriesId
    if (confirmNavigationRef.current) {
      return await confirmNavigationRef.current()
    }
    return true
  }

  const handleBeforeEventChange = async (newEventId: string): Promise<boolean> => {
    void newEventId
    if (confirmNavigationRef.current) {
      return await confirmNavigationRef.current()
    }
    return true
  }

  const handleRequestNavigation = (confirmFn: () => Promise<boolean>) => {
    confirmNavigationRef.current = confirmFn
  }

  const eventsSidebarAction = canAddEvent ? (
    <button
      type="button"
      onClick={() => void onCreateEvent()}
      className={styles.sidebarActionButton}
    >
      {t('mainView.createNewEvent')}
    </button>
  ) : undefined

  const seriesSidebarAction = canAddSeries ? (
    <button
      type="button"
      onClick={() => void onCreateSeries()}
      className={styles.sidebarActionButton}
    >
      {t('mainView.createNewSeries')}
    </button>
  ) : undefined

  // Use a placeholder series item when there are no series
  const displaySeries = series.length === 0
    ? [{ id: 'empty', name: t('mainView.noSeriesYet'), description: t('mainView.noSeriesDescription'), events: [] as EventItem[], eventsLoaded: true }]
    : series

  const effectiveSelectedSeriesId = series.length === 0 ? 'empty' : selectedSeriesId

  const eventItems: Array<EventItem | { id: string; name: string }> = selectedSeries && selectedSeries.id !== 'empty'
    ? [{ id: 'general', name: t('mainView.generalInformation') }, ...selectedSeries.events]
    : []

  const renderSeriesLabel = (s: SeriesItem) => {
    const labelContent = (
      <div>
        <div style={{ fontWeight: 600, fontSize: '0.95rem' }}>{s.name}</div>
        {s.description && (
          <div style={{ fontSize: '0.8rem', opacity: 0.7 }}>
            {s.description.length > 50
              ? `${s.description.substring(0, 50)}...`
              : s.description}
          </div>
        )}
      </div>
    )

    // Only wrap in tooltip if there's a description
    if (s.description) {
      return (
        <Tooltip content={s.description}>
          {labelContent}
        </Tooltip>
      )
    }

    return labelContent
  }

  const renderEventLabel = (event: EventItem) => {
    const formatDate = (date?: Date): string => {
      if (!date) return ''
      return date.toLocaleDateString('de-DE', {
        day: 'numeric',
        month: 'short',
        year: '2-digit',
      })
    }

    const formatTime = (date?: Date): string => {
      if (!date) return ''
      return date.toLocaleTimeString('de-DE', {
        hour: '2-digit',
        minute: '2-digit',
      })
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 500 }}>{event.name}</div>
          <EventStatusBadge
            status={event.status}
            ariaLabel={`Status of ${event.name}: ${event.status}`}
          />
        </div>
        <div style={{ fontSize: '0.8rem', opacity: 0.7, display: 'flex', gap: '8px', alignItems: 'center' }}>
          {event.startTime && (
            <>
              <span>{formatDate(event.startTime)}</span>
              <span>{formatTime(event.startTime)}</span>
            </>
          )}
          {event.tag && (
            <span className={styles.tagBadge}>
              {event.tag}
            </span>
          )}
        </div>
      </div>
    )
  }

  const renderMainContent = (s: SeriesItem) => {
    if (s.id === 'empty') {
      return (
        <div className={styles.placeholderText}>
          <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('mainView.noSeriesYet')}</p>
          <p>{t('mainView.noSeriesButton')}</p>
        </div>
      )
    }

    if (!s.eventsLoaded) {
      return (
        <div className={styles.placeholderText}>
          <p>{t('mainView.loadingEvents')}</p>
        </div>
      )
    }

    const effectiveSelectedEventId = eventItems.some((event) => event.id === selectedEventId)
      ? selectedEventId
      : 'general'

    return (
      <SelectionPanel<EventItem>
        items={eventItems}
        selectedItemId={effectiveSelectedEventId}
        onSelectionChange={onEventChange}
        onBeforeSelectionChange={handleBeforeEventChange}
        renderItemLabel={renderEventLabel}
        renderContent={(event) => {
          const apiSeries: Series = {
            id: s.id,
            name: s.name,
            description: s.description,
            events: s.events as Event[],
          }
          return (
            <ContentRenderer
              series={apiSeries}
              selectedEventId={event.id}
              onSeriesUpdate={onSeriesUpdate}
              onEventUpdate={(updated) => onEventUpdate(s.id, updated)}
              onSeriesDelete={onSeriesDelete}
              onEventDelete={onEventDelete}
              onRequestNavigation={handleRequestNavigation}
              canEditSeries={canChangeSeries}
              canEditEvent={canChangeEvent}
            />
          )
        }}
        sidebarTitle={t('mainView.events')}
        belowTitleElement={eventsSidebarAction}
        isNested={true}
      />
    )
  }

  return (
    <SelectionPanel<SeriesItem>
      items={displaySeries}
      selectedItemId={effectiveSelectedSeriesId}
      onSelectionChange={async (id) => {
        if (id !== 'empty') {
          onSeriesChange(id)
        }
      }}
      onBeforeSelectionChange={handleBeforeSeriesChange}
      renderItemLabel={renderSeriesLabel}
      renderContent={renderMainContent}
      sidebarTitle={t('mainView.series')}
      belowTitleElement={seriesSidebarAction}
    />
  )
}

