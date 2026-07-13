import {useState, useEffect, useMemo} from 'react'
import {useNavigate} from 'react-router-dom'
import {useTranslation} from 'react-i18next'
import type {Event, Series, ExternalCalendarEvent} from './api'
import {
    fetchSyncStatus,
    fetchSyncTargets,
    createSyncItem,
    fetchExternalCalendarEvents,
    pushToPlatform,
    deleteRemoteSyncItem,
    updateEvent,
    fetchCalculatedPrices,
    createCalculatedPrices,
    updateCalculatedPrices,
    deleteCalculatedPrices,
    getUserPermissions,
    checkObjectPermission,
    type CalculatedPrices,
    type EventSyncInfo,
    type SyncTarget,
} from './api'
import type {CalendarEvent, Resource} from './calendarTypes'
import WeekViewCombined from './WeekViewCombined'
import {useUnsavedChanges} from './useUnsavedChanges'
import {EventTransitionButtons} from './EventTransitionButtons'
import { translateApiError } from './apiError'
import styles from './SubEventEditor.module.css'

interface EventEditorProps {
    series: Series
    event: Event
    onEventUpdate: (updated: Event) => void
    onDeleteEvent?: () => Promise<void>
    onRequestNavigation?: (confirmFn: () => Promise<boolean>) => void
    disabled?: boolean
    canDelete?: boolean
}

interface CalculatedPricesFormValues {
    member_regular_gross_eur: string
    member_discounted_gross_eur: string
    guest_regular_gross_eur: string
    guest_discounted_gross_eur: string
    business_net_eur: string
    internal_training_eur: string
}

const EMPTY_CALCULATED_PRICES_FORM: CalculatedPricesFormValues = {
    member_regular_gross_eur: '',
    member_discounted_gross_eur: '',
    guest_regular_gross_eur: '',
    guest_discounted_gross_eur: '',
    business_net_eur: '',
    internal_training_eur: '',
}

function parseLocalDateTimeInput(value: string): Date | null {
    if (!value) return null
    const parsed = new Date(value)
    return Number.isNaN(parsed.getTime()) ? null : parsed
}

function toLocalDateTimeInputValue(date: Date | string): string {
    const d = typeof date === 'string' ? new Date(date) : date
    if (!d || Number.isNaN(d.getTime())) return ''
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    return `${y}-${m}-${day}T${hh}:${mm}`
}

function toCalculatedPricesForm(values: CalculatedPrices): CalculatedPricesFormValues {
    return {
        member_regular_gross_eur: values.member_regular_gross_eur ?? '',
        member_discounted_gross_eur: values.member_discounted_gross_eur ?? '',
        guest_regular_gross_eur: values.guest_regular_gross_eur ?? '',
        guest_discounted_gross_eur: values.guest_discounted_gross_eur ?? '',
        business_net_eur: values.business_net_eur ?? '',
        internal_training_eur: values.internal_training_eur ?? '',
    }
}

function toNullableDecimal(value: string): string | null {
    const trimmed = value.trim()
    return trimmed.length > 0 ? trimmed : null
}

