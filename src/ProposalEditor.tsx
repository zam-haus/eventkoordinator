import React, {useState, useEffect, useRef} from 'react'
import {Link, useNavigate, useSearchParams} from 'react-router-dom'
import mermaid from 'mermaid'
import {
    fetchProposal,
    updateProposal,
    fetchProposalChecklist,
    fetchProposalHistory,
    fetchProposalSpeakers,
    fetchSubmissionTypes,
    fetchProposalLanguages,
    fetchProposalAreas,
    fetchCalls,
    searchUsers,
    fetchProposalEvents,
    fetchProposalTransitions,
    fetchProposalFlowDiagram,
    createEvent,
    createSeries,
    searchSeriesAutocomplete,
    uploadProposalImage,
    type ProposalDetail,
    type ProposalChecklistItem,
    type ProposalHistoryEntry,
    type ProposalSpeakerOut,
    type LookupItem,
    type UserBasic,
    type ProposalEventSummary,
    type Series,
    type CallOut,
    type EventFlowDiagram,
} from './api'
mermaid.initialize({ startOnLoad: false, theme: 'default' })
let proposalDiagramCounter = 0

import {useUnsavedChanges} from './useUnsavedChanges'
import {usePermissions} from './usePermissions'
import {useTranslation} from 'react-i18next'
import {ImageUploadField} from './ImageUploadField'
import {SpeakerListEditor} from './SpeakerListEditor'
import {ProposalTransitionButtons} from './ProposalTransitionButtons'
import { translateApiError } from './apiError'
import {EventStatusBadge} from './EventStatusBadge'
import {ReviewsSection} from './ProposalReviews'
import styles from './EventGeneralEditor.module.css'

const NEW_SERIES_SENTINEL = '__new__'

const CHECKLIST_FIELD_MAP: Record<string, { tabId: number; elementId: string }> = {
    title: {tabId: 0, elementId: 'proposal-title'},
    abstract: {tabId: 0, elementId: 'proposal-abstract'},
    description: {tabId: 0, elementId: 'proposal-description'},
    duration: {tabId: 2, elementId: 'proposal-duration-section'},
    maxParticipants: {tabId: 1, elementId: 'proposal-max-participants'},
    occurrenceCount: {tabId: 2, elementId: 'proposal-occurrence-count'},
    preferredDates: {tabId: 2, elementId: 'proposal-preferred-dates'},
    language: {tabId: 0, elementId: 'proposal-language'},
    submissionType: {tabId: 0, elementId: 'proposal-submission-type'},
    workshopArea: {tabId: 0, elementId: 'proposal-area'},
    atLeastOneSpeaker: {tabId: 3, elementId: 'proposal-speaker-list-section'},
    speakersHaveBio: {tabId: 3, elementId: 'proposal-speaker-list-section'},
    photo: {tabId: 0, elementId: 'proposal-image-section'},
    submissionDeadline: {tabId: 0, elementId: 'proposal-call'},
}

interface ProposalFormData {
    // General section
    title: string
    submission_type: string
    area: string
    language: string
    abstract: string
    description: string
    internal_notes: string
    occurrence_count: number
    duration_days: number
    duration_time_per_day: string
    call_id: string | null

    // Additional information section
    is_basic_course: boolean
    max_participants: number
    material_cost_eur: string
    preferred_dates: string

    // Profile section
    has_building_access: boolean

    // Moderation section (requires moderate_proposal permission to edit)
    moderation_comment: string
}

interface ProposalEditorProps {
    proposalId?: string
    canEdit?: boolean
    canDelete?: boolean
    onProposalSave?: (formData: ProposalFormData, freshProposal: ProposalDetail) => void
    onDeleteProposal?: (proposalId: string) => Promise<void>
    onRequestNavigation?: (confirmFn: () => Promise<boolean>) => void
    onTransitionSuccess?: () => void
    onUnsavedChangesChange?: (hasChanges: boolean) => void
}

const DEFAULT_FORM_DATA: ProposalFormData = {
    title: '',
    submission_type: '',
    area: '',
    language: '',
    abstract: '',
    description: '',
    internal_notes: '',
    call_id: null,
    occurrence_count: 1,
    duration_days: 1,
    duration_time_per_day: '00:00',

    is_basic_course: false,
    max_participants: 0,
    material_cost_eur: '0.00',
    preferred_dates: '',

    has_building_access: false,

    moderation_comment: '',
}

const TAB_FIELD_MAP: Record<number, Array<ChangedFieldName>> = {
    0: ['title', 'submission_type', 'area', 'language', 'abstract', 'description', 'call_id'],
    1: ['is_basic_course', 'max_participants', 'material_cost_eur'],
    2: ['duration_days', 'duration_time_per_day', 'occurrence_count', 'preferred_dates'],
    3: ['has_building_access', 'editors'],
    4: ['internal_notes'],
    5: [],
}

function hasTabUnsavedChanges(tabId: number, changedFields: Set<ChangedFieldName>): boolean {
    const fields = TAB_FIELD_MAP[tabId]
    if (!fields || fields.length === 0) {
        return false
    }
    return fields.some((field) => changedFields.has(field))
}

type ChangedFieldName = keyof ProposalFormData | 'editors'

function proposalToFormData(data: ProposalDetail): ProposalFormData {
    return {
        title: data.title,
        submission_type: data.submission_type,
        area: data.area || '',
        language: data.language || '',
        abstract: data.abstract,
        description: data.description,
        internal_notes: data.internal_notes,
        occurrence_count: data.occurrence_count,
        duration_days: data.duration_days,
        duration_time_per_day: data.duration_time_per_day,
        call_id: data.call_id || null,
        is_basic_course: data.is_basic_course,
        max_participants: data.max_participants,
        material_cost_eur: data.material_cost_eur,
        preferred_dates: data.preferred_dates,
        has_building_access: data.has_building_access,
        moderation_comment: data.moderation_comment || '',
    }
}

function copyProposalField<K extends keyof ProposalFormData>(
    target: ProposalFormData,
    source: ProposalFormData,
    fieldName: K,
) {
    target[fieldName] = source[fieldName]
}

function pad2(value: number): string {
    return value.toString().padStart(2, '0')
}

