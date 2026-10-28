import { useState, useEffect, useCallback, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { DataTable, type DataTableExpandedRows, type DataTableRowEvent } from 'primereact/datatable'
import { Column } from 'primereact/column'
import { MultiSelect, type MultiSelectChangeEvent } from 'primereact/multiselect'
import { MeterGroup } from 'primereact/metergroup'
import {
  fetchProposalsList,
  fetchProposalEvents,
  fetchSyncStatus,
  type ProposalListItem,
  type ProposalEventSummary,
  type EventSyncInfo,
} from './api'
import { usePermissions } from './usePermissions'
import { translateApiError } from './apiError'
import styles from './ProposalDashboard.module.css'
import reviewStyles from './ProposalReviews.module.css'

// ── display helpers ───────────────────────────────────────────────────────────

function formatDatetimeSpan(startTime: string, endTime: string): string {
  const start = new Date(startTime)
  const end = new Date(endTime)
  const datePart = start.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' })
  const startT = start.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
  const endDate = end.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' })
  if (datePart !== endDate) {
    const endT = end.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
    return `${datePart} ${startT} – ${endDate} ${endT}`
  }
  const endT = end.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
  return `${datePart}  ${startT} – ${endT}`
}

function ProposalStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const cls = `${styles.badge} ${styles[`badge--${status}`] ?? styles['badge--unknown']}`
  return <span className={cls}>{t(`proposal.statusValues.${status}`, status)}</span>
}

function EventStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const cls = `${styles.badge} ${styles[`badge--event-${status}`] ?? styles['badge--unknown']}`
  return <span className={cls}>{t(`event.statusValues.${status}`, status)}</span>
}

function SyncStatusBadge({ platform, status }: { platform: string; status: string }) {
  const clsMap: Record<string, string> = {
    'entry up-to-date': styles['syncBadge--up-to-date'],
    'entry differs': styles['syncBadge--differs'],
    'creation pending': styles['syncBadge--creation-pending'],
    'no entry exists': styles['syncBadge--no-entry'],
    'status unknown': styles['syncBadge--unknown'],
  }
  const cls = `${styles.syncBadge} ${clsMap[status] ?? styles['syncBadge--unknown']}`
  return <span className={cls}>{platform}</span>
}

function ReviewStatsBar({ stats }: { stats: ProposalListItem['review_stats'] }) {
  if (!stats || stats.total === 0) return <span style={{ color: '#9ca3af' }}>—</span>
  return (
    <div className={reviewStyles.progressBar} title={`${stats.approved}/${stats.revise}/${stats.rejected}/${stats.pending}`}>
      {stats.approved > 0 && (
        <div className={`${reviewStyles.progressSeg} ${reviewStyles.progressSegApproved}`}
             style={{ width: `${(stats.approved / stats.total) * 100}%` }} />
      )}
      {stats.revise > 0 && (
        <div className={`${reviewStyles.progressSeg} ${reviewStyles.progressSegRevise}`}
             style={{ width: `${(stats.revise / stats.total) * 100}%` }} />
      )}
      {stats.rejected > 0 && (
        <div className={`${reviewStyles.progressSeg} ${reviewStyles.progressSegRejected}`}
             style={{ width: `${(stats.rejected / stats.total) * 100}%` }} />
      )}
      {stats.pending > 0 && (
        <div className={reviewStyles.progressSeg}
             style={{ width: `${(stats.pending / stats.total) * 100}%`, background: '#e5e7eb' }} />
      )}
    </div>
  )
}

function MyReviewBadge({ status }: { status: string | null | undefined }) {
  const { t } = useTranslation()
  if (!status) return <span style={{ color: '#9ca3af' }}>—</span>
  const label = t(`reviews.status.${status}`, status)
  const cls = `${styles.badge} ${styles[`badge--review-${status}`] ?? styles['badge--unknown']}`
  return <span className={cls}>{label}</span>
}