export function EventEditor({
                                series,
                                event,
                                onEventUpdate,
                                onDeleteEvent,
                                onRequestNavigation,
                                disabled = false,
                                canDelete = false
                            }: EventEditorProps) {
    const {t} = useTranslation()
    const navigate = useNavigate()
    const [syncInfo, setSyncInfo] = useState<EventSyncInfo | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [pushing, setPushing] = useState<string | null>(null)
    const [deletingRemote, setDeletingRemote] = useState<string | null>(null)
    const [syncTargets, setSyncTargets] = useState<SyncTarget[]>([])
    const [creatingSyncItem, setCreatingSyncItem] = useState<string | null>(null)

    // Edit form state
    const [name, setName] = useState(event.name)
    const [startTime, setStartTime] = useState(toLocalDateTimeInputValue(event.startTime))
    const [endTime, setEndTime] = useState(toLocalDateTimeInputValue(event.endTime))
    const [tag, setTag] = useState(event.tag || '')
    const [useFullDays, setUseFullDays] = useState(event.useFullDays || false)
    const [changedFields, setChangedFields] = useState<Set<string>>(new Set())
    const [isSaving, setIsSaving] = useState(false)
    const [isDeleting, setIsDeleting] = useState(false)
    const [editError, setEditError] = useState<string | null>(null)
    const [calculatedPrices, setCalculatedPrices] = useState<CalculatedPrices | null>(null)
    const [calculatedPricesForm, setCalculatedPricesForm] = useState<CalculatedPricesFormValues>(
        EMPTY_CALCULATED_PRICES_FORM
    )
    const [calculatedPricesDirty, setCalculatedPricesDirty] = useState(false)
    const [calculatedPricesLoading, setCalculatedPricesLoading] = useState(true)
    const [calculatedPricesSaving, setCalculatedPricesSaving] = useState(false)
    const [calculatedPricesDeleting, setCalculatedPricesDeleting] = useState(false)
    const [calculatedPricesCreateMode, setCalculatedPricesCreateMode] = useState<'default' | 'blank' | null>(null)
    const [calculatedPricesError, setCalculatedPricesError] = useState<string | null>(null)
    const [canViewCalculatedPrices, setCanViewCalculatedPrices] = useState(false)
    const [canAddCalculatedPrices, setCanAddCalculatedPrices] = useState(false)
    const [canChangeCalculatedPrices, setCanChangeCalculatedPrices] = useState(false)
    const [canDeleteCalculatedPrices, setCanDeleteCalculatedPrices] = useState(false)
    const [canViewCalculatedPricesSection, setCanViewCalculatedPricesSection] = useState(true)
    const [canViewSyncSection, setCanViewSyncSection] = useState(true)

    // Calendar reference state – calendarRange is driven solely by WeekViewCombined's onWeekRangeChange
    const [calendarRange, setCalendarRange] = useState<{ startUtc: string; endUtc: string } | null>(null)
    const [externalEvents, setExternalEvents] = useState<ExternalCalendarEvent[]>([])
    const [calendarLoading, setCalendarLoading] = useState(false)
    const [calendarError, setCalendarError] = useState<string | null>(null)
    const [calendarNoAccess, setCalendarNoAccess] = useState(false)

    // Track unsaved changes
    const hasChanges = changedFields.size > 0
    const {confirmNavigation} = useUnsavedChanges(hasChanges)

    const calendarResources = useMemo<Resource[]>(() => [
        {
            id: 'event-editor-resource',
            name: t('event.EventSchedule'),
            color: '#2e7d32',
        },
    ], [t])

    const calendarEvents = useMemo<CalendarEvent[]>(() => {
        const referenceEvents: CalendarEvent[] = externalEvents.map((reference) => ({
            id: reference.id,
            resourceId: 'event-editor-resource',
            title: `${reference.title} (${reference.source})`,
            startUtc: reference.startUtc,
            endUtc: reference.endUtc,
            color: '#90a4ae',
        }))

        const selectedStart = parseLocalDateTimeInput(startTime)
        const selectedEnd = parseLocalDateTimeInput(endTime)
        let editedEvents: CalendarEvent[] = []

        if (selectedStart && selectedEnd && selectedEnd > selectedStart) {
            if (useFullDays) {
                // Single continuous event spanning over midnight
                editedEvents = [
                    {
                        id: `edited-${event.id}`,
                        resourceId: 'event-editor-resource',
                        title: name || event.name,
                        startUtc: selectedStart.toISOString(),
                        endUtc: selectedEnd.toISOString(),
                        color: '#2e7d32',
                    },
                ]
            } else {
                // Split into per-day blocks using the same daily start/end hours
                const startHours = selectedStart.getHours()
                const startMinutes = selectedStart.getMinutes()
                const endHours = selectedEnd.getHours()
                const endMinutes = selectedEnd.getMinutes()

                // Calculate the number of calendar days this event spans
                const startDay = new Date(selectedStart.getFullYear(), selectedStart.getMonth(), selectedStart.getDate())
                const endDay = new Date(selectedEnd.getFullYear(), selectedEnd.getMonth(), selectedEnd.getDate())
                const dayCount = Math.round((endDay.getTime() - startDay.getTime()) / 86_400_000) + 1

                if (dayCount <= 1) {
                    // Single day event, no splitting needed
                    editedEvents = [
                        {
                            id: `edited-${event.id}`,
                            resourceId: 'event-editor-resource',
                            title: name || event.name,
                            startUtc: selectedStart.toISOString(),
                            endUtc: selectedEnd.toISOString(),
                            color: '#2e7d32',
                        },
                    ]
                } else {
                    // Multi-day: each day gets start hours → end hours
                    for (let i = 0; i < dayCount; i++) {
                        const day = new Date(startDay.getFullYear(), startDay.getMonth(), startDay.getDate() + i)
                        const dayStart = new Date(day)
                        dayStart.setHours(startHours, startMinutes, 0, 0)
                        const dayEnd = new Date(day)
                        dayEnd.setHours(endHours, endMinutes, 0, 0)

                        // Correct days where end <= start (edge-case times, e.g. overnight block)
                        if (dayEnd <= dayStart) {
                            dayEnd.setDate(dayEnd.getDate() + 1)
                            if (dayEnd > selectedEnd) continue
                        }

                        editedEvents.push({
                            id: `edited-${event.id}-day${i}`,
                            resourceId: 'event-editor-resource',
                            title: name || event.name,
                            startUtc: dayStart.toISOString(),
                            endUtc: dayEnd.toISOString(),
                            color: '#2e7d32',
                        })
                    }
                }
            }
        }

        return [...referenceEvents, ...editedEvents]
    }, [endTime, event.id, event.name, externalEvents, name, startTime, useFullDays])

    // Expose confirmation function to parent
    useEffect(() => {
        if (onRequestNavigation) {
            onRequestNavigation(confirmNavigation)
        }
    }, [onRequestNavigation, confirmNavigation])

    // Reset form state when event changes
    useEffect(() => {
        setName(event.name)
        setStartTime(toLocalDateTimeInputValue(event.startTime))
        setEndTime(toLocalDateTimeInputValue(event.endTime))
        setTag(event.tag || '')
        setUseFullDays(event.useFullDays || false)
        setChangedFields(new Set())
        setEditError(null)
        // calendarRange is not set here – the remounted WeekViewCombined will
        // call onWeekRangeChange once, which is the single source of truth.
    }, [event.id, event.name, event.startTime, event.endTime, event.tag, event.useFullDays])

    useEffect(() => {
        const loadSyncData = async () => {
            try {
                setLoading(true)
                const permissions = await getUserPermissions()
                const canView = permissions.is_superuser || permissions.permissions.includes('viewrestricted_syncbasetarget')
                if (!canView) {
                    setCanViewSyncSection(false)
                    return
                }
                const [statusData, targetsData] = await Promise.all([
                    fetchSyncStatus(series.id, event.id),
                    fetchSyncTargets().catch(() => [] as SyncTarget[]),
                ])
                setSyncInfo(statusData)
                setSyncTargets(targetsData)
                setError(null)
            } catch (err) {
                setError(translateApiError(err instanceof Error ? err.message : undefined))
            } finally {
                setLoading(false)
            }
        }

        loadSyncData()
    }, [series.id, event.id])

    useEffect(() => {
        if (!calendarRange) return

        const loadExternalEvents = async () => {
            try {
                setCalendarLoading(true)
                const data = await fetchExternalCalendarEvents(calendarRange.startUtc, calendarRange.endUtc)
                setExternalEvents(data)
                setCalendarError(null)
                setCalendarNoAccess(false)
            } catch (err) {
                const code = err instanceof Error ? err.message : undefined
                if (code === 'auth.permissionDenied') {
                    setCalendarNoAccess(true)
                    setCalendarError(null)
                } else {
                    setCalendarNoAccess(false)
                    setCalendarError(translateApiError(code))
                }
            } finally {
                setCalendarLoading(false)
            }
        }

        loadExternalEvents()
    }, [calendarRange])

    useEffect(() => {
        let isMounted = true

        const loadCalculatedPrices = async () => {
            try {
                setCalculatedPricesLoading(true)
                setCalculatedPricesError(null)
                setCalculatedPricesDirty(false)

                const permissions = await getUserPermissions()
                const canAdd = permissions.is_superuser || permissions.permissions.includes('add_calculatedprices')
                const canViewGlobal = permissions.is_superuser || permissions.permissions.includes('view_calculatedprices')
                if (!isMounted) {
                    return
                }
                if (!canAdd && !canViewGlobal) {
                    setCanViewCalculatedPricesSection(false)
                    return
                }
                setCanAddCalculatedPrices(canAdd)

                const prices = await fetchCalculatedPrices(series.id, event.id)
                if (!isMounted) {
                    return
                }

                if (!prices) {
                    setCalculatedPrices(null)
                    setCalculatedPricesForm(EMPTY_CALCULATED_PRICES_FORM)
                    setCanViewCalculatedPrices(false)
                    setCanChangeCalculatedPrices(false)
                    setCanDeleteCalculatedPrices(false)
                    return
                }

                setCalculatedPrices(prices)
                setCalculatedPricesForm(toCalculatedPricesForm(prices))

                const [canView, canChange, canDelete] = await Promise.all([
                    checkObjectPermission({
                        app: 'sync_pretix',
                        action: 'view',
                        object_type: 'calculatedprices',
                        object_id: prices.id,
                    }),
                    checkObjectPermission({
                        app: 'sync_pretix',
                        action: 'change',
                        object_type: 'calculatedprices',
                        object_id: prices.id,
                    }),
                    checkObjectPermission({
                        app: 'sync_pretix',
                        action: 'delete',
                        object_type: 'calculatedprices',
                        object_id: prices.id,
                    }),
                ])

                if (!isMounted) {
                    return
                }

                setCanViewCalculatedPrices(canView)
                setCanChangeCalculatedPrices(canChange)
                setCanDeleteCalculatedPrices(canDelete)
            } catch (err) {
                if (!isMounted) {
                    return
                }
                setCalculatedPricesError(translateApiError(err instanceof Error ? err.message : undefined))
                setCalculatedPrices(null)
                setCalculatedPricesForm(EMPTY_CALCULATED_PRICES_FORM)
                setCanViewCalculatedPrices(false)
                setCanChangeCalculatedPrices(false)
                setCanDeleteCalculatedPrices(false)
            } finally {
                if (isMounted) {
                    setCalculatedPricesLoading(false)
                }
            }
        }

        void loadCalculatedPrices()

        return () => {
            isMounted = false
        }
    }, [series.id, event.id])

    const handleNameChange = (value: string) => {
        setName(value)
        setChangedFields((prev) => new Set(prev).add('name'))
        setEditError(null)
    }

    const handleStartTimeChange = (value: string) => {
        setStartTime(value)
        setChangedFields((prev) => new Set(prev).add('startTime'))
        setEditError(null)
    }

    const handleEndTimeChange = (value: string) => {
        setEndTime(value)
        setChangedFields((prev) => new Set(prev).add('endTime'))
        setEditError(null)
    }

    const handleTagChange = (value: string) => {
        setTag(value)
        setChangedFields((prev) => new Set(prev).add('tag'))
        setEditError(null)
    }

    const handleUseFullDaysChange = (value: boolean) => {
        setUseFullDays(value)
        setChangedFields((prev) => new Set(prev).add('useFullDays'))
        setEditError(null)
    }

    const handleCalendarSelection = (selection: Omit<CalendarEvent, 'id'>) => {
        handleStartTimeChange(toLocalDateTimeInputValue(new Date(selection.startUtc)))
        handleEndTimeChange(toLocalDateTimeInputValue(new Date(selection.endUtc)))
    }

    const handleSaveChanges = async () => {
        try {
            setIsSaving(true)
            setEditError(null)

            const updates: Record<string, unknown> = {}
            if (changedFields.has('name') && name !== event.name) {
                updates.name = name
            }
            if (changedFields.has('startTime') && startTime !== toLocalDateTimeInputValue(event.startTime)) {
                updates.startTime = parseLocalDateTimeInput(startTime)?.toISOString()
            }
            if (changedFields.has('endTime') && endTime !== toLocalDateTimeInputValue(event.endTime)) {
                updates.endTime = parseLocalDateTimeInput(endTime)?.toISOString()
            }
            if (changedFields.has('tag') && tag !== event.tag) {
                updates.tag = tag || null
            }
            if (changedFields.has('useFullDays') && useFullDays !== (event.useFullDays || false)) {
                updates.useFullDays = useFullDays
            }

            if (Object.keys(updates).length === 0) {
                setChangedFields(new Set())
                return
            }

            const updated = await updateEvent({
                seriesId: series.id,
                eventId: event.id,
                name: updates.name as string | undefined,
                startTime: updates.startTime as string | undefined,
                endTime: updates.endTime as string | undefined,
                tag: updates.tag as string | null | undefined,
                useFullDays: updates.useFullDays as boolean | undefined,
            })

            onEventUpdate(updated)

            setName(updated.name)
            setStartTime(toLocalDateTimeInputValue(updated.startTime))
            setEndTime(toLocalDateTimeInputValue(updated.endTime))
            setTag(updated.tag || '')
            setUseFullDays(updated.useFullDays || false)
            setChangedFields(new Set())
        } catch (err) {
            setEditError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setIsSaving(false)
        }
    }

    const handleCancel = () => {
        setName(event.name)
        setStartTime(toLocalDateTimeInputValue(event.startTime))
        setEndTime(toLocalDateTimeInputValue(event.endTime))
        setTag(event.tag || '')
        setUseFullDays(event.useFullDays || false)
        setChangedFields(new Set())
        setEditError(null)
    }

    const handleDelete = async () => {
        const confirmed = window.confirm(
            t('event.deleteConfirm', {name: event.name})
        )
        if (!confirmed) {
            return
        }

        try {
            setIsDeleting(true)
            setEditError(null)
            await onDeleteEvent?.()
        } catch (err) {
            setEditError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setIsDeleting(false)
        }
    }

    const handleCreateSyncItem = async (targetId: string) => {
        try {
            setCreatingSyncItem(targetId)
            await createSyncItem(targetId, series.id, event.id)

            // Refresh sync status after creating the item
            const data = await fetchSyncStatus(series.id, event.id)
            setSyncInfo(data)
        } catch (err) {
            setError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setCreatingSyncItem(null)
        }
    }

    const handlePush = async (targetId: string) => {
        try {
            setPushing(targetId)
            await pushToPlatform(series.id, event.id, targetId)

            // Refresh sync status
            const data = await fetchSyncStatus(series.id, event.id)
            setSyncInfo(data)
        } catch (err) {
            setError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setPushing(null)
        }
    }

    const handleViewDiff = (targetId: string) => {
        navigate(`/sync/diff/${series.id}/${event.id}/${encodeURIComponent(targetId)}`)
    }

    const handleDeleteRemote = async (targetId: string, platform: string) => {
        const confirmed = window.confirm(
            t('event.deleteRemoteConfirm', {name: event.name, platform})
        )
        if (!confirmed) return

        try {
            setDeletingRemote(targetId)
            await deleteRemoteSyncItem(series.id, event.id, targetId)

            // Refresh sync status so the card reflects the deleted state
            const data = await fetchSyncStatus(series.id, event.id)
            setSyncInfo(data)
        } catch (err) {
            setError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setDeletingRemote(null)
        }
    }

    const handleCalculatedPriceFieldChange = (field: keyof CalculatedPricesFormValues, value: string) => {
        setCalculatedPricesForm((prev) => ({...prev, [field]: value}))
        setCalculatedPricesDirty(true)
        setCalculatedPricesError(null)
    }

    const handleCreateCalculatedPrices = async (useDefaultPricingConfiguration: boolean) => {
        try {
            setCalculatedPricesError(null)
            setCalculatedPricesCreateMode(useDefaultPricingConfiguration ? 'default' : 'blank')

            const created = await createCalculatedPrices(series.id, event.id, useDefaultPricingConfiguration)
            setCalculatedPrices(created)
            setCalculatedPricesForm(toCalculatedPricesForm(created))
            setCalculatedPricesDirty(false)

            const [canView, canChange, canDelete] = await Promise.all([
                checkObjectPermission({
                    app: 'sync_pretix',
                    action: 'view',
                    object_type: 'calculatedprices',
                    object_id: created.id,
                }),
                checkObjectPermission({
                    app: 'sync_pretix',
                    action: 'change',
                    object_type: 'calculatedprices',
                    object_id: created.id,
                }),
                checkObjectPermission({
                    app: 'sync_pretix',
                    action: 'delete',
                    object_type: 'calculatedprices',
                    object_id: created.id,
                }),
            ])

            setCanViewCalculatedPrices(canView)
            setCanChangeCalculatedPrices(canChange)
            setCanDeleteCalculatedPrices(canDelete)
        } catch (err) {
            setCalculatedPricesError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setCalculatedPricesCreateMode(null)
        }
    }

    const handleSaveCalculatedPrices = async () => {
        if (!calculatedPrices) {
            return
        }

        try {
            setCalculatedPricesSaving(true)
            setCalculatedPricesError(null)

            const updated = await updateCalculatedPrices({
                seriesId: series.id,
                eventId: event.id,
                member_regular_gross_eur: toNullableDecimal(calculatedPricesForm.member_regular_gross_eur),
                member_discounted_gross_eur: toNullableDecimal(calculatedPricesForm.member_discounted_gross_eur),
                guest_regular_gross_eur: toNullableDecimal(calculatedPricesForm.guest_regular_gross_eur),
                guest_discounted_gross_eur: toNullableDecimal(calculatedPricesForm.guest_discounted_gross_eur),
                business_net_eur: toNullableDecimal(calculatedPricesForm.business_net_eur),
                internal_training_eur: toNullableDecimal(calculatedPricesForm.internal_training_eur),
            })

            setCalculatedPrices(updated)
            setCalculatedPricesForm(toCalculatedPricesForm(updated))
            setCalculatedPricesDirty(false)
        } catch (err) {
            setCalculatedPricesError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setCalculatedPricesSaving(false)
        }
    }

    const handleDeleteCalculatedPrices = async () => {
        if (!calculatedPrices) {
            return
        }

        const confirmed = window.confirm(t('event.deleteCalculatedPrices'))
        if (!confirmed) {
            return
        }

        try {
            setCalculatedPricesDeleting(true)
            setCalculatedPricesError(null)
            await deleteCalculatedPrices(series.id, event.id)
            setCalculatedPrices(null)
            setCalculatedPricesForm(EMPTY_CALCULATED_PRICES_FORM)
            setCalculatedPricesDirty(false)
            setCanViewCalculatedPrices(false)
            setCanChangeCalculatedPrices(false)
            setCanDeleteCalculatedPrices(false)
        } catch (err) {
            setCalculatedPricesError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setCalculatedPricesDeleting(false)
        }
    }

    const getStatusColor = (status: string): string => {
        switch (status) {
            case 'entry up-to-date':
                return '#4caf50' // green
            case 'entry differs':
                return '#ff9800' // orange
            case 'no entry exists':
                return '#9e9e9e' // grey – no sync item yet
            case 'creation pending':
                return '#2196f3' // blue – item created locally, not yet pushed
            case 'status unknown':
                return '#9c27b0' // purple – pushed but state not yet verified
            default:
                return '#666'
        }
    }

    const getStatusLabel = (status: string): string => {
        switch (status) {
            case 'entry up-to-date':
                return t('event.upToDate')
            case 'entry differs':
                return t('event.differs')
            case 'no entry exists':
                return t('event.noEntry')
            case 'creation pending':
                return t('event.creationPending')
            case 'status unknown':
                return t('event.statusUnknown')
            default:
                return status
        }
    }

    return (
        <div className={styles.container}>
            <div className={styles.header}>
                <h2>{t('event.editEvent')}</h2>
                <p className={styles.eventName}>{event.name}</p>
            </div>

            {/* Edit Form */}
            <div className={styles.editSection}>
                <h3>{t('event.eventDetails')}</h3>
                <form className={styles.editForm} aria-label="Edit event details">
                    <div className={styles.formGroup}>
                        <label htmlFor="event-name" className={styles.label}>
                            {t('event.name')}
                            {changedFields.has('name') &&
                                <span className={styles.changedIndicator} aria-label={t('event.unsavedChange')}>●</span>}
                        </label>
                        <input
                            id="event-name"
                            type="text"
                            value={name}
                            onChange={(e) => handleNameChange(e.target.value)}
                            className={`${styles.formInput} ${changedFields.has('name') ? styles.changed : ''}`}
                            disabled={isSaving || disabled}
                        />
                    </div>

                    <div className={styles.formRow}>
                        <div className={styles.formGroup}>
                            <label htmlFor="event-start-time" className={styles.label}>
                                {t('event.startTime')}
                                {changedFields.has('startTime') &&
                                    <span className={styles.changedIndicator} aria-label={t('event.unsavedChange')}>●</span>}
                            </label>
                            <input
                                id="event-start-time"
                                type="datetime-local"
                                value={startTime}
                                onChange={(e) => handleStartTimeChange(e.target.value)}
                                className={`${styles.formInput} ${changedFields.has('startTime') ? styles.changed : ''}`}
                                disabled={isSaving || disabled}
                            />
                        </div>

                        <div className={styles.formGroup}>
                            <label htmlFor="event-end-time" className={styles.label}>
                                {t('event.endTime')}
                                {changedFields.has('endTime') &&
                                    <span className={styles.changedIndicator} aria-label={t('event.unsavedChange')}>●</span>}
                            </label>
                            <input
                                id="event-end-time"
                                type="datetime-local"
                                value={endTime}
                                onChange={(e) => handleEndTimeChange(e.target.value)}
                                className={`${styles.formInput} ${changedFields.has('endTime') ? styles.changed : ''}`}
                                disabled={isSaving || disabled}
                            />
                        </div>
                    </div>

                    <div className={styles.formGroup}>
                        <label htmlFor="event-tag" className={styles.label}>
                            {t('event.tag')}
                            {changedFields.has('tag') &&
                                <span className={styles.changedIndicator} aria-label={t('event.unsavedChange')}>●</span>}
                        </label>
                        <input
                            id="event-tag"
                            type="text"
                            value={tag}
                            onChange={(e) => handleTagChange(e.target.value)}
                            placeholder={t('event.tagPlaceholder')}
                            className={`${styles.formInput} ${changedFields.has('tag') ? styles.changed : ''}`}
                            disabled={isSaving || disabled}
                        />
                    </div>

                    <div className={styles.formGroup}>
                        <label className={styles.checkboxLabel}>
                            <input
                                id="event-use-full-days"
                                type="checkbox"
                                checked={useFullDays}
                                onChange={(e) => handleUseFullDaysChange(e.target.checked)}
                                disabled={isSaving || disabled}
                                aria-describedby="event-use-full-days-desc"
                            />
                            <span>
                {t('event.spanFullDays')}
                                {changedFields.has('useFullDays') &&
                                    <span className={styles.changedIndicator} aria-label={t('event.unsavedChange')}>●</span>}
              </span>
                        </label>
                        <p id="event-use-full-days-desc" className={styles.fieldDescription}>
                            {t('event.useFullDaysDesc')}
                        </p>
                    </div>
                    <label>
                        {disabled ? t('event.viewOnlyNoPermission') : t('event.dragToUpdate')}
                    </label>
                    {calendarLoading && <p className={styles.loading}>{t('event.loading')}</p>}
                    {calendarError && <p className={styles.error}>{calendarError}</p>}
                    {calendarNoAccess && <p className={styles.info}>{t('event.calendarNoAccess')}</p>}
                    <div className={styles.calendarWrapper}
                         style={disabled ? {opacity: 0.6, pointerEvents: 'none'} : undefined}>
                        <WeekViewCombined
                            key={event.id}
                            resources={calendarResources}
                            events={calendarEvents}
                            startDate={event.startTime}
                            onEventCreate={handleCalendarSelection}
                            onWeekRangeChange={setCalendarRange}
                            disabled={disabled}
                        />
                    </div>
                    {editError && <div className={styles.editError} role="alert">{editError}</div>}

                    <div className={styles.buttonGroup}>
                        <button
                            type="button"
                            onClick={handleSaveChanges}
                            disabled={disabled || !hasChanges || isSaving || isDeleting}
                            className={styles.saveButton}
                            aria-busy={isSaving}
                        >
                            {isSaving ? t('event.saving') : t('event.saveChanges')}
                        </button>
                        {hasChanges && (
                            <button
                                type="button"
                                onClick={handleCancel}
                                disabled={isSaving || disabled}
                                className={styles.cancelButton}
                            >
                                {t('event.cancel')}
                            </button>
                        )}
                        {canDelete && onDeleteEvent && (
                            <button
                                type="button"
                                onClick={() => void handleDelete()}
                                disabled={isSaving || isDeleting}
                                className={styles.deleteButton}
                            >
                                {isDeleting ? t('event.deleting') : t('event.deleteEvent')}
                            </button>
                        )}
                    </div>
                </form>
                <br/>

            </div>

            {/* Calendar Time Picker */}
            <div className={styles.workflowSection}>
                <h3>{t('event.workflowStatus')}</h3>
                {/* Workflow status & transition buttons */}
                <EventTransitionButtons
                    seriesId={series.id}
                    eventId={event.id}
                    currentStatus={(event.status as string | undefined) ?? 'draft'}
                    onTransitionSuccess={(updatedEvent) => onEventUpdate(updatedEvent)}
                />
            </div>
            {canViewCalculatedPricesSection && <div className={styles.calculatedPricesSection}>
                <h3>{t('event.calculatedPrices')}</h3>

                {calculatedPricesLoading && <p className={styles.loading}>{t('event.loadingCalculatedPrices')}</p>}
                {!calculatedPricesLoading && calculatedPricesError &&
                    <p className={styles.error}>{calculatedPricesError}</p>}

                {!calculatedPricesLoading && !calculatedPrices && !calculatedPricesError && (
                    <div className={styles.calculatedPricesEmptyState}>
                        <p className={styles.note}>{t('event.noCalculatedPrices')}</p>
                        {canAddCalculatedPrices ? (
                            <div className={styles.buttonGroup}>
                                <button
                                    type="button"
                                    className={styles.saveButton}
                                    onClick={() => void handleCreateCalculatedPrices(true)}
                                    disabled={calculatedPricesCreateMode !== null}
                                >
                                    {calculatedPricesCreateMode === 'default' ? t('event.generating') : t('event.generateWithDefault')}
                                </button>
                                <button
                                    type="button"
                                    className={styles.cancelButton}
                                    onClick={() => void handleCreateCalculatedPrices(false)}
                                    disabled={calculatedPricesCreateMode !== null}
                                >
                                    {calculatedPricesCreateMode === 'blank' ? t('event.creating') : t('event.createBlankValues')}
                                </button>
                            </div>
                        ) : (
                            <p className={styles.note}>{t('event.noPermissionCreateCalculatedPrices')}</p>
                        )}
                    </div>
                )}

                {!calculatedPricesLoading && calculatedPrices && !canViewCalculatedPrices && (
                    <p className={styles.note}>{t('event.noPermissionViewCalculatedPrices')}</p>
                )}

                {!calculatedPricesLoading && calculatedPrices && canViewCalculatedPrices && (
                    <div className={styles.calculatedPricesForm}>
                        <div className={styles.formGroup}>
                            <label htmlFor="pricing-config-id" className={styles.label}>{t('event.pricingConfiguration')}</label>
                            <input
                                id="pricing-config-id"
                                type="text"
                                value={calculatedPrices.pricing_configuration_id ?? 'None (manual)'}
                                className={styles.formInput}
                                disabled
                            />
                        </div>

                        <div className={styles.calculatedPricesGrid}>
                            <div className={styles.formGroup}>
                                <label htmlFor="member-regular-gross" className={styles.label}>{t('event.memberRegularGross')}</label>
                                <input
                                    id="member-regular-gross"
                                    type="text"
                                    value={calculatedPricesForm.member_regular_gross_eur}
                                    onChange={(e) => handleCalculatedPriceFieldChange('member_regular_gross_eur', e.target.value)}
                                    className={styles.formInput}
                                    disabled={!canChangeCalculatedPrices || calculatedPricesSaving}
                                />
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="member-discounted-gross" className={styles.label}>{t('event.memberDiscountedGross')}</label>
                                <input
                                    id="member-discounted-gross"
                                    type="text"
                                    value={calculatedPricesForm.member_discounted_gross_eur}
                                    onChange={(e) => handleCalculatedPriceFieldChange('member_discounted_gross_eur', e.target.value)}
                                    className={styles.formInput}
                                    disabled={!canChangeCalculatedPrices || calculatedPricesSaving}
                                />
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="guest-regular-gross" className={styles.label}>{t('event.guestRegularGross')}</label>
                                <input
                                    id="guest-regular-gross"
                                    type="text"
                                    value={calculatedPricesForm.guest_regular_gross_eur}
                                    onChange={(e) => handleCalculatedPriceFieldChange('guest_regular_gross_eur', e.target.value)}
                                    className={styles.formInput}
                                    disabled={!canChangeCalculatedPrices || calculatedPricesSaving}
                                />
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="guest-discounted-gross" className={styles.label}>{t('event.guestDiscountedGross')}</label>
                                <input
                                    id="guest-discounted-gross"
                                    type="text"
                                    value={calculatedPricesForm.guest_discounted_gross_eur}
                                    onChange={(e) => handleCalculatedPriceFieldChange('guest_discounted_gross_eur', e.target.value)}
                                    className={styles.formInput}
                                    disabled={!canChangeCalculatedPrices || calculatedPricesSaving}
                                />
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="business-net" className={styles.label}>{t('event.businessNet')}</label>
                                <input
                                    id="business-net"
                                    type="text"
                                    value={calculatedPricesForm.business_net_eur}
                                    onChange={(e) => handleCalculatedPriceFieldChange('business_net_eur', e.target.value)}
                                    className={styles.formInput}
                                    disabled={!canChangeCalculatedPrices || calculatedPricesSaving}
                                />
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="internal-training" className={styles.label}>{t('event.internalTraining')}</label>
                                <input
                                    id="internal-training"
                                    type="text"
                                    value={calculatedPricesForm.internal_training_eur}
                                    onChange={(e) => handleCalculatedPriceFieldChange('internal_training_eur', e.target.value)}
                                    className={styles.formInput}
                                    disabled={!canChangeCalculatedPrices || calculatedPricesSaving}
                                />
                            </div>
                        </div>

                        <div className={styles.buttonGroup}>
                            {canChangeCalculatedPrices ? (
                                <button
                                    type="button"
                                    className={styles.saveButton}
                                    onClick={() => void handleSaveCalculatedPrices()}
                                    disabled={!calculatedPricesDirty || calculatedPricesSaving || calculatedPricesDeleting}
                                >
                                    {calculatedPricesSaving ? t('event.saving') : t('event.saveCalculatedPrices')}
                                </button>
                            ) : (
                                <p className={styles.note}>{t('event.noPermissionChangeCalculatedPrices')}</p>
                            )}

                            {canDeleteCalculatedPrices && (
                                <button
                                    type="button"
                                    className={styles.deleteButton}
                                    onClick={() => void handleDeleteCalculatedPrices()}
                                    disabled={calculatedPricesDeleting || calculatedPricesSaving}
                                >
                                    {calculatedPricesDeleting ? t('event.deleting') : t('event.deleteCalculatedPricesBtn')}
                                </button>
                            )}
                        </div>
                    </div>
                )}
            </div>}


            {/* Sync Section */}
            {canViewSyncSection && <div className={styles.syncSection}>
                <h3>{t('event.syncSynchronization')}</h3>

                {loading && <p className={styles.loading}>{t('event.loadingSyncStatus')}</p>}
                {error && <p className={styles.error}>{error}</p>}

                {syncInfo && (
                    <div className={styles.syncGrid}>
                        {syncInfo.sync_statuses.map((sync) => {
                            // Find the matching sync target for this specific target id
                            const matchingTarget = syncTargets.find((t) => t.id === sync.target_id)

                            return (
                                <div key={sync.target_id} className={styles.syncCard}>
                                    <div className={styles.cardHeader}>
                                        <h4>{sync.platform}</h4>
                                        <div
                                            className={styles.statusBadge}
                                            style={{backgroundColor: getStatusColor(sync.status)}}
                                        >
                                            {getStatusLabel(sync.status)}
                                        </div>
                                    </div>

                                    {matchingTarget && Object.keys(matchingTarget.public_properties).length > 0 && (
                                        <div className={styles.cardBody}>
                                            {Object.entries(matchingTarget.public_properties).map(([key, value]) => (
                                                <div key={key} className={styles.info}>
                                                    <span className={styles.infoLabel}>{t(`syncTarget.${key}`, key)}:</span>
                                                    <span className={styles.infoValue}>{value}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}

                                    <div className={styles.cardBody}>
                                        {sync.last_synced && (
                                            <div className={styles.info}>
                                                <span className={styles.infoLabel}>{t('event.lastSynced')}</span>
                                                <span className={styles.infoValue}>
                          {new Date(sync.last_synced).toLocaleDateString('de-DE', {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                          })}
                        </span>
                                            </div>
                                        )}

                                        {sync.item_url && (
                                            <div className={styles.info}>
                                                <span className={styles.infoLabel}>{t('event.remoteEntry')}</span>
                                                <span className={styles.infoValue}>
                          <a
                              href={sync.item_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              aria-label={t('event.openInPlatform', {platform: sync.platform})}
                          >
                            {t('event.openInPlatform', {platform: sync.platform})}
                          </a>
                        </span>
                                            </div>
                                        )}

                                        {sync.last_error && (
                                            <div className={styles.error}>
                                                <span className={styles.infoLabel}>{t('event.error')}</span>
                                                <span className={styles.infoValue}>{sync.last_error}</span>
                                            </div>
                                        )}
                                    </div>

                                    {sync.status === 'no entry exists' && matchingTarget ? (
                                        // No sync item exists yet → offer to create one.
                                        <button
                                            type="button"
                                            className={styles.pushButton}
                                            onClick={() => handleCreateSyncItem(matchingTarget.id)}
                                            disabled={creatingSyncItem === matchingTarget.id || !sync.can_push}
                                            aria-label={t('event.createSyncEntry')}
                                        >
                                            {creatingSyncItem === matchingTarget.id ? t('event.creatingSync') : t('event.createSyncEntry')}
                                        </button>
                                    ) : (
                                        // Sync item exists (pending, unknown, up-to-date, or differs) →
                                        // offer push (creates or updates remote) and diff (preview or compare).
                                        <>
                                            <button
                                                type="button"
                                                className={styles.pushButton}
                                                onClick={() => handlePush(sync.target_id)}
                                                disabled={pushing === sync.target_id || deletingRemote === sync.target_id || !sync.can_push}
                                                aria-label={t('event.pushUpdate')}
                                            >
                                                {pushing === sync.target_id
                                                    ? t('event.pushing')
                                                    : sync.status === 'creation pending'
                                                        ? t('event.createInRemoteService')
                                                        : t('event.pushUpdate')}
                                            </button>

                                            <button
                                                type="button"
                                                className={styles.diffButton}
                                                onClick={() => handleViewDiff(sync.target_id)}
                                                disabled={pushing === sync.target_id || deletingRemote === sync.target_id}
                                                aria-label={t('event.viewDiff')}
                                            >
                                                {sync.status === 'creation pending' ? t('event.preview') : t('event.viewDiff')}
                                            </button>

                                            <button
                                                type="button"
                                                className={styles.deleteSyncButton}
                                                onClick={() => void handleDeleteRemote(sync.target_id, sync.platform)}
                                                disabled={pushing === sync.target_id || deletingRemote === sync.target_id || !sync.can_delete}
                                                aria-label={t('event.deleteRemote')}
                                            >
                                                {deletingRemote === sync.target_id ? t('event.deletingRemote') : t('event.deleteRemote')}
                                            </button>
                                        </>
                                    )}
                                </div>
                            )
                        })}
                    </div>
                )}
            </div>}

            {canViewSyncSection && <div className={styles.footer}>
                <p className={styles.note}>
                    {t('event.syncNote')}
                </p>
            </div>}
        </div>
    )
}