function formatLocalIso(dateValue: string): string {
    const date = new Date(dateValue)
    if (Number.isNaN(date.getTime())) {
        return dateValue
    }

    const year = date.getFullYear()
    const month = pad2(date.getMonth() + 1)
    const day = pad2(date.getDate())
    const hours = pad2(date.getHours())
    const minutes = pad2(date.getMinutes())
    const seconds = pad2(date.getSeconds())

    const offsetMinutes = -date.getTimezoneOffset()
    const sign = offsetMinutes >= 0 ? '+' : '-'
    const absOffset = Math.abs(offsetMinutes)
    const offsetHours = pad2(Math.floor(absOffset / 60))
    const offsetMins = pad2(absOffset % 60)

    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds} ${sign}${offsetHours}:${offsetMins}`
}

function formatRelativeTime(dateValue: string, t: ReturnType<typeof useTranslation>['t']): string {
    const date = new Date(dateValue)
    if (Number.isNaN(date.getTime())) {
        return t('proposal.justNow')
    }

    const deltaMs = Date.now() - date.getTime()
    const deltaSeconds = Math.max(0, Math.floor(deltaMs / 1000))

    if (deltaSeconds < 60) {
        return t('proposal.justNow')
    }

    const minutes = Math.floor(deltaSeconds / 60)
    if (minutes < 60) {
        return t('proposal.minutesAgo', {count: minutes})
    }

    const hours = Math.floor(minutes / 60)
    if (hours < 24) {
        return t('proposal.hoursAgo', {count: hours})
    }

    const days = Math.floor(hours / 24)
    if (days < 30) {
        return t('proposal.daysAgo', {count: days})
    }

    const months = Math.floor(days / 30)
    if (months < 12) {
        return t('proposal.monthsAgo', {count: months})
    }

    const years = Math.floor(months / 12)
    return t('proposal.yearsAgo', {count: years})
}

export function ProposalEditor({
                                   proposalId: _proposalId,
                                   canEdit = false,
                                   canDelete = false,
                                   onProposalSave,
                                   onDeleteProposal,
                                   onRequestNavigation,
                                   onTransitionSuccess: onTransitionSuccessProp,
                                   onUnsavedChangesChange,
                               }: ProposalEditorProps) {
    const {t, i18n} = useTranslation()
    const navigate = useNavigate()
    const {canView, canAdd, hasPermission, loading: permissionsLoading} = usePermissions()
    const [formData, setFormData] = useState<ProposalFormData>(DEFAULT_FORM_DATA)
    const [changedFields, setChangedFields] = useState<Set<ChangedFieldName>>(new Set())
    const changedFieldsRef = useRef(changedFields)
    const [isSaving, setIsSaving] = useState(false)
    const [isDeleting, setIsDeleting] = useState(false)
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [checklist, setChecklist] = useState<Record<string, ProposalChecklistItem>>({})
    const [checklistLoading, setChecklistLoading] = useState(false)
    const [highlightedChecklistItem, setHighlightedChecklistItem] = useState<string | null>(null)
    const [history, setHistory] = useState<ProposalHistoryEntry[]>([])
    const [historyLoading, setHistoryLoading] = useState(false)
    const [speakers, setSpeakers] = useState<ProposalSpeakerOut[]>([])
    const [submissionTypes, setSubmissionTypes] = useState<LookupItem[]>([])
    const [proposalLanguages, setProposalLanguages] = useState<LookupItem[]>([])
    const [proposalAreas, setProposalAreas] = useState<LookupItem[]>([])
    const [availableCalls, setAvailableCalls] = useState<CallOut[]>([])
    const [lookupLoading, setLookupLoading] = useState(true)
    const [owner, setOwner] = useState<UserBasic | null>(null)
    const [editors, setEditors] = useState<UserBasic[]>([])
    const [participantSearchQuery, setParticipantSearchQuery] = useState('')
    const [participantSearchResults, setParticipantSearchResults] = useState<UserBasic[]>([])
    const [participantDropdownOpen, setParticipantDropdownOpen] = useState(false)
    const [participantHighlightedIndex, setParticipantHighlightedIndex] = useState(-1)
    const participantDropdownRef = useRef<HTMLDivElement>(null)
    const [linkedEvents, setLinkedEvents] = useState<ProposalEventSummary[]>([])
    const [linkedEventsLoading, setLinkedEventsLoading] = useState(false)
    const [currentStatus, setCurrentStatus] = useState<string>('')
    const [transitionButtonsVersion, setTransitionButtonsVersion] = useState(0)
    const [proposalPhotoUrl, setProposalPhotoUrl] = useState<string | null>(null)
    const [isProposalImageUploading, setIsProposalImageUploading] = useState(false)
    const [proposalImageUploadProgress, setProposalImageUploadProgress] = useState<number | null>(null)
    const [proposalImageError, setProposalImageError] = useState<string | null>(null)
    const [proposalImageCopyrightChecked, setProposalImageCopyrightChecked] = useState(false)

    const [maxParticipantsRaw, setMaxParticipantsRaw] = useState(String(formData.max_participants))
    const [durationDaysRaw, setDurationDaysRaw] = useState(String(formData.duration_days))
    const [occurrenceCountRaw, setOccurrenceCountRaw] = useState(String(formData.occurrence_count))

    useEffect(() => { setMaxParticipantsRaw(String(formData.max_participants)) }, [formData.max_participants])
    useEffect(() => { setDurationDaysRaw(String(formData.duration_days)) }, [formData.duration_days])
    useEffect(() => { setOccurrenceCountRaw(String(formData.occurrence_count)) }, [formData.occurrence_count])

    // Lifecycle diagram state
    const [flowChartOpen, setFlowChartOpen] = useState(false)
    const [flowDiagram, setFlowDiagram] = useState<EventFlowDiagram | null>(null)
    const [flowChartLoading, setFlowChartLoading] = useState(false)
    const [flowChartError, setFlowChartError] = useState<string | null>(null)
    const [renderedSvg, setRenderedSvg] = useState<string | null>(null)
    const diagramRenderingRef = useRef(false)

    // Event creation state
    const [seriesSearchQuery, setSeriesSearchQuery] = useState('')
    const [seriesSearchResults, setSeriesSearchResults] = useState<Series[]>([])
    const [selectedSeriesId, setSelectedSeriesId] = useState<string>('')
    const [isCreatingEvent, setIsCreatingEvent] = useState(false)
    const [createEventError, setCreateEventError] = useState<string | null>(null)
    const latestHistoryEntry = history.reduce<ProposalHistoryEntry | null>((latest, entry) => {
        if (!latest) return entry
        return new Date(entry.timestamp).getTime() > new Date(latest.timestamp).getTime() ? entry : latest
    }, null)
    const historyBadge = latestHistoryEntry
        ? `${t('proposal.lastChanged')} ${formatRelativeTime(latestHistoryEntry.timestamp, t)}`
        : t('proposal.noRecentChanges')

    useEffect(() => {
        changedFieldsRef.current = changedFields
    }, [changedFields])

    const updateChangedFields = (
        updater: (previous: Set<ChangedFieldName>) => Set<ChangedFieldName>
    ) => {
        setChangedFields((previous) => {
            const next = updater(previous)
            changedFieldsRef.current = next
            return next
        })
    }

    const markFieldAsChanged = (fieldName: ChangedFieldName) => {
        updateChangedFields((previous) => {
            const next = new Set(previous)
            next.add(fieldName)
            return next
        })
    }

    const clearChangedFields = () => {
        const next = new Set<ChangedFieldName>()
        changedFieldsRef.current = next
        setChangedFields(next)
    }

    const applyProposalData = (data: ProposalDetail, resetChangedFields: boolean) => {
        const nextFormData = proposalToFormData(data)

        setFormData((previous) => {
            if (resetChangedFields || changedFieldsRef.current.size === 0) {
                return nextFormData
            }

            const merged: ProposalFormData = {...previous}
            for (const fieldName of Object.keys(nextFormData) as Array<keyof ProposalFormData>) {
                if (!changedFieldsRef.current.has(fieldName)) {
                    copyProposalField(merged, nextFormData, fieldName)
                }
            }
            return merged
        })

        setOwner(data.owner || null)
        setProposalPhotoUrl(data.photo || null)

        if (resetChangedFields || !changedFieldsRef.current.has('editors')) {
            setEditors(data.editors || [])
        }

        if (resetChangedFields) {
            clearChangedFields()
        }
    }

    const handleDelete = async () => {
        if (!_proposalId || !onDeleteProposal) {
            return
        }

        const confirmed = window.confirm(
            t('proposal.confirmDelete', {title: formData.title || t('proposal.untitledProposal')})
        )
        if (!confirmed) {
            return
        }

        try {
            setIsDeleting(true)
            setError(null)
            await onDeleteProposal(_proposalId)
        } catch (err) {
            setError(t('proposal.failedToDelete'))
        } finally {
            setIsDeleting(false)
        }
    }

    // Load proposal data when proposalId changes
    useEffect(() => {
        if (_proposalId && _proposalId.trim()) {
            const loadProposal = async () => {
                try {
                    setIsLoading(true)
                    setError(null)
                    const data = await fetchProposal(_proposalId)
                    applyProposalData(data, false)
                } catch (err) {
                    setError(t('proposal.failedToLoad'))
                    console.error('Failed to load proposal:', err)
                } finally {
                    setIsLoading(false)
                }
            }
            loadProposal()
        }
    }, [_proposalId])

    // Load proposal checklist when proposalId changes
    useEffect(() => {
        if (_proposalId && _proposalId.trim()) {
            const loadChecklist = async () => {
                try {
                    setChecklistLoading(true)
                    const data = await fetchProposalChecklist(_proposalId)
                    setChecklist(data)
                } catch (err) {
                    console.error('Failed to load checklist:', err)
                } finally {
                    setChecklistLoading(false)
                }
            }
            loadChecklist()
        }
    }, [_proposalId])

    // Load proposal history when proposalId changes
    useEffect(() => {
        if (_proposalId && _proposalId.trim()) {
            const loadHistory = async () => {
                try {
                    setHistoryLoading(true)
                    const data = await fetchProposalHistory(_proposalId, 7)
                    setHistory(data.entries)
                } catch (err) {
                    console.error('Failed to load history:', err)
                } finally {
                    setHistoryLoading(false)
                }
            }
            loadHistory()
        }
    }, [_proposalId])

    // Load speakers when proposalId changes
    useEffect(() => {
        if (_proposalId && _proposalId.trim()) {
            const loadSpeakers = async () => {
                try {
                    const data = await fetchProposalSpeakers(_proposalId)
                    setSpeakers(data)
                } catch (err) {
                    console.error('Failed to load speakers:', err)
                }
            }
            loadSpeakers()
        }
    }, [_proposalId])

    // Load linked events and current status when proposalId changes
    useEffect(() => {
        if (_proposalId && _proposalId.trim()) {
            const loadLinkedEvents = async () => {
                try {
                    setLinkedEventsLoading(true)
                    const events = await fetchProposalEvents(_proposalId)
                    setLinkedEvents(events)
                } catch (err) {
                    console.error('Failed to load linked events:', err)
                } finally {
                    setLinkedEventsLoading(false)
                }
            }
            const loadStatus = async () => {
                try {
                    const data = await fetchProposalTransitions(_proposalId)
                    setCurrentStatus(data.current_status)
                } catch (err) {
                    console.error('Failed to load proposal status:', err)
                }
            }
            loadLinkedEvents()
            loadStatus()
        }
    }, [_proposalId])

    // Debounced series search for event creation
    useEffect(() => {
        if (!_proposalId || !_proposalId.trim() || currentStatus !== 'accepted') {
            setSeriesSearchResults([])
            setSelectedSeriesId('')
            return
        }

        const timer = setTimeout(async () => {
            try {
                const results = await searchSeriesAutocomplete(seriesSearchQuery)
                setSeriesSearchResults(results)
            } catch (err) {
                console.error('Failed to search series:', err)
            }
        }, 300)
        return () => clearTimeout(timer)
    }, [_proposalId, currentStatus, seriesSearchQuery])

    const handleCreateEvent = async () => {
        if (!selectedSeriesId || !_proposalId) return
        try {
            setIsCreatingEvent(true)
            setCreateEventError(null)
            let seriesId = selectedSeriesId
            if (selectedSeriesId === NEW_SERIES_SENTINEL) {
                const newSeries = await createSeries({name: formData.title})
                seriesId = newSeries.id
            }
            const newEvent = await createEvent({
                seriesId,
                name: formData.title,
                proposal_id: _proposalId,
            })
            navigate(`/proposal/${_proposalId}/event/${newEvent.id}`)
        } catch (err) {
            setCreateEventError(t('proposal.failedToCreateEvent'))
        } finally {
            setIsCreatingEvent(false)
        }
    }

    // Load submission types, languages, and areas on mount
    useEffect(() => {
        const loadLookups = async () => {
            try {
                setLookupLoading(true)
                const [types, languages, areas, calls] = await Promise.all([
                    fetchSubmissionTypes(),
                    fetchProposalLanguages(),
                    fetchProposalAreas(),
                    fetchCalls(false),
                ])
                setSubmissionTypes(types)
                setProposalLanguages(languages)
                setProposalAreas(areas)
                setAvailableCalls(calls)
            } catch (err) {
                console.error('Failed to load lookup tables:', err)
            } finally {
                setLookupLoading(false)
            }
        }
        loadLookups()
    }, [])

    // Track unsaved changes
    const hasChanges = changedFields.size > 0
    const canViewLinkedEvents = Boolean(_proposalId && _proposalId.trim())
    const canLinkEvents = canViewLinkedEvents && currentStatus === 'accepted'
    const canShowLinkedEventCreateControls =
        canLinkEvents && !permissionsLoading && canView('series') && canAdd('event')
    const matchingSeries =
        canShowLinkedEventCreateControls && formData.title.trim() !== ''
            ? seriesSearchResults.find(
                  (s) => s.name.toLowerCase() === formData.title.toLowerCase()
              ) ?? null
            : null
    const showCreateNewSeriesOption =
        canShowLinkedEventCreateControls &&
        !permissionsLoading &&
        canAdd('series') &&
        formData.title.trim() !== '' &&
        !matchingSeries
    const {confirmNavigation} = useUnsavedChanges(hasChanges)

    // Auto-select the matching or "create new" series option when it becomes available and nothing is selected
    useEffect(() => {
        if (matchingSeries) {
            if (selectedSeriesId === '' || selectedSeriesId === NEW_SERIES_SENTINEL) {
                setSelectedSeriesId(matchingSeries.id)
            }
        } else if (showCreateNewSeriesOption) {
            if (selectedSeriesId === '') {
                setSelectedSeriesId(NEW_SERIES_SENTINEL)
            }
        } else if (selectedSeriesId === NEW_SERIES_SENTINEL) {
            setSelectedSeriesId('')
        }
    }, [matchingSeries?.id, showCreateNewSeriesOption])

    // Notify parent about unsaved changes state
    useEffect(() => {
        if (onUnsavedChangesChange) {
            onUnsavedChangesChange(hasChanges)
        }
    }, [hasChanges, onUnsavedChangesChange])

    // Expose confirmation function to parent
    useEffect(() => {
        if (onRequestNavigation) {
            onRequestNavigation(confirmNavigation)
        }
    }, [onRequestNavigation, confirmNavigation])


    // Debounced participant search
    useEffect(() => {
        if (!participantSearchQuery.trim()) {
            setParticipantSearchResults([])
            return
        }

        const timer = setTimeout(async () => {
            try {
                const results = await searchUsers(participantSearchQuery)
                setParticipantSearchResults(results)
            } catch (err) {
                console.error('Failed to search users:', err)
            }
        }, 300)

        return () => clearTimeout(timer)
    }, [participantSearchQuery])

    const handleFieldChange = (fieldName: keyof ProposalFormData, value: unknown) => {
        setFormData((prev) => ({
            ...prev,
            [fieldName]: value,
        }))
        markFieldAsChanged(fieldName)
        setError(null)
    }


    const handleRemoveEditor = (userId: string) => {
        setEditors((prev) => prev.filter((p) => p.id !== userId))
        markFieldAsChanged('editors')
        setError(null)
    }

    const handleAddEditor = (user: UserBasic) => {
        if (!editors.find((p) => p.id === user.id)) {
            setEditors((prev) => [...prev, user])
            markFieldAsChanged('editors')
            setError(null)
        }
        setParticipantSearchQuery('')
        setParticipantDropdownOpen(false)
        setParticipantHighlightedIndex(-1)
    }

    const handleParticipantKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        const filteredResults = participantSearchResults.filter((u) => !editors.find((p) => p.id === u.id))
        if (e.key === 'ArrowDown') {
            e.preventDefault()
            setParticipantHighlightedIndex((prev) => Math.min(prev + 1, filteredResults.length - 1))
        } else if (e.key === 'ArrowUp') {
            e.preventDefault()
            setParticipantHighlightedIndex((prev) => Math.max(prev - 1, -1))
        } else if (e.key === 'Enter') {
            e.preventDefault()
            if (participantHighlightedIndex >= 0 && filteredResults[participantHighlightedIndex]) {
                handleAddEditor(filteredResults[participantHighlightedIndex])
            } else if (filteredResults.length === 1) {
                handleAddEditor(filteredResults[0])
            }
        } else if (e.key === 'Escape') {
            setParticipantDropdownOpen(false)
            setParticipantHighlightedIndex(-1)
        }
    }

    useEffect(() => {
        setParticipantHighlightedIndex(-1)
    }, [participantSearchResults])

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (participantDropdownRef.current && !participantDropdownRef.current.contains(e.target as Node)) {
                setParticipantDropdownOpen(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const handleProposalImageUpload = async (file: File) => {
        if (!_proposalId || !_proposalId.trim()) {
            setProposalImageError('Save the proposal before uploading an image.')
            return
        }

        try {
            setIsProposalImageUploading(true)
            setProposalImageUploadProgress(0)
            setProposalImageError(null)

            const updatedProposal = await uploadProposalImage(_proposalId, file, setProposalImageUploadProgress)
            setProposalPhotoUrl(updatedProposal.photo || null)
        } catch (err) {
            setProposalImageError(translateApiError(err instanceof Error ? err.message : undefined))
        } finally {
            setIsProposalImageUploading(false)
            setProposalImageUploadProgress(null)
        }
    }

    const handleSave = async () => {
        if (!_proposalId || !_proposalId.trim()) {
            setError(t('proposal.noProposalId'))
            return
        }

        try {
            setIsSaving(true)
            setError(null)


            // No validation - allow saving with incomplete data
            // Checklist will show what's missing

            // Build update payload with only changed fields
            // Convert empty strings to null for optional fields
            const updatePayload: Record<string, unknown> = {}
            const formFields: Array<keyof ProposalFormData> = [
                'title',
                'submission_type',
                'area',
                'language',
                'abstract',
                'description',
                'internal_notes',
                'occurrence_count',
                'duration_days',
                'duration_time_per_day',
                'call_id',
                'is_basic_course',
                'max_participants',
                'material_cost_eur',
                'preferred_dates',
                'has_building_access',
                'moderation_comment',
            ]

            for (const fieldName of formFields) {
                if (!changedFields.has(fieldName)) {
                    continue
                }
                const value = formData[fieldName]
                if (['submission_type', 'area', 'language'].includes(fieldName) && value === '') {
                    updatePayload[fieldName] = null
                } else {
                    updatePayload[fieldName] = value
                }
            }

            if (changedFields.has('editors')) {
                updatePayload.editor_ids = editors.map((p) => p.id)
            }

            // Save to API - only send changed fields
            await updateProposal(_proposalId, updatePayload)

            // Reload proposal data to get fresh state from server
            const freshData = await fetchProposal(_proposalId)
            applyProposalData(freshData, true)

            // Reload checklist
            const freshChecklist = await fetchProposalChecklist(_proposalId)
            setChecklist(freshChecklist)

            // Reload speakers
            const freshSpeakers = await fetchProposalSpeakers(_proposalId)
            setSpeakers(freshSpeakers)
            setTransitionButtonsVersion((previous) => previous + 1)
            if (onProposalSave) {
                onProposalSave(formData, freshData)
            }
        } catch (err) {
            setError(t('proposal.failedToSave'))
        } finally {
            setIsSaving(false)
        }
    }

    const handleCancel = async () => {
        setParticipantSearchQuery('')
        setError(null)
        if (_proposalId && _proposalId.trim()) {
            try {
                const data = await fetchProposal(_proposalId)
                applyProposalData(data, true)
            } catch (err) {
                setError(t('proposal.failedToLoad'))
            }
        } else {
            setFormData(DEFAULT_FORM_DATA)
            setOwner(null)
            setEditors([])
            clearChangedFields()
        }
    }

    const [searchParams, setSearchParams] = useSearchParams()
    const initialTabFromUrl = parseInt(searchParams.get('tab') || '0', 10)
    const [activeTab, setActiveTab] = useState(Math.max(0, initialTabFromUrl))

    const tabs = [
        {id: 0, label: t('proposal.tabGeneral')},
        {id: 1, label: t('proposal.tabParticipantsCost')},
        {id: 2, label: t('proposal.tabScheduling')},
        {id: 3, label: t('proposal.tabSpeakers')},
        {id: 4, label: t('proposal.tabSubmission')},
    ]

    const isStatusFinal = ['submitted', 'accepted', 'rejected'].includes(currentStatus)
    const showTerminfestlegungTab = currentStatus === 'accepted' || currentStatus === 'archived' || linkedEvents.length > 0
    if (showTerminfestlegungTab) {
        tabs.push({id: 5, label: t('proposal.tabDateArrangement')})
    }

    const handleTabChange = (tabId: number) => {
        setActiveTab(tabId)
        setHighlightedChecklistItem(null)
        const newParams = new URLSearchParams(searchParams)
        newParams.set('tab', tabId.toString())
        setSearchParams(newParams)
    }

    const handleChecklistItemClick = (item: string) => {
        const mapping = CHECKLIST_FIELD_MAP[item]
        if (!mapping) return
        setActiveTab(mapping.tabId)
        setHighlightedChecklistItem(item)
        const newParams = new URLSearchParams(searchParams)
        newParams.set('tab', mapping.tabId.toString())
        setSearchParams(newParams)
    }

    useEffect(() => {
        if (!highlightedChecklistItem) return
        const mapping = CHECKLIST_FIELD_MAP[highlightedChecklistItem]
        if (!mapping) return
        const timer = setTimeout(() => {
            const el = document.getElementById(mapping.elementId)
            if (el) el.scrollIntoView({block: 'center', behavior: 'smooth'})
        }, 50)
        return () => clearTimeout(timer)
    }, [highlightedChecklistItem])

    const handlePreviousTab = () => {
        if (activeTab > 0) {
            handleTabChange(activeTab - 1)
        }
    }

    const handleNextTab = () => {
        if (activeTab < tabs.length - 1) {
            handleTabChange(activeTab + 1)
        }
    }

    const buildProposalMermaidDefinition = (diagram: EventFlowDiagram, activeStatus: string): string => {
        const lines: string[] = ['flowchart TD']
        for (const node of diagram.nodes) {
            const label = t(`proposal.statusValues.${node}`, { defaultValue: node })
            const cls = node === activeStatus ? ':::activeState' : ''
            lines.push(`    ${node}["${label}"]${cls}`)
        }
        for (const edge of diagram.edges) {
            const label = t(`proposal.transition.${edge.label_id}`, { defaultValue: edge.label_id })
            lines.push(`    ${edge.source} -->|"${label}"| ${edge.target}`)
        }
        lines.push('    classDef activeState fill:#1565c0,stroke:#0d47a1,color:#fff,font-weight:bold')
        return lines.join('\n')
    }

    const renderProposalMermaid = async (diagram: EventFlowDiagram, activeStatus: string) => {
        if (diagramRenderingRef.current) return
        diagramRenderingRef.current = true
        try {
            const definition = buildProposalMermaidDefinition(diagram, activeStatus)
            const id = `proposal-flow-${++proposalDiagramCounter}`
            const { svg } = await mermaid.render(id, definition)
            setRenderedSvg(svg)
        } catch (err) {
            setFlowChartError(t('common.error'))
        } finally {
            diagramRenderingRef.current = false
        }
    }

    // Re-render proposal lifecycle diagram when status, language, or diagram data changes
    useEffect(() => {
        if (!flowDiagram || !flowChartOpen) return
        void renderProposalMermaid(flowDiagram, currentStatus)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentStatus, flowDiagram, flowChartOpen, i18n.language])

    const handleToggleFlowChart = async () => {
        const nextOpen = !flowChartOpen
        setFlowChartOpen(nextOpen)
        if (nextOpen && flowDiagram === null && !flowChartLoading) {
            try {
                setFlowChartLoading(true)
                setFlowChartError(null)
                const diagram = await fetchProposalFlowDiagram()
                setFlowDiagram(diagram)
            } catch (err) {
                setFlowChartError(t('common.error'))
            } finally {
                setFlowChartLoading(false)
            }
        }
    }

    return (
        <div className={styles.container}>
            <h1>{t('proposal.editorTitle')}</h1>

            {isLoading && <p aria-live="polite">{t('proposal.loadingProposal')}</p>}

            {currentStatus === 'draft' && (
                <div className={styles.draftWarning} role="alert" aria-live="assertive">
                    <h3>{t('proposal.draftWarning')}</h3>
                    <p>{t('proposal.draftWarningDesc')}</p>
                </div>
            )}
            {currentStatus === 'revise' && (
                <div className={styles.statusBannerRevise} role="alert" aria-live="assertive">
                    <h3>{t('proposal.statusRevise')}</h3>
                    <p>{t('proposal.statusReviseDesc')}</p>
                </div>
            )}
            {currentStatus === 'accepted' && (
                <div className={styles.statusBannerAccepted} role="alert" aria-live="assertive">
                    <h3>{t('proposal.statusAccepted')}</h3>
                    <p>{t('proposal.statusAcceptedDesc')}</p>
                </div>
            )}
            {currentStatus === 'rejected' && (
                <div className={styles.statusBannerRejected} role="alert" aria-live="assertive">
                    <h3>{t('proposal.statusRejected')}</h3>
                    <p>{t('proposal.statusRejectedDesc')}</p>
                </div>
            )}
            {currentStatus === 'submitted' && (
                <div className={styles.statusBannerSubmitted} role="alert" aria-live="assertive">
                    <h3>{t('proposal.statusSubmitted')}</h3>
                    <p>{t('proposal.statusSubmittedDesc')}</p>
                </div>
            )}

            <form className={styles.form} aria-label="Proposal editor">
                {/* Tab Navigation - Desktop */}
                <div className={styles.tabNavigation} role="tablist">
                    {tabs.map((tab) => {
                        const tabHasChanges = hasTabUnsavedChanges(tab.id, changedFields)
                        const tabNumber = tab.id + 1
                        return (
                            <button
                                key={tab.id}
                                type="button"
                                role="tab"
                                aria-selected={activeTab === tab.id}
                                onClick={() => handleTabChange(tab.id)}
                                className={`${styles.tabButton} ${activeTab === tab.id ? styles.tabButtonActive : ''}`}
                                disabled={isSaving}
                            >
                                <span className={styles.tabNumberContainer}>
                                    {tabHasChanges ? (
                                        <span className={styles.tabUnsavedIcon} title="Unsaved changes">
                                            ✎
                                        </span>
                                    ) : isStatusFinal && tab.id <= 3 ? (
                                        <span className={styles.tabCheckIcon} title="Completed">
                                            ✓
                                        </span>
                                    ) : isStatusFinal && tab.id === 4 ? (
                                        currentStatus === 'submitted' ? (
                                            <span className={styles.tabHourglassIcon} title="Pending review">
                                                ⧗
                                            </span>
                                        ) : currentStatus === 'rejected' ? (
                                            <span className={styles.tabCrossIcon} title="Rejected">
                                                ✗
                                            </span>
                                        ) : (
                                            <span className={styles.tabCheckIcon} title="Accepted">
                                                ✓
                                            </span>
                                        )
                                    ) : (
                                        <span className={styles.tabNumber}>{tabNumber}</span>
                                    )}
                                </span>
                                <span className={styles.tabLabel}>{tab.label}</span>
                            </button>
                        )
                    })}
                </div>

                {/* Tab Navigation - Mobile Dropdown */}
                <div className={styles.tabNavigationDropdown}>
                    <select
                        value={activeTab}
                        onChange={(e) => handleTabChange(parseInt(e.target.value, 10))}
                        disabled={isSaving}
                        aria-label={t('proposal.tabScheduling')}
                    >
                        {tabs.map((tab) => (
                            <option key={tab.id} value={tab.id}>
                                {tab.id + 1}. {tab.label}
                            </option>
                        ))}
                    </select>
                </div>

                {/* Tab Content */}
                <div className={styles.tabContent}>
                    {/* Tab 1: Allgemeines */}
                    <div className={activeTab === 0 ? styles.tabPanelActive : styles.tabPanelHidden} role="tabpanel">
                        <div className={styles.detailsContent}>
                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-title" className={styles.label}>
                                    {t('proposal.title')}
                                    {changedFields.has('title') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <input
                                    id="proposal-title"
                                    type="text"
                                    value={formData.title}
                                    onChange={(e) => handleFieldChange('title', e.target.value.slice(0, 30))}
                                    className={`${styles.input} ${changedFields.has('title') ? styles.changed : ''} ${highlightedChecklistItem === 'title' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || !canEdit}
                                    maxLength={30}
                                    required
                                />
                                <small aria-live="polite">{formData.title.length}/30</small>
                            </div>

                            <div id="proposal-image-section" className={`${styles.formGroup} ${highlightedChecklistItem === 'photo' ? styles.checklistHighlight : ''}`}>
                                <ImageUploadField
                                    label={t('proposal.proposalImage')}
                                    inputId="proposal-image-upload"
                                    previewAlt="Proposal image preview"
                                    currentImageUrl={proposalPhotoUrl}
                                    disabled={isSaving || !canEdit || !_proposalId || !_proposalId.trim()}
                                    isUploading={isProposalImageUploading}
                                    uploadProgress={proposalImageUploadProgress}
                                    error={proposalImageError}
                                    helpText={t('proposal.proposalImageHelp')}
                                    uploadEnabled={proposalImageCopyrightChecked}
                                    onFileSelected={handleProposalImageUpload}
                                >
                                    <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.9rem', cursor: 'pointer' }}>
                                        <input
                                            type="checkbox"
                                            checked={proposalImageCopyrightChecked}
                                            onChange={(e) => setProposalImageCopyrightChecked(e.target.checked)}
                                            disabled={isSaving || !canEdit}
                                        />
                                        {t('common.imageUploadCopyrightConsent')}
                                    </label>
                                </ImageUploadField>
                            </div>

                            {lookupLoading && (
                                <p className={styles.fieldHint} role="status" aria-live="polite">
                                    {t('proposal.loading')}
                                </p>
                            )}

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-call" className={styles.label}>
                                    {t('call.fieldLabel')}
                                    {changedFields.has('call_id') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <select
                                    id="proposal-call"
                                    value={formData.call_id ?? ''}
                                    onChange={(e) => handleFieldChange('call_id', e.target.value || null)}
                                    className={`${styles.input} ${changedFields.has('call_id') ? styles.changed : ''} ${highlightedChecklistItem === 'submissionDeadline' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || lookupLoading || !canEdit || (currentStatus !== '' && currentStatus !== 'draft')}
                                >
                                    <option value="">{t('call.fieldPlaceholder')}</option>
                                    {availableCalls.map((call) => (
                                        <option key={String(call.id)} value={String(call.id)}>
                                            {call.title}
                                        </option>
                                    ))}
                                </select>
                                {currentStatus !== 'draft' && currentStatus !== '' && (
                                    <small className={styles.fieldHint}>
                                        {t('call.callChangeNotAllowed')}
                                    </small>
                                )}
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-submission-type" className={styles.label}>
                                    {t('proposal.submissionType')}
                                    {changedFields.has('submission_type') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <select
                                    id="proposal-submission-type"
                                    value={formData.submission_type}
                                    onChange={(e) => handleFieldChange('submission_type', e.target.value)}
                                    className={`${styles.input} ${changedFields.has('submission_type') ? styles.changed : ''} ${highlightedChecklistItem === 'submissionType' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || lookupLoading || !canEdit}
                                >
                                    {lookupLoading ? (
                                        <option value="">{t('proposal.loading')}</option>
                                    ) : submissionTypes.length === 0 ? (
                                        <option value="">{t('proposal.noSubmissionTypes')}</option>
                                    ) : (
                                        <>
                                            <option value="">{t('proposal.selectSubmissionType')}</option>
                                            {submissionTypes.map((type) => (
                                                <option key={type.code} value={type.code}>
                                                    {type.label}
                                                </option>
                                            ))}
                                        </>
                                    )}
                                </select>
                                <small className={styles.fieldHint}>
                                    {t('proposal.submissionTypeHint')}
                                </small>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-area" className={styles.label}>
                                    {t('proposal.area')}
                                    {changedFields.has('area') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <select
                                    id="proposal-area"
                                    value={formData.area}
                                    onChange={(e) => handleFieldChange('area', e.target.value)}
                                    className={`${styles.input} ${changedFields.has('area') ? styles.changed : ''} ${highlightedChecklistItem === 'workshopArea' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || !canEdit || lookupLoading}
                                >
                                    {lookupLoading ? (
                                        <option value="">{t('proposal.loading')}</option>
                                    ) : proposalAreas.length === 0 ? (
                                        <option value="">{t('proposal.noAreas')}</option>
                                    ) : (
                                        <>
                                            <option value="">{t('proposal.selectArea')}</option>
                                            {proposalAreas.map((area) => (
                                                <option key={area.code} value={area.code}>
                                                    {area.label}
                                                </option>
                                            ))}
                                        </>
                                    )}
                                </select>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-language" className={styles.label}>
                                    {t('proposal.language')}
                                    {changedFields.has('language') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <select
                                    id="proposal-language"
                                    value={formData.language}
                                    onChange={(e) => handleFieldChange('language', e.target.value)}
                                    className={`${styles.input} ${changedFields.has('language') ? styles.changed : ''} ${highlightedChecklistItem === 'language' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || lookupLoading || !canEdit}
                                >
                                    {lookupLoading ? (
                                        <option value="">{t('proposal.loading')}</option>
                                    ) : proposalLanguages.length === 0 ? (
                                        <option value="">{t('proposal.noLanguages')}</option>
                                    ) : (
                                        <>
                                            <option value="">{t('proposal.selectLanguage')}</option>
                                            {proposalLanguages.map((lang) => (
                                                <option key={lang.code} value={lang.code}>
                                                    {lang.label}
                                                </option>
                                            ))}
                                        </>
                                    )}
                                </select>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-abstract" className={styles.label}>
                                    {t('proposal.abstract')}
                                    {changedFields.has('abstract') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <textarea
                                    id="proposal-abstract"
                                    value={formData.abstract}
                                    onChange={(e) => handleFieldChange('abstract', e.target.value)}
                                    className={`${styles.textarea} ${changedFields.has('abstract') ? styles.changed : ''} ${highlightedChecklistItem === 'abstract' ? styles.checklistHighlight : ''}`}
                                    rows={3}
                                    disabled={isSaving || !canEdit}
                                    required
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.abstractHint')}
                                </small>
                                <small aria-live="polite">{formData.abstract.length}/250</small>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-description" className={styles.label}>
                                    {t('proposal.description')}
                                    {changedFields.has('description') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <textarea
                                    id="proposal-description"
                                    value={formData.description}
                                    onChange={(e) => handleFieldChange('description', e.target.value)}
                                    className={`${styles.textarea} ${changedFields.has('description') ? styles.changed : ''} ${highlightedChecklistItem === 'description' ? styles.checklistHighlight : ''}`}
                                    rows={6}
                                    disabled={isSaving || !canEdit}
                                    required
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.descriptionHint')}
                                </small>
                                <small aria-live="polite">{formData.description.length}/1000</small>
                            </div>

                        </div>
                    </div>

                    {/* Tab 2: Teilnehmer und Kosten */}
                    <div className={activeTab === 1 ? styles.tabPanelActive : styles.tabPanelHidden} role="tabpanel">
                        <div className={styles.detailsContent}>
                            <div className={styles.formGroup}>
                                <label className={styles.label}>
                                    <input
                                        id="proposal-is-basic-course"
                                        type="checkbox"
                                        checked={formData.is_basic_course}
                                        onChange={(e) => handleFieldChange('is_basic_course', e.target.checked)}
                                        disabled={isSaving || !canEdit}
                                        aria-describedby="proposal-is-basic-course-desc"
                                    />
                                    {t('proposal.isBasicCourse')}
                                </label>

                                <small id="proposal-is-basic-course-desc" className={styles.fieldHint}
                                       style={{marginBottom: '0.75rem'}}>
                                    {t('proposal.isBasicCourseDesc')}
                                </small>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-max-participants" className={styles.label}>
                                    {t('proposal.maxParticipants')}
                                    {changedFields.has('max_participants') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <input
                                    id="proposal-max-participants"
                                    type="text"
                                    inputMode="numeric"
                                    value={maxParticipantsRaw}
                                    onChange={(e) => {
                                        const v = e.target.value.replace(/[^0-9]/g, '')
                                        setMaxParticipantsRaw(v)
                                        if (v !== '') handleFieldChange('max_participants', parseInt(v, 10))
                                    }}
                                    onBlur={() => {
                                        const n = parseInt(maxParticipantsRaw, 10)
                                        const final = (!maxParticipantsRaw || isNaN(n) || n < 0) ? 0 : n
                                        setMaxParticipantsRaw(String(final))
                                        handleFieldChange('max_participants', final)
                                    }}
                                    className={`${styles.input} ${changedFields.has('max_participants') ? styles.changed : ''} ${highlightedChecklistItem === 'maxParticipants' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || !canEdit}
                                />
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-material-cost" className={styles.label}>
                                    {t('proposal.materialCost')}
                                    {changedFields.has('material_cost_eur') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <input
                                    id="proposal-material-cost"
                                    type="text"
                                    inputMode="decimal"
                                    value={formData.material_cost_eur}
                                    onChange={(e) => {
                                        const v = e.target.value.replace(/[^0-9.]/g, '').replace(/(\..*)\./g, '$1')
                                        handleFieldChange('material_cost_eur', v)
                                    }}
                                    onBlur={() => {
                                        const n = parseFloat(formData.material_cost_eur)
                                        const final = (!formData.material_cost_eur || isNaN(n) || n < 0) ? '0.00' : n.toFixed(2)
                                        handleFieldChange('material_cost_eur', final)
                                    }}
                                    className={`${styles.input} ${changedFields.has('material_cost_eur') ? styles.changed : ''}`}
                                    disabled={isSaving || !canEdit}
                                />
                            </div>
                        </div>
                    </div>

                    {/* Tab 3: Zeitplanung */}
                    <div className={activeTab === 2 ? styles.tabPanelActive : styles.tabPanelHidden} role="tabpanel">
                        <div className={styles.detailsContent}>
                            <div id="proposal-duration-section" className={highlightedChecklistItem === 'duration' ? styles.checklistHighlight : undefined}>
                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-duration-days" className={styles.label}>
                                    {t('proposal.numberOfDays')}
                                    {changedFields.has('duration_days') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <input
                                    id="proposal-duration-days"
                                    type="text"
                                    inputMode="numeric"
                                    value={durationDaysRaw}
                                    onChange={(e) => {
                                        const v = e.target.value.replace(/[^0-9]/g, '')
                                        setDurationDaysRaw(v)
                                        if (v !== '') handleFieldChange('duration_days', parseInt(v, 10))
                                    }}
                                    onBlur={() => {
                                        const n = parseInt(durationDaysRaw, 10)
                                        const final = (!durationDaysRaw || isNaN(n) || n < 1) ? 1 : n
                                        setDurationDaysRaw(String(final))
                                        handleFieldChange('duration_days', final)
                                    }}
                                    className={`${styles.input} ${changedFields.has('duration_days') ? styles.changed : ''}`}
                                    disabled={isSaving || !canEdit}
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.numberOfDaysHint')}
                                </small>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-duration-time" className={styles.label}>
                                    {t('proposal.timePerDay')}
                                    {changedFields.has('duration_time_per_day') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <input
                                    id="proposal-duration-time"
                                    type="text"
                                    placeholder="HH:MM"
                                    value={formData.duration_time_per_day}
                                    onChange={(e) => {
                                        let value = e.target.value
                                        value = value.replace(/[^\d:]/g, '')
                                        const parts = value.split(':')
                                        if (parts.length > 2) {
                                            value = `${parts[0]}:${parts[1]}`
                                        }
                                        handleFieldChange('duration_time_per_day', value)
                                    }}
                                    className={`${styles.input} ${changedFields.has('duration_time_per_day') ? styles.changed : ''}`}
                                    disabled={isSaving || !canEdit}
                                    required
                                    maxLength={5}
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.timePerDayHint')}
                                </small>
                            </div>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-occurrence-count" className={styles.label}>
                                    {t('proposal.occurrenceCount')}
                                    {changedFields.has('occurrence_count') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <input
                                    id="proposal-occurrence-count"
                                    type="text"
                                    inputMode="numeric"
                                    value={occurrenceCountRaw}
                                    onChange={(e) => {
                                        const v = e.target.value.replace(/[^0-9]/g, '')
                                        setOccurrenceCountRaw(v)
                                        if (v !== '') handleFieldChange('occurrence_count', parseInt(v, 10))
                                    }}
                                    onBlur={() => {
                                        const n = parseInt(occurrenceCountRaw, 10)
                                        const final = (!occurrenceCountRaw || isNaN(n) || n < 0) ? 0 : n
                                        setOccurrenceCountRaw(String(final))
                                        handleFieldChange('occurrence_count', final)
                                    }}
                                    className={`${styles.input} ${changedFields.has('occurrence_count') ? styles.changed : ''} ${highlightedChecklistItem === 'occurrenceCount' ? styles.checklistHighlight : ''}`}
                                    disabled={isSaving || !canEdit}
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.occurrenceCountHint')}
                                </small>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-preferred-dates" className={styles.label}>
                                    {t('proposal.preferredDates')}
                                    {changedFields.has('preferred_dates') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <textarea
                                    id="proposal-preferred-dates"
                                    value={formData.preferred_dates}
                                    onChange={(e) => handleFieldChange('preferred_dates', e.target.value)}
                                    className={`${styles.textarea} ${changedFields.has('preferred_dates') ? styles.changed : ''} ${highlightedChecklistItem === 'preferredDates' ? styles.checklistHighlight : ''}`}
                                    rows={3}
                                    disabled={isSaving || !canEdit}
                                    required
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.preferredDatesHint')}
                                </small>
                            </div>
                        </div>
                    </div>

                    {/* Tab 4: Referent*innen */}
                    <div className={activeTab === 3 ? styles.tabPanelActive : styles.tabPanelHidden} role="tabpanel">
                        <div className={styles.detailsContent}>
                            <div className={styles.formGroup}>
                                <label className={styles.label}>
                                    <input
                                        id="proposal-has-building-access"
                                        type="checkbox"
                                        checked={formData.has_building_access}
                                        onChange={(e) => handleFieldChange('has_building_access', e.target.checked)}
                                        disabled={isSaving || !canEdit}
                                    />
                                    {t('proposal.hasBuildingAccess')}
                                </label>
                            </div>

                            <div className={styles.formGroup}>
                                <label className={styles.label} id="proposal-owner-label">{t('proposal.owner')}</label>
                                <div className={styles.ownerDisplay} aria-labelledby="proposal-owner-label">
                                    {owner ? (
                                        <span>{owner.username}</span>
                                    ) : (
                                        <span style={{fontStyle: 'italic'}}>{t('proposal.ownerWillBeSet')}</span>
                                    )}
                                </div>
                                <small className={styles.fieldHint}>
                                    {t('proposal.ownerHint')}
                                </small>
                            </div>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-editors-search" className={styles.label}>
                                    {t('proposal.editors')}
                                    {changedFields.has('editors') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <div style={{display: 'flex', flexDirection: 'column', gap: '0.5rem'}}>
                                    <div ref={participantDropdownRef} style={{position: 'relative'}}>
                                    <input
                                        id="proposal-editors-search"
                                        type="text"
                                        autoComplete="off"
                                        placeholder={t('proposal.searchEditors')}
                                        value={participantSearchQuery}
                                        onChange={(e) => {
                                            setParticipantSearchQuery(e.target.value)
                                            setParticipantDropdownOpen(true)
                                        }}
                                        onKeyDown={handleParticipantKeyDown}
                                        className={`${styles.input} ${changedFields.has('editors') ? styles.changed : ''}`}
                                        disabled={isSaving || !canEdit}
                                    />
                                    {participantDropdownOpen && participantSearchResults.filter((u) => !editors.find((p) => p.id === u.id)).length > 0 && (
                                        <ul style={{
                                            position: 'absolute',
                                            top: '100%',
                                            left: 0,
                                            right: 0,
                                            zIndex: 100,
                                            background: 'white',
                                            border: '1px solid #ccc',
                                            borderRadius: '4px',
                                            margin: 0,
                                            padding: 0,
                                            listStyle: 'none',
                                            maxHeight: '200px',
                                            overflowY: 'auto',
                                            boxShadow: '0 4px 8px rgba(0,0,0,0.15)',
                                        }}>
                                            {participantSearchResults
                                                .filter((u) => !editors.find((p) => p.id === u.id))
                                                .map((user, index) => (
                                                    <li
                                                        key={user.id}
                                                        onMouseDown={(e) => {
                                                            e.preventDefault()
                                                            handleAddEditor(user)
                                                        }}
                                                        onMouseEnter={() => setParticipantHighlightedIndex(index)}
                                                        style={{
                                                            padding: '0.5rem 0.75rem',
                                                            cursor: 'pointer',
                                                            background: index === participantHighlightedIndex ? '#e3f2fd' : 'white',
                                                        }}
                                                    >
                                                        {user.username}
                                                    </li>
                                                ))}
                                        </ul>
                                    )}
                                    </div>

                                    {editors.length > 0 && (
                                        <div style={{display: 'flex', gap: '0.5rem', flexWrap: 'wrap'}}>
                                            {editors.map((p) => (
                                                <span
                                                    key={p.id}
                                                    style={{
                                                        display: 'inline-flex',
                                                        alignItems: 'center',
                                                        gap: '0.4rem',
                                                        backgroundColor: '#e3f2fd',
                                                        color: '#1565c0',
                                                        borderRadius: '999px',
                                                        padding: '0.3rem 0.6rem',
                                                        fontSize: '0.85rem',
                                                    }}
                                                >
                          {p.username}
                                                    <button
                                                        type="button"
                                                        onClick={() => handleRemoveEditor(p.id)}
                                                        disabled={isSaving || !canEdit}
                                                        style={{
                                                            border: 'none',
                                                            background: 'transparent',
                                                            color: '#1565c0',
                                                            cursor: 'pointer',
                                                            padding: 0,
                                                            fontSize: '0.9rem',
                                                            lineHeight: 1,
                                                        }}
                                                        aria-label={t('proposal.removeEditor', {username: p.username})}
                                                    >
                            ×
                          </button>
                        </span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                                <small className={styles.fieldHint} style={{marginBottom: '0.75rem'}}>
                                    {t('proposal.editorsHint')}
                                </small>
                            </div>

                            {/* Speaker list editor */}
                            <div
                                id="proposal-speaker-list-section"
                                style={{marginTop: '1rem'}}
                                className={(highlightedChecklistItem === 'atLeastOneSpeaker' || highlightedChecklistItem === 'speakersHaveBio') ? styles.checklistHighlight : undefined}
                            >
                                <SpeakerListEditor
                                    proposalId={_proposalId ?? ''}
                                    speakers={speakers}
                                    onSpeakersChange={async (newSpeakers) => {
                                        setSpeakers(newSpeakers)
                                        // Refresh checklist and buttons when speakers change
                                        if (_proposalId && _proposalId.trim()) {
                                            try {
                                                setChecklistLoading(true)
                                                const data = await fetchProposalChecklist(_proposalId)
                                                setChecklist(data)
                                            } catch (err) {
                                                console.error('Failed to refresh checklist after speaker change:', err)
                                            } finally {
                                                setChecklistLoading(false)
                                            }
                                        }
                                        setTransitionButtonsVersion((previous) => previous + 1)
                                    }}
                                    disabled={isSaving || !_proposalId || !_proposalId.trim() || !canEdit}
                                />
                            </div>

                        </div>
                    </div>

                    {/* Tab 5: Einreichung (Checklist) */}
                    <div className={activeTab === 4 ? styles.tabPanelActive : styles.tabPanelHidden} role="tabpanel">
                        <div className={styles.detailsContent}>

                            <div className={styles.formGroup}>
                                <label htmlFor="proposal-internal-notes" className={styles.label}>
                                    {t('proposal.internalNotes')}
                                    {changedFields.has('internal_notes') &&
                                        <span className={styles.changedIndicator} aria-label="unsaved change">●</span>}
                                </label>
                                <textarea
                                    id="proposal-internal-notes"
                                    value={formData.internal_notes}
                                    onChange={(e) => handleFieldChange('internal_notes', e.target.value)}
                                    className={`${styles.textarea} ${changedFields.has('internal_notes') ? styles.changed : ''}`}
                                    rows={3}
                                    disabled={isSaving || !canEdit}
                                />
                                <small className={styles.fieldHint}>
                                    {t('proposal.internalNotesHint')}
                                </small>
                            </div>

                            {hasChanges ? (
                                <div className={styles.checklistBox}>
                                    <p className={styles.checklistMuted}>{t('proposal.saveBeforeChecking')}</p>
                                </div>
                            ) : _proposalId && _proposalId.trim() ? (
                                <div className={styles.checklistBox}>
                                    <h3>{t('proposal.submissionChecklist')}</h3>
                                    {checklistLoading ? (
                                        <p className={styles.checklistMuted}>{t('proposal.loadingChecklist')}</p>
                                    ) : Object.keys(checklist).length === 0 ? (
                                        <p className={styles.checklistMuted}>{t('proposal.saveToSeeChecklist')}</p>
                                    ) : (
                                        <ul style={{margin: 0, padding: 0, listStyle: 'none'}}>
                                            {Object.entries(checklist).map(([item, {status}]) => (
                                                <li
                                                    key={item}
                                                    style={{
                                                        display: 'flex',
                                                        alignItems: 'center',
                                                        gap: '0.5rem',
                                                        padding: '0.4rem 0',
                                                        fontSize: '0.9rem',
                                                    }}
                                                >
                        <span
                            style={{
                                display: 'inline-block',
                                width: '1.2rem',
                                fontSize: '1.2rem',
                                lineHeight: '1',
                                color: status === 'ok' ? '#4caf50' : '#ff9800',
                                fontWeight: 'bold',
                                flexShrink: 0,
                            }}
                            title={status === 'ok' ? t('proposal.complete') : t('proposal.incomplete')}
                        >
                          {status === 'ok' ? '✓' : '⚠'}
                        </span>
                                                    <button
                                                        type="button"
                                                        className={`${status === 'ok' ? styles['checklistItemText--ok'] : styles['checklistItemText--warn']} ${styles.checklistItemButton}`}
                                                        onClick={() => handleChecklistItemClick(item)}
                                                    >{t(`proposal.checklist.${item}`, {defaultValue: item})}</button>
                                                </li>
                                            ))}
                                        </ul>
                                    )}
                                </div>
                            ) : null}


                        </div>
                    </div>

                    {/* Tab 6: Terminfestlegung (only for accepted/archived proposals) */}
                    {showTerminfestlegungTab && (
                        <div className={activeTab === 5 ? styles.tabPanelActive : styles.tabPanelHidden}
                             role="tabpanel">
                            <div className={styles.detailsContent}>
                                {/* Linked Events Section */}
                                {canViewLinkedEvents && (
                                    <details
                                        className={styles.fieldset}
                                        style={{marginTop: '1.5rem'}}
                                        open={true}
                                    >
                                        <summary className={styles.legend}>
            <span className={styles.summaryContent}>
              {t('proposal.linkedEvents', {count: linkedEvents.length})}
            </span>
                                        </summary>
                                        <div className={styles.detailsContent}>
                                            {linkedEventsLoading ? (
                                                <p style={{
                                                    color: '#888',
                                                    fontSize: '0.9rem'
                                                }}>{t('proposal.loadingLinkedEvents')}</p>
                                            ) : linkedEvents.length === 0 ? (
                                                <p style={{
                                                    color: '#888',
                                                    fontSize: '0.9rem'
                                                }}>{t('proposal.noLinkedEvents')}</p>
                                            ) : (
                                                <ul style={{margin: 0, padding: 0, listStyle: 'none'}}>
                                                    {linkedEvents.map((ev) => (
                                                        <li key={ev.id} style={{
                                                            padding: '0.5rem 0',
                                                            borderBottom: '1px solid #eee'
                                                        }}>
                                                            <div style={{
                                                                display: 'flex',
                                                                alignItems: 'center',
                                                                gap: '0.5rem',
                                                                flexWrap: 'wrap'
                                                            }}>
                                                                <Link
                                                                    to={`/proposal/${_proposalId}/event/${ev.id}`}
                                                                    style={{
                                                                        color: '#1976d2',
                                                                        textDecoration: 'none',
                                                                        fontWeight: 500
                                                                    }}
                                                                >
                                                                    {ev.name}
                                                                </Link>
                                                                <EventStatusBadge status={ev.status}/>
                                                            </div>
                                                            <div style={{fontSize: '0.8rem', color: '#666'}}>
                                                                {t('proposal.series')}: {ev.series_name}
                                                            </div>
                                                            <div style={{fontSize: '0.8rem', color: '#666'}}>
                                                                {new Date(ev.startTime).toLocaleDateString('de-DE', {
                                                                    year: 'numeric',
                                                                    month: 'short',
                                                                    day: 'numeric',
                                                                    hour: '2-digit',
                                                                    minute: '2-digit',
                                                                })}
                                                                {' – '}
                                                                {new Date(ev.endTime).toLocaleDateString('de-DE', {
                                                                    year: 'numeric',
                                                                    month: 'short',
                                                                    day: 'numeric',
                                                                    hour: '2-digit',
                                                                    minute: '2-digit',
                                                                })}
                                                            </div>
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                            {canShowLinkedEventCreateControls && (
                                                <div style={{
                                                    marginTop: '1rem',
                                                    display: 'flex',
                                                    flexDirection: 'column',
                                                    gap: '0.5rem'
                                                }}>
                                                    <div>
                                                        <label htmlFor="create-event-series-filter" style={{
                                                            display: 'block',
                                                            fontSize: '0.85rem',
                                                            fontWeight: 500,
                                                            marginBottom: '0.3rem'
                                                        }}>
                                                            {t('proposal.series')}
                                                        </label>
                                                        <input
                                                            id="create-event-series-filter"
                                                            type="text"
                                                            placeholder={t('proposal.filterSeries')}
                                                            value={seriesSearchQuery}
                                                            onChange={(e) => {
                                                                setSeriesSearchQuery(e.target.value)
                                                                setSelectedSeriesId('')
                                                            }}
                                                            style={{
                                                                width: '100%',
                                                                padding: '0.5rem',
                                                                border: '1px solid #ccc',
                                                                borderRadius: '4px',
                                                                fontSize: '0.9rem',
                                                                boxSizing: 'border-box'
                                                            }}
                                                        />
                                                    </div>
                                                    <select
                                                        id="create-event-series-select"
                                                        aria-label={t('proposal.series')}
                                                        value={selectedSeriesId}
                                                        onChange={(e) => setSelectedSeriesId(e.target.value)}
                                                        style={{
                                                            width: '100%',
                                                            padding: '0.5rem',
                                                            border: '1px solid #ccc',
                                                            borderRadius: '4px',
                                                            fontSize: '0.9rem'
                                                        }}
                                                    >
                                                        {matchingSeries && (
                                                            <option value={matchingSeries.id}>{matchingSeries.name}</option>
                                                        )}
                                                        {!matchingSeries && showCreateNewSeriesOption && (
                                                            <option value={NEW_SERIES_SENTINEL}>
                                                                {formData.title} {t('proposal.createNewSeriesHint')}
                                                            </option>
                                                        )}
                                                        <option value="">{t('proposal.selectSeries')}</option>
                                                        {seriesSearchResults
                                                            .filter((s) => s.id !== matchingSeries?.id)
                                                            .map((s) => (
                                                                <option key={s.id} value={s.id}>{s.name}</option>
                                                            ))}
                                                    </select>
                                                    <button
                                                        type="button"
                                                        onClick={handleCreateEvent}
                                                        disabled={!selectedSeriesId || isCreatingEvent}
                                                        style={{
                                                            padding: '0.5rem 1.2rem',
                                                            backgroundColor: '#4caf50',
                                                            color: 'white',
                                                            border: 'none',
                                                            borderRadius: '4px',
                                                            cursor: !selectedSeriesId || isCreatingEvent ? 'not-allowed' : 'pointer',
                                                            fontWeight: 600,
                                                            fontSize: '0.9rem',
                                                            opacity: !selectedSeriesId || isCreatingEvent ? 0.6 : 1,
                                                            alignSelf: 'flex-start',
                                                        }}
                                                    >
                                                        {isCreatingEvent ? t('proposal.creating') : t('proposal.createNewEvent')}
                                                    </button>
                                                    {createEventError && (
                                                        <div style={{
                                                            color: '#d32f2f',
                                                            fontSize: '0.85rem',
                                                            marginTop: '0.3rem'
                                                        }}>{createEventError}</div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    </details>
                                )}
                            </div>
                        </div>
                    )}
                    {/* Tab Navigation Buttons - Show on all tabs except last */}
                    <div className={styles.tabNavigationButtons}>
                        {activeTab > 0 ? (
                            <button type="button" onClick={handlePreviousTab} className={styles.tabNavButton}
                                    disabled={isSaving}>
                                ← {t("common.back")}
                            </button>
                        ) : <div/>}
                        <div>
                            {activeTab < tabs.length - 1 ? (

                                <button type="button" onClick={handleNextTab}
                                        className={`${styles.tabNavButton} ${styles.tabNavButtonGreen}`}
                                        disabled={isSaving}>
                                    {t("common.next")} →
                                </button>

                            ) : <></>}
                            {/* Transition Buttons */}
                            {activeTab >= 4 && !hasChanges && _proposalId && _proposalId.trim() && (
                                <div style={{marginTop: '1.5rem'}}>
                                    <ProposalTransitionButtons
                                        key={`${_proposalId}-${transitionButtonsVersion}`}
                                        proposalId={_proposalId}
                                        onTransitionSuccess={(updatedProposal) => {
                                            applyProposalData(updatedProposal, true)
                                            if (_proposalId && _proposalId.trim()) {
                                                const loadHistory = async () => {
                                                    try {
                                                        setHistoryLoading(true)
                                                        const data = await fetchProposalHistory(_proposalId, 7)
                                                        setHistory(data.entries)
                                                    } catch (err) {
                                                        console.error('Failed to reload history:', err)
                                                    }
                                                }
                                                loadHistory()
                                                fetchProposalTransitions(_proposalId).then((data) => {
                                                    setCurrentStatus(data.current_status)
                                                }).catch(console.error)
                                            }
                                            onTransitionSuccessProp?.()
                                        }}
                                        onTransitionError={(error) => {
                                            console.error('Transition error:', error)
                                        }}
                                    />
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Proposal lifecycle diagram */}
                    {activeTab >= 4 && _proposalId && _proposalId.trim() && (
                        <div style={{marginTop: '1rem'}}>
                            <button
                                type="button"
                                onClick={() => void handleToggleFlowChart()}
                                aria-expanded={flowChartOpen}
                                style={{
                                    background: 'none',
                                    border: 'none',
                                    cursor: 'pointer',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '0.4rem',
                                    fontSize: '0.9rem',
                                    color: '#555',
                                    padding: '0.25rem 0',
                                }}
                            >
                                <span style={{
                                    display: 'inline-block',
                                    transition: 'transform 0.2s',
                                    transform: flowChartOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                                    fontSize: '0.75rem',
                                }}>▶</span>
                                {t('proposal.lifecycleDiagram')}
                            </button>
                            {flowChartOpen && (
                                <div aria-label={t('proposal.lifecycleDiagramAria')} style={{marginTop: '0.5rem'}}>
                                    {flowChartLoading && <p style={{color: '#666', fontSize: '0.9rem'}}>{t('proposal.loadingDiagram')}</p>}
                                    {flowChartError && <p style={{color: '#d32f2f', fontSize: '0.9rem'}}>{flowChartError}</p>}
                                    {renderedSvg && (
                                        // SVG is generated locally by mermaid – safe to inject
                                        <div dangerouslySetInnerHTML={{ __html: renderedSvg }} />
                                    )}
                                </div>
                            )}
                        </div>
                    )}

                    {/* Save/Cancel/Delete Buttons - Always visible */}
                    <div className={styles.buttonGroup}>
                        <button type="button" onClick={handleSave}
                                disabled={!hasChanges || isSaving || isDeleting} className={styles.saveButton}
                                aria-busy={isSaving}>
                            {isSaving ? t('proposal.saving') : t('proposal.saveProposal')}
                        </button>
                        {hasChanges && (
                            <button type="button" onClick={() => void handleCancel()}
                                    disabled={isSaving || isDeleting || !canEdit}
                                    className={styles.cancelButton}>
                                {t('common.cancel')}
                            </button>
                        )}
                        {canDelete && _proposalId && onDeleteProposal && (
                            <button type="button" onClick={() => void handleDelete()}
                                    disabled={isSaving || isDeleting} className={styles.deleteButton}>
                                {isDeleting ? t('proposal.deleting') : t('proposal.deleteProposal')}
                            </button>
                        )}
                    </div>

                    {error && <div className={styles.error} role="alert">{error}</div>}

                    {/* Reviews - always visible for non-draft proposals */}
                    {_proposalId && _proposalId.trim() && currentStatus && currentStatus !== 'draft' && (
                        <ReviewsSection
                            proposalId={_proposalId}
                            proposalStatus={currentStatus}
                            moderationComment={formData.moderation_comment || undefined}
                            moderationCommentAt={undefined}
                            canModerate={hasPermission('moderate_proposal')}
                            canDeleteReview={hasPermission('delete_review_proposal')}
                            canCreateReview={hasPermission('create_review_proposal')}
                        />
                    )}

                    {/* History Display - Always visible */}
                    {_proposalId && _proposalId.trim() && (
                        <details
                            className={styles.fieldset}
                            style={{marginTop: '2rem'}}
                        >
                            <summary className={`${styles.legend} ${styles.historySummary}`}>
            <span className={`${styles.summaryContent} ${styles.historySummary}`}>
              <span>Edit history</span>
              <span className={styles.historyBadge}>
                {historyBadge}
              </span>
            </span>
                            </summary>

                            <div className={styles.detailsContent} style={{marginTop: '0.25rem'}}>
                                {historyLoading ? (
                                    <p className={styles.historyMuted}>Loading history...</p>
                                ) : history.length === 0 ? (
                                    <p className={styles.historyMuted}>No changes recorded in the last 7
                                        days</p>
                                ) : (
                                    <ul style={{margin: 0, padding: 0, listStyle: 'none'}}>
                                        {history.map((entry, index) => (
                                            <li
                                                key={index}
                                                className={index < history.length - 1 ? styles.historyEntry : undefined}
                                                style={{
                                                    display: 'flex',
                                                    flexDirection: 'column',
                                                    gap: '0.45rem',
                                                    padding: '0.7rem 0',
                                                    fontSize: '0.9rem',
                                                }}
                                            >
                                                <div style={{
                                                    display: 'flex',
                                                    alignItems: 'center',
                                                    gap: '0.5rem'
                                                }}>
                        <span
                            style={{
                                display: 'inline-block',
                                width: '6px',
                                height: '6px',
                                borderRadius: '50%',
                                backgroundColor:
                                    entry.change_type === 'create'
                                        ? '#4caf50'
                                        : entry.change_type === 'change'
                                            ? '#2196f3'
                                            : '#ff9800',
                                flexShrink: 0,
                            }}
                        />
                                                    <span
                                                        className={styles.historyEntrySummary}>{entry.summary}</span>
                                                </div>

                                                <div className={styles.historyEntryMeta}
                                                     style={{paddingLeft: '1rem'}}>
                                                    <div>
                                                        {t('proposal.by')} <strong>{entry.changed_by}</strong>
                                                    </div>
                                                    <div>
                                                        {formatLocalIso(entry.timestamp)} ({formatRelativeTime(entry.timestamp, t)})
                                                    </div>
                                                    {entry.field_name && (
                                                        <div style={{marginTop: '0.2rem'}}>
                                                                    <span
                                                                        style={{fontStyle: 'italic'}}>{t('proposal.field')}:</span> {entry.field_name}
                                                        </div>
                                                    )}

                                                    {(entry.old_value !== undefined && entry.old_value !== null) ||
                                                    (entry.new_value !== undefined && entry.new_value !== null) ? (
                                                        <div
                                                            style={{
                                                                marginTop: '0.35rem',
                                                                display: 'grid',
                                                                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                                                                gap: '0.4rem',
                                                            }}
                                                        >
                                                            {entry.old_value !== undefined && entry.old_value !== null && (
                                                                <div style={{
                                                                    display: 'flex',
                                                                    flexDirection: 'column',
                                                                    gap: '0.2rem'
                                                                }}>
                                                                            <span
                                                                                className={styles['historyValueLabel--old']}>{t('proposal.old')}</span>
                                                                    <div
                                                                        className={styles['historyValueBox--old']}>
                                                                        {entry.old_value}
                                                                    </div>
                                                                </div>
                                                            )}
                                                            {entry.new_value !== undefined && entry.new_value !== null && (
                                                                <div style={{
                                                                    display: 'flex',
                                                                    flexDirection: 'column',
                                                                    gap: '0.2rem'
                                                                }}>
                                                                            <span
                                                                                className={styles['historyValueLabel--new']}>{t('proposal.new')}</span>
                                                                    <div
                                                                        className={styles['historyValueBox--new']}>
                                                                        {entry.new_value}
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>
                                                    ) : null}
                                                </div>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        </details>
                    )}
                </div>
            </form>
        </div>
    )
}