function AgreedDatesBar({ accepted, intended }: { accepted: number; intended: number }) {
  const pct = intended > 0 ? Math.min((accepted / intended) * 100, 100) : 0
  return (
    <div className={styles.progressCell}>
      <div className={styles.progressBar}>
        <div className={styles.progressFill} style={{ width: `${pct}%` }} />
      </div>
      <span className={styles.progressLabel}>{accepted}/{intended}</span>
    </div>
  )
}

// ── filterable column header ──────────────────────────────────────────────────

function FilterableHeader({ label, options, value, onChange }: {
  label: string
  options: { label: string; value: string }[]
  value: string[]
  onChange: (vals: string[]) => void
}) {
  const isActive = value.length > 0
  return (
    <div className={styles.filterableHeader}>
      <span>{label}</span>
      <div onClick={e => e.stopPropagation()}>
        <MultiSelect
          value={value}
          options={options}
          onChange={(e: MultiSelectChangeEvent) => onChange(e.value as string[])}
          dropdownIcon={isActive ? 'pi pi-filter-fill' : 'pi pi-filter'}
          pt={{
            root: { className: styles.colFilter },
            label: { style: { display: 'none' } },
            trigger: { className: `${styles.colFilterTrigger} ${isActive ? styles.colFilterActive : ''}` },
          }}
          panelStyle={{ minWidth: '14rem' }}
        />
      </div>
    </div>
  )
}

// ── filter state ──────────────────────────────────────────────────────────────

/** Filter option value matching proposals without a value in that column. */
const EMPTY_VALUE = '__empty__'

interface FilterValues {
  callTitles: string[]
  creators: string[]
  types: string[]
  areas: string[]
  languages: string[]
  statuses: string[]
}

/** URL query parameter name for each filter; values are repeated, e.g. `?status=submitted&status=revise`. */
const FILTER_PARAMS: Record<keyof FilterValues, string> = {
  callTitles: 'call',
  creators: 'creator',
  types: 'type',
  areas: 'area',
  languages: 'language',
  statuses: 'status',
}

function filtersFromSearchParams(params: URLSearchParams): FilterValues {
  return Object.fromEntries(
    (Object.keys(FILTER_PARAMS) as (keyof FilterValues)[]).map(k => [k, params.getAll(FILTER_PARAMS[k])])
  ) as unknown as FilterValues
}

// ── column toggle config ──────────────────────────────────────────────────────

type ColKey =
  | 'call' | 'creator' | 'speakers' | 'type' | 'area' | 'language' | 'status' | 'agreedDates'
  | 'eventDatetime' | 'eventStatus' | 'eventSync'
  | 'reviews' | 'myReview'

const ALL_COLUMN_KEYS: ColKey[] = [
  'call', 'creator', 'speakers', 'type', 'area', 'language', 'status', 'agreedDates',
  'eventDatetime', 'eventStatus', 'eventSync',
  'reviews', 'myReview',
]

const DEFAULT_VISIBLE: Record<ColKey, boolean> = {
  call: true, creator: true, speakers: true, type: true, area: true, language: true, status: true, agreedDates: true,
  eventDatetime: true, eventStatus: true, eventSync: true,
  reviews: true, myReview: true,
}

// ── expanded row data ─────────────────────────────────────────────────────────

interface ExpandedData {
  events: ProposalEventSummary[]
  syncStatus: Record<string, EventSyncInfo>
  loading: boolean
}

// ── status meter colors (match badge hues) ────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  draft:     '#9ca3af',
  submitted: '#3b82f6',
  revise:    '#f59e0b',
  accepted:  '#22c55e',
  rejected:  '#ef4444',
}

const STATUS_ORDER = ['draft', 'submitted', 'revise', 'accepted', 'rejected']

// ── main component ────────────────────────────────────────────────────────────

export function ProposalDashboard() {
  const { t } = useTranslation()
  const { canBrowse, loading: permissionsLoading } = usePermissions()

  const [proposals, setProposals] = useState<ProposalListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [expandedRows, setExpandedRows] = useState<DataTableExpandedRows>({})
  const [expandedData, setExpandedData] = useState<Record<string, ExpandedData>>({})
  const [visibleColumns, setVisibleColumns] = useState<Record<ColKey, boolean>>(DEFAULT_VISIBLE)
  const [searchParams, setSearchParams] = useSearchParams()
  const filterValues = useMemo(() => filtersFromSearchParams(searchParams), [searchParams])

  useEffect(() => {
    fetchProposalsList()
      .then(setProposals)
      .catch((e: unknown) => setError(translateApiError(e instanceof Error ? e.message : undefined)))
      .finally(() => setLoading(false))
  }, [])

  // ── filter options & filtered rows ────────────────────────────────────────

  const callOptions = useMemo(() =>
    [...new Set(proposals.map(p => p.call_title).filter((v): v is string => !!v))].sort()
      .map(v => ({ label: v, value: v })), [proposals])

  const creatorOptions = useMemo(() =>
    [...new Set(proposals.map(p => p.owner?.username).filter((v): v is string => !!v))].sort()
      .map(v => ({ label: v, value: v })), [proposals])

  const typeOptions = useMemo(() =>
    [...new Set(proposals.map(p => p.submission_type).filter((v): v is string => !!v))].sort()
      .map(v => ({ label: v, value: v })), [proposals])

  const areaOptions = useMemo(() => [
    { label: t('proposalDashboard.filterEmpty'), value: EMPTY_VALUE },
    ...[...new Set(proposals.map(p => p.area).filter((v): v is string => !!v))].sort()
      .map(v => ({ label: v, value: v })),
  ], [proposals, t])

  const languageOptions = useMemo(() =>
    [...new Set(proposals.map(p => p.language).filter((v): v is string => !!v))].sort()
      .map(v => ({ label: v, value: v })), [proposals])

  const statusOptions = useMemo(() => [
    { label: t('proposal.statusValues.draft'),     value: 'draft' },
    { label: t('proposal.statusValues.submitted'), value: 'submitted' },
    { label: t('proposal.statusValues.revise'),    value: 'revise' },
    { label: t('proposal.statusValues.accepted'),  value: 'accepted' },
    { label: t('proposal.statusValues.rejected'),  value: 'rejected' },
  ], [t])

  const filteredProposals = useMemo(() => proposals.filter(p => {
    if (filterValues.callTitles.length > 0 && !filterValues.callTitles.includes(p.call_title ?? '')) return false
    if (filterValues.creators.length > 0 && !filterValues.creators.includes(p.owner?.username ?? '')) return false
    if (filterValues.types.length > 0 && !filterValues.types.includes(p.submission_type ?? '')) return false
    if (filterValues.areas.length > 0 && !filterValues.areas.includes(p.area || EMPTY_VALUE)) return false
    if (filterValues.languages.length > 0 && !filterValues.languages.includes(p.language ?? '')) return false
    if (filterValues.statuses.length > 0 && !filterValues.statuses.includes(p.status)) return false
    return true
  }), [proposals, filterValues])

  const statusMeterItems = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const p of filteredProposals) {
      counts[p.status] = (counts[p.status] ?? 0) + 1
    }
    return STATUS_ORDER.map(s => ({
      label: `${t(`proposal.statusValues.${s}`, s)} (${counts[s] ?? 0})`,
      value: counts[s] ?? 0,
      color: STATUS_COLORS[s] ?? '#9ca3af',
    }))
  }, [filteredProposals, t])

  // ── filter onChange handlers (stable references) ──────────────────────────

  const setFilter = useCallback((key: keyof FilterValues, values: string[]) => {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.delete(FILTER_PARAMS[key])
      values.forEach(v => next.append(FILTER_PARAMS[key], v))
      return next
    }, { replace: true })
  }, [setSearchParams])

  const setCallFilter     = useCallback((v: string[]) => setFilter('callTitles', v), [setFilter])
  const setCreatorFilter  = useCallback((v: string[]) => setFilter('creators', v), [setFilter])
  const setTypeFilter     = useCallback((v: string[]) => setFilter('types', v), [setFilter])
  const setAreaFilter     = useCallback((v: string[]) => setFilter('areas', v), [setFilter])
  const setLanguageFilter = useCallback((v: string[]) => setFilter('languages', v), [setFilter])
  const setStatusFilter   = useCallback((v: string[]) => setFilter('statuses', v), [setFilter])

  // ── row expansion ─────────────────────────────────────────────────────────

  const loadExpandedData = useCallback(async (proposal: ProposalListItem) => {
    if (expandedData[proposal.id]) return

    setExpandedData(prev => ({ ...prev, [proposal.id]: { events: [], syncStatus: {}, loading: true } }))
    try {
      const events = await fetchProposalEvents(proposal.id)
      const syncStatus: Record<string, EventSyncInfo> = {}
      await Promise.all(
        events.map(async ev => {
          try { syncStatus[ev.id] = await fetchSyncStatus(ev.series_id, ev.id) } catch { /* optional */ }
        })
      )
      setExpandedData(prev => ({ ...prev, [proposal.id]: { events, syncStatus, loading: false } }))
    } catch {
      setExpandedData(prev => ({ ...prev, [proposal.id]: { events: [], syncStatus: {}, loading: false } }))
    }
  }, [expandedData])

  const onRowExpand = useCallback((e: DataTableRowEvent) => {
    void loadExpandedData(e.data as ProposalListItem)
  }, [loadExpandedData])

  const allExpanded = filteredProposals.length > 0 && filteredProposals.every(p => expandedRows[p.id])

  const toggleExpandAll = useCallback(() => {
    if (allExpanded) {
      setExpandedRows({})
      return
    }
    const next: DataTableExpandedRows = {}
    for (const p of filteredProposals) {
      next[p.id] = true
      void loadExpandedData(p)
    }
    setExpandedRows(next)
  }, [allExpanded, filteredProposals, loadExpandedData])

  const rowExpansionTemplate = useCallback((data: ProposalListItem) => {
    const loaded = expandedData[data.id]
    if (!loaded || loaded.loading) {
      return <div className={styles.expandedSection}>{t('proposalDashboard.loadingEvents')}</div>
    }
    if (loaded.events.length === 0) {
      return <div className={styles.expandedSection}><span className={styles.noEvents}>{t('proposalDashboard.noEvents')}</span></div>
    }
    return (
      <div className={styles.expandedSection}>
        <DataTable value={loaded.events} size="small" showGridlines={false}>
          {visibleColumns.eventDatetime && (
            <Column
              header={t('proposalDashboard.columns.eventDatetime')}
              body={(ev: ProposalEventSummary) => (
                <Link to={`/proposal/${data.id}/event/${ev.id}`} className={styles.tableLink}>
                  {formatDatetimeSpan(ev.startTime, ev.endTime)}
                </Link>
              )}
            />
          )}
          {visibleColumns.eventStatus && (
            <Column
              header={t('proposalDashboard.columns.eventStatus')}
              body={(ev: ProposalEventSummary) => <EventStatusBadge status={ev.status} />}
            />
          )}
          {visibleColumns.eventSync && (
            <Column
              header={t('proposalDashboard.columns.eventSync')}
              body={(ev: ProposalEventSummary) => {
                const info = loaded.syncStatus[ev.id]
                if (!info || info.sync_statuses.length === 0) return null
                return (
                  <div className={styles.syncBadges}>
                    {info.sync_statuses.map(s => (
                      <SyncStatusBadge key={s.target_id} platform={s.platform} status={s.status} />
                    ))}
                  </div>
                )
              }}
            />
          )}
        </DataTable>
      </div>
    )
  }, [expandedData, visibleColumns, t])

  // ── column toggle ─────────────────────────────────────────────────────────

  const columnToggleOptions = [
    {
      label: t('proposalDashboard.proposalColumns'),
      items: [
        { key: 'call',        label: t('proposalDashboard.columns.call') },
        { key: 'creator',     label: t('proposalDashboard.columns.creator') },
        { key: 'speakers',    label: t('proposalDashboard.columns.speakers') },
        { key: 'type',        label: t('proposalDashboard.columns.type') },
        { key: 'area',        label: t('proposalDashboard.columns.area') },
        { key: 'language',    label: t('proposalDashboard.columns.language') },
        { key: 'status',      label: t('proposalDashboard.columns.status') },
        { key: 'agreedDates', label: t('proposalDashboard.columns.agreedDates') },
        { key: 'reviews',     label: t('proposalDashboard.columns.reviews') },
        { key: 'myReview',    label: t('proposalDashboard.columns.myReview') },
      ],
    },
    {
      label: t('proposalDashboard.eventColumns'),
      items: [
        { key: 'eventDatetime', label: t('proposalDashboard.columns.eventDatetime') },
        { key: 'eventStatus',   label: t('proposalDashboard.columns.eventStatus') },
        { key: 'eventSync',     label: t('proposalDashboard.columns.eventSync') },
      ],
    },
  ]

  const selectedColumnKeys = ALL_COLUMN_KEYS.filter(k => visibleColumns[k])

  const onColumnToggle = (e: MultiSelectChangeEvent) => {
    const selected = e.value as ColKey[]
    const next = { ...visibleColumns }
    ALL_COLUMN_KEYS.forEach(k => { next[k] = selected.includes(k) })
    setVisibleColumns(next)
  }

  const tableHeader = (
    <div className={styles.tableHeader}>
      <span className={styles.tableTitle}>{t('proposalDashboard.title')}</span>
      <MultiSelect
        value={selectedColumnKeys}
        options={columnToggleOptions}
        optionGroupLabel="label"
        optionGroupChildren="items"
        optionLabel="label"
        optionValue="key"
        onChange={onColumnToggle}
        placeholder={t('proposalDashboard.toggleColumns')}
        display="chip"
        style={{ maxWidth: '32rem' }}
      />
      <a
        href="/api/v1/export/excel"
        download="export.xlsx"
        className={styles.downloadButton}
      >
        <i className="pi pi-download" style={{ marginRight: '0.4rem' }} />
        {t('proposalDashboard.downloadExcel', 'Excel')}
      </a>
      <a
        href="/api/v1/export/images"
        download="proposal_images.zip"
        className={styles.downloadButton}
      >
        <i className="pi pi-images" style={{ marginRight: '0.4rem' }} />
        {t('proposalDashboard.downloadImages', 'Images')}
      </a>
    </div>
  )

  // ── guards ────────────────────────────────────────────────────────────────

  if (permissionsLoading) return null

  if (!canBrowse('proposal')) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#666' }}>
        <p style={{ fontSize: '1.2rem', marginBottom: '1rem' }}>{t('common.accessDenied')}</p>
        <p>{t('common.noPermissionBrowseProposal')}</p>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#b91c1c' }}>
        <p>{t('common.error')}: {error}</p>
      </div>
    )
  }

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className={styles.container}>
      <div className={styles.statusMeter}>
        <MeterGroup
          values={statusMeterItems}
          max={Math.max(filteredProposals.length, 1)}
        />
      </div>
      <DataTable
        value={filteredProposals}
        loading={loading}
        expandedRows={expandedRows}
        onRowToggle={e => setExpandedRows(e.data as DataTableExpandedRows)}
        onRowExpand={onRowExpand}
        rowExpansionTemplate={rowExpansionTemplate}
        header={tableHeader}
        sortMode="multiple"
        removableSort
        size="small"
        showGridlines
        dataKey="id"
      >
        <Column
          expander
          style={{ width: '3rem' }}
          header={
            <button
              type="button"
              className={styles.expandAllButton}
              onClick={toggleExpandAll}
              disabled={filteredProposals.length === 0}
              title={allExpanded ? t('proposalDashboard.collapseAll') : t('proposalDashboard.expandAll')}
              aria-label={allExpanded ? t('proposalDashboard.collapseAll') : t('proposalDashboard.expandAll')}
            >
              <i className={allExpanded ? 'pi pi-angle-double-up' : 'pi pi-angle-double-down'} />
            </button>
          }
        />
        <Column
          field="title"
          header={t('proposalDashboard.columns.title')}
          sortable
          body={(p: ProposalListItem) => (
            <Link to={`/proposal-editor/${p.id}`} className={styles.tableLink}>{p.title}</Link>
          )}
        />
        {visibleColumns.call && (
          <Column
            field="call_title"
            header={
              <FilterableHeader
                label={t('proposalDashboard.columns.call')}
                options={callOptions}
                value={filterValues.callTitles}
                onChange={setCallFilter}
              />
            }
            sortable
            body={(p: ProposalListItem) => p.call_title ?? '—'}
          />
        )}
        {visibleColumns.creator && (
          <Column
            sortField="owner.username"
            header={
              <FilterableHeader
                label={t('proposalDashboard.columns.creator')}
                options={creatorOptions}
                value={filterValues.creators}
                onChange={setCreatorFilter}
              />
            }
            sortable
            body={(p: ProposalListItem) => p.owner?.username ?? '—'}
          />
        )}
        {visibleColumns.speakers && (
          <Column
            header={t('proposalDashboard.columns.speakers')}
            body={(p: ProposalListItem) => (
              <div className={styles.speakerList}>
                {p.speakers.length > 0 ? p.speakers.map((s, i) => <div key={i}>{s}</div>) : '—'}
              </div>
            )}
          />
        )}
        {visibleColumns.type && (
          <Column
            field="submission_type"
            header={
              <FilterableHeader
                label={t('proposalDashboard.columns.type')}
                options={typeOptions}
                value={filterValues.types}
                onChange={setTypeFilter}
              />
            }
            sortable
            body={(p: ProposalListItem) => p.submission_type ?? '—'}
          />
        )}
        {visibleColumns.area && (
          <Column
            field="area"
            header={
              <FilterableHeader
                label={t('proposalDashboard.columns.area')}
                options={areaOptions}
                value={filterValues.areas}
                onChange={setAreaFilter}
              />
            }
            sortable
            body={(p: ProposalListItem) => p.area ?? '—'}
          />
        )}
        {visibleColumns.language && (
          <Column
            field="language"
            header={
              <FilterableHeader
                label={t('proposalDashboard.columns.language')}
                options={languageOptions}
                value={filterValues.languages}
                onChange={setLanguageFilter}
              />
            }
            sortable
            body={(p: ProposalListItem) => p.language ?? '—'}
          />
        )}
        {visibleColumns.status && (
          <Column
            field="status"
            header={
              <FilterableHeader
                label={t('proposalDashboard.columns.status')}
                options={statusOptions}
                value={filterValues.statuses}
                onChange={setStatusFilter}
              />
            }
            sortable
            body={(p: ProposalListItem) => <ProposalStatusBadge status={p.status} />}
          />
        )}
        {visibleColumns.agreedDates && (
          <Column
            sortField="accepted_event_count"
            header={t('proposalDashboard.columns.agreedDates')}
            sortable
            style={{ minWidth: '10rem' }}
            body={(p: ProposalListItem) => (
              <AgreedDatesBar accepted={p.accepted_event_count} intended={p.occurrence_count} />
            )}
          />
        )}
        {visibleColumns.reviews && (
          <Column
            header={t('proposalDashboard.columns.reviews')}
            style={{ minWidth: '8rem' }}
            body={(p: ProposalListItem) => <ReviewStatsBar stats={p.review_stats} />}
          />
        )}
        {visibleColumns.myReview && (
          <Column
            header={t('proposalDashboard.columns.myReview')}
            body={(p: ProposalListItem) => <MyReviewBadge status={p.my_review_status} />}
          />
        )}
      </DataTable>
    </div>
  )
}
