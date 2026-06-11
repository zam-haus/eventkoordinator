import createClient from 'openapi-fetch'
import type { paths } from './schema'
import { apiFetch, notifySessionExpired, SessionExpiredError } from './sessionExpiry'

const CSRF_COOKIE_NAME = 'csrftoken'
const CSRF_HEADER_NAME = 'X-CSRFToken'

let csrfToken: string | null = null

// Helper to extract CSRF token from cookies
function getCsrfTokenFromCookie(): string | null {
  if (typeof document === 'undefined') return null

  const value = `; ${document.cookie}`
  const parts = value.split(`; ${CSRF_COOKIE_NAME}=`)
  if (parts.length === 2) {
    return parts.pop()?.split(';').shift() || null
  }
  return null
}

// Fetch and cache CSRF token
export async function initializeCsrfToken(): Promise<string> {
  try {
    const response = await apiFetch('/api/v1/csrf', {
      method: 'GET',
      credentials: 'include',
    })

    if (response.ok) {
      csrfToken = getCsrfTokenFromCookie()
      if (!csrfToken) {
        console.warn('CSRF token cookie not found after CSRF endpoint call')
      }
    }
  } catch (error) {
    console.error('Failed to initialize CSRF token:', error)
  }

  return csrfToken || ''
}

// Get the current CSRF token (or fetch it if not yet initialized)
export async function getCsrfToken(): Promise<string> {
  // Django can rotate CSRF (for example on login), so prefer current cookie.
  const tokenFromCookie = getCsrfTokenFromCookie()
  if (tokenFromCookie) {
    csrfToken = tokenFromCookie
  } else if (!csrfToken) {
    csrfToken = await initializeCsrfToken()
  }
  return csrfToken || ''
}

function parseUploadError(responseText: string, fallbackMessage: string): Error {
  try {
    const payload = responseText ? JSON.parse(responseText) : null
    const errorMessage = payload?.detail ? `${payload.code}: ${payload.detail}` : payload?.code
    return new Error(errorMessage || fallbackMessage)
  } catch {
    return new Error(fallbackMessage)
  }
}

async function uploadMultipartJson<T>(
  url: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<T> {
  const token = await getCsrfToken()

  return await new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)
    xhr.withCredentials = true

    if (token) {
      xhr.setRequestHeader(CSRF_HEADER_NAME, token)
    }

    xhr.upload.onprogress = (event) => {
      if (!onProgress) {
        return
      }

      if (!event.lengthComputable || event.total === 0) {
        onProgress(0)
        return
      }

      onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
    }

    xhr.onerror = () => {
      reject(new Error('Upload failed because of a network error.'))
    }

    xhr.onload = () => {
      // XHR cannot suppress redirects, so an expired session is only detectable
      // best-effort: if a redirect happens to land back on a same-origin /oidc/
      // page we treat it as expired. A redirect all the way to the cross-origin
      // identity provider is blocked by CORS and surfaces via onerror instead.
      if (xhr.responseURL && xhr.responseURL.includes('/oidc/')) {
        notifySessionExpired()
        reject(new SessionExpiredError())
        return
      }

      const fallbackMessage = `Upload failed: ${xhr.status} ${xhr.statusText}`.trim()
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100)

        try {
          resolve(JSON.parse(xhr.responseText) as T)
        } catch {
          reject(new Error('Upload succeeded but the response could not be read.'))
        }
        return
      }

      reject(parseUploadError(xhr.responseText, fallbackMessage))
    }

    const formData = new FormData()
    formData.append('file', file)
    onProgress?.(0)
    xhr.send(formData)
  })
}

const client = createClient<paths>({
  baseUrl: '',
  fetch: async (request: Request) => {
    // Ensure CSRF token is initialized
    const token = await getCsrfToken()

    // Add CSRF token to POST, PUT, PATCH, DELETE requests
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method)) {
      // Clone request to modify headers
      const headers = new Headers(request.headers)
      if (token) {
        headers.set(CSRF_HEADER_NAME, token)
      }

      const modifiedRequest = new Request(request, {
        headers,
        credentials: 'include',
      })

      return apiFetch(modifiedRequest)
    }

    // For GET and other safe methods, just ensure credentials
    const safeRequest = new Request(request, {
      credentials: 'include',
    })

    return apiFetch(safeRequest)
  },
})

// Authentication Types
export interface User {
  username: string
  user_id: string
}

export interface UserBasic {
  id: string
  username: string
}

export interface AuthError {
  code: string
}

export interface UserPermissions {
  is_authenticated: boolean
  is_staff: boolean
  is_superuser: boolean
  is_active: boolean
  permissions: string[]
}

export interface PermissionCheckRequest {
  app: string
  action: 'view' | 'add' | 'change' | 'delete' | 'browse'
  object_type: string
  object_id?: string
}

interface ApiEvent {
  id: string
  name: string
  startTime: string
  endTime: string
  tag?: string
  useFullDays?: boolean
  proposal_id?: string | null
  series_id?: string | null
  series_name?: string | null
  status?: string
}

interface ApiSeries {
  id: string
  name: string
  description?: string
  events: ApiEvent[]
}

interface ApiSeriesListItem {
  id: string
  name: string
  description?: string
}

interface ApiCreateEventResponse {
  series_id: string
  event: ApiEvent
}

type SeriesApiResponse = paths['/api/v1/series']['get']['responses'][200] extends {
  content: { 'application/json': infer T }
}
  ? T
  : ApiSeriesListItem[]

// Frontend types (converted to Date objects)
export interface Event {
  id: string
  name: string
  startTime: Date
  endTime: Date
  tag?: string
  useFullDays?: boolean
  proposal_id?: string | null
  series_id?: string | null
  series_name?: string | null
  status?: string
  [key: string]: unknown
}

export interface Series {
  id: string
  name: string
  description?: string
  events: Event[]
}

function toFrontendSeries(data: ApiSeries[]): Series[] {
  return data.map((series) => ({
    ...series,
    events: series.events.map((event) => ({
      ...event,
      startTime: new Date(event.startTime),
      endTime: new Date(event.endTime),
    })),
  }))
}

function toFrontendSeriesItem(series: ApiSeries): Series {
  return toFrontendSeries([series])[0]
}

function toFrontendEvent(event: ApiEvent): Event {
  return {
    ...event,
    startTime: new Date(event.startTime),
    endTime: new Date(event.endTime),
  }
}

async function fetchCalendarData(): Promise<Series[]> {
  const { data, error, response } = await client.GET('/api/v1/series')

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }

  const apiData = (data ?? []) as SeriesApiResponse
  return (apiData as ApiSeriesListItem[]).map((series) => ({
    id: series.id,
    name: series.name,
    description: series.description,
    events: [],
  }))
}

export async function fetchSeriesById(seriesId: string): Promise<Series> {
  const { data, error, response } = await client.GET('/api/v1/series/{series_id}', {
    params: {
      path: {
        series_id: seriesId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return toFrontendSeriesItem(data as unknown as ApiSeries)
}

export async function loadSeriesFromAPI(): Promise<Series[]> {
  try {
    return await fetchCalendarData()
  } catch (error) {
    console.error('Error loading series from API:', error)
    throw error
  }
}

// Sync Status Types (fallback DTOs since schema has content?: never)
export interface SyncStatus {
  target_id: string
  platform: string
  status: 'no entry exists' | 'creation pending' | 'status unknown' | 'entry up-to-date' | 'entry differs'
  last_synced?: string
  last_error?: string
  can_push: boolean
  can_delete: boolean
  item_url?: string
}

export interface EventSyncInfo {
  series_id: string
  event_id: string
  sync_statuses: SyncStatus[]
}

export interface SyncPushResult {
  success: boolean
  message: string
  timestamp: string
  target_id: string
  series_id: string
  event_id: string
}

export interface PropertyDiff {
  property_name: string
  local_value: string
  remote_value: string
  file_type: string
}

export interface SyncDiffData {
  series_id: string
  event_id: string
  target_id: string
  properties: PropertyDiff[]
}

export interface SyncTarget {
  id: string
  type: string
  public_properties: Record<string, string>
}

export interface CreateSyncItemResult {
  id: string
  target_id: string
  event_id: string
}

export interface ProposalSummary {
  id: string
  title: string
  submission_type: string
  call_id?: string | null
}

export async function fetchSyncTargets(): Promise<SyncTarget[]> {
  const { data, error, response } = await client.GET('/api/v1/sync/targets')

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as SyncTarget[]
}

export async function createSyncItem(
  targetId: string,
  seriesId: string,
  eventId: string
): Promise<CreateSyncItemResult> {
  const { data, error, response } = await client.POST(
    '/api/v1/sync/create/{series_id}/{event_id}/{target_id}',
    {
      params: {
        path: {
          series_id: seriesId,
          event_id: eventId,
          target_id: targetId,
        },
      },
    }
  )

  if (error || !response.ok) {
    throw new Error(error?.code || 'sync.failed')
  }

  return data as unknown as CreateSyncItemResult
}

export async function fetchSyncStatus(
  seriesId: string,
  eventId: string
): Promise<EventSyncInfo> {
  const { data, error, response } = await client.GET(
    '/api/v1/sync/status/{series_id}/{event_id}',
    {
      params: {
        path: {
          series_id: seriesId,
          event_id: eventId,
        },
      },
    }
  )

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }

  // Cast since schema has content?: never but backend returns JSON
  return data as unknown as EventSyncInfo
}

export async function pushToPlatform(
  seriesId: string,
  eventId: string,
  targetId: string
): Promise<SyncPushResult> {
  const { data, error, response } = await client.POST(
    '/api/v1/sync/push/{series_id}/{event_id}/{target_id}',
    {
      params: {
        path: {
          series_id: seriesId,
          event_id: eventId,
          target_id: targetId,
        },
      },
    }
  )

  if (error || !response.ok) {
    throw new Error(error?.code || 'sync.failed')
  }

  // Cast since schema has content?: never but backend returns JSON
  return data as unknown as SyncPushResult
}

export async function deleteRemoteSyncItem(
  seriesId: string,
  eventId: string,
  targetId: string
): Promise<void> {
  const { error, response } = await client.DELETE(
    '/api/v1/sync/delete/{series_id}/{event_id}/{target_id}',
    {
      params: {
        path: {
          series_id: seriesId,
          event_id: eventId,
          target_id: targetId,
        },
      },
    }
  )

  if (error || !response.ok) {
    throw new Error(error?.code || 'sync.failed')
  }
}

export async function fetchSyncDiff(
  seriesId: string,
  eventId: string,
  targetId: string
): Promise<SyncDiffData> {
  const { data, error, response } = await client.GET(
    '/api/v1/sync/diff/{series_id}/{event_id}/{target_id}',
    {
      params: {
        path: {
          series_id: seriesId,
          event_id: eventId,
          target_id: targetId,
        },
      },
    }
  )

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }

  // Cast since schema has content?: never but backend returns JSON
  return data as unknown as SyncDiffData
}

// Authentication API functions
export async function login(username: string, password: string): Promise<User> {
  const { data, error, response } = await client.POST('/api/v1/authenticate', {
    body: {
      username,
      password,
    },
  })

  if (error || !response.ok) {
    const errorData = data as unknown as AuthError
    throw new Error(errorData?.code || 'auth.loginFailed')
  }

  // Ensure subsequent state-changing requests use the post-login CSRF token.
  csrfToken = getCsrfTokenFromCookie() || (await initializeCsrfToken())

  return data as unknown as User
}

export async function getCurrentUser(): Promise<User> {
  const { data, error, response } = await client.GET('/api/v1/me')

  if (error || !response.ok) {
    if (response.status === 401) {
      return null as unknown as User
    }

    throw new Error(error?.code || 'auth.notAuthenticated')
  }

  return data as unknown as User
}

export async function getUserPermissions(): Promise<UserPermissions> {
  const { data, error, response } = await client.GET('/api/v1/user/permissions')

  if (error || !response.ok) {
    if (response.status === 401) {
      return {
        is_authenticated: false,
        is_staff: false,
        is_superuser: false,
        is_active: false,
        permissions: [],
      }
    }

    throw new Error(error?.code || 'auth.permissionDenied')
  }

  return data as unknown as UserPermissions
}

export async function checkObjectPermission(request: PermissionCheckRequest): Promise<boolean> {
  const { error, response } = await client.POST('/api/v1/user/permission', {
    body: {
      app: request.app,
      action: request.action,
      object_type: request.object_type,
      object_id: request.object_id,
    },
  })

  if (response?.ok) {
    return true
  }

  if (response?.status === 401 || response?.status === 403 || response?.status === 400) {
    return false
  }

  if (error) {
    console.error('Permission check failed:', error)
  }
  return false
}

export async function logout(): Promise<void> {
  const { response } = await client.POST('/api/v1/logout', {})

  if (!response.ok) {
    throw new Error('common.internalError')
  }
}

// Series and Event API functions
export interface CreateSeriesRequest {
  name?: string
  description?: string
}

export async function createSeries(request: CreateSeriesRequest = {}): Promise<Series> {
  const { data, error, response } = await client.POST('/api/v1/series/create', {
    body: request,
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return toFrontendSeriesItem(data as unknown as ApiSeries)
}

export interface CreateEventRequest {
  seriesId: string
  name?: string
  startTime?: string
  endTime?: string
  tag?: string
  proposal_id?: string
}

export async function createEvent(request: CreateEventRequest): Promise<Event> {
  const { data, error, response } = await client.POST('/api/v1/series/{series_id}/events/create', {
    params: {
      path: {
        series_id: request.seriesId,
      },
    },
    body: {
      name: request.name,
      startTime: request.startTime,
      endTime: request.endTime,
      tag: request.tag,
      proposal_id: request.proposal_id,
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  const payload = data as unknown as ApiCreateEventResponse
  return toFrontendEvent(payload.event)
}

export interface UpdateSeriesRequest {
  seriesId: string
  name?: string
  description?: string
}

export async function updateSeries(request: UpdateSeriesRequest): Promise<Series> {
  const { data, error, response } = await client.PUT('/api/v1/series/{series_id}', {
    params: {
      path: {
        series_id: request.seriesId,
      },
    },
    body: {
      name: request.name,
      description: request.description,
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'series.validationError')
  }

  return toFrontendSeriesItem(data as unknown as ApiSeries)
}

export async function deleteSeries(seriesId: string): Promise<void> {
  const { error, response } = await client.DELETE('/api/v1/series/{series_id}', {
    params: {
      path: {
        series_id: seriesId,
      },
    },
  })

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }
}

export interface UpdateEventRequest {
  seriesId: string
  eventId: string
  name?: string
  startTime?: string
  endTime?: string
  tag?: string | null
  useFullDays?: boolean
  proposal_id?: string | null
  series_id?: string | null
}

export async function updateEvent(request: UpdateEventRequest): Promise<Event> {
  const { data, error, response } = await client.PUT('/api/v1/series/{series_id}/events/{event_id}', {
    params: {
      path: {
        series_id: request.seriesId,
        event_id: request.eventId,
      },
    },
    body: {
      name: request.name,
      startTime: request.startTime,
      endTime: request.endTime,
      tag: request.tag,
      useFullDays: request.useFullDays,
      proposal_id: request.proposal_id,
      series_id: request.series_id,
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return toFrontendEvent(data as unknown as ApiEvent)
}

export async function deleteEvent(seriesId: string, eventId: string): Promise<void> {
  const { error, response } = await client.DELETE('/api/v1/series/{series_id}/events/{event_id}', {
    params: {
      path: {
        series_id: seriesId,
        event_id: eventId,
      },
    },
  })

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }
}

export async function searchUsers(query: string = ''): Promise<UserBasic[]> {
  const { data, error, response } = await client.GET('/api/v1/users/search', {
    params: {
      query: {
        q: query,
      },
    },
  })

  if (error || !response.ok) {
    console.error('Failed to search users:', response.statusText)
    return []
  }

  return (data as unknown as UserBasic[]) || []
}

export async function searchProposals(query: string = ''): Promise<ProposalSummary[]> {
  const { data, error, response } = await client.GET('/api/v1/proposals/search', {
    params: {
      query: {
        q: query,
      },
    },
  })

  if (error || !response.ok) {
    console.error('Failed to search proposals:', response.statusText)
    return []
  }

  return (data as unknown as ProposalSummary[]) || []
}

export async function searchSeriesAutocomplete(query: string = ''): Promise<Series[]> {
  const { data, error, response } = await client.GET('/api/v1/series/search', {
    params: {
      query: {
        q: query,
      },
    },
  })

  if (error || !response.ok) {
    console.error('Failed to search series:', response.statusText)
    return []
  }

  return toFrontendSeries((data as unknown as ApiSeries[]) || [])
}

export async function searchEventsAutocomplete(
  query: string = '',
  seriesId?: string
): Promise<Event[]> {
  const { data, error, response } = await client.GET('/api/v1/events/search', {
    params: {
      query: {
        q: query,
        series_id: seriesId,
      },
    },
  })

  if (error || !response.ok) {
    console.error('Failed to search events:', response.statusText)
    return []
  }

  return ((data as unknown as ApiEvent[]) || []).map(toFrontendEvent)
}

export async function fetchProposalChecklist(proposalId: string): Promise<Record<string, ProposalChecklistItem>> {
  const { data, error, response } = await client.GET('/api/v1/proposals/{proposal_id}/checklist', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as Record<string, ProposalChecklistItem>
}

export async function fetchProposalHistory(proposalId: string, days: number = 7): Promise<ProposalHistory> {
  const { data, error, response } = await client.GET('/api/v1/proposals/{proposal_id}/history', {
    params: {
      path: {
        proposal_id: proposalId,
      },
      query: {
        days,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as ProposalHistory
}

export interface ProposalTransition {
  action: string  // 'submit', 'accept', 'reject', 'revise'
  label_id: string  // stable i18n key, e.g. 'submit', 'resubmit', 'revise_after_rejection'
  target_status: string  // target status
  enabled: boolean  // whether the transition is currently allowed
  disable_reason?: string | null  // reason if disabled
}

export interface ProposalTransitions {
  proposal_id: string
  current_status: string
  transitions: ProposalTransition[]
}

export async function fetchProposalTransitions(proposalId: string): Promise<ProposalTransitions> {
  const { data, error, response } = await client.GET('/api/v1/proposals/{proposal_id}/transitions', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as ProposalTransitions
}

export async function submitProposal(proposalId: string): Promise<ProposalDetail> {
  const { data, error, response } = await client.POST('/api/v1/proposals/{proposal_id}/submit', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'proposals.transitionFailed')
  }

  return data as unknown as ProposalDetail
}

export async function acceptProposal(proposalId: string): Promise<ProposalDetail> {
  const { data, error, response } = await client.POST('/api/v1/proposals/{proposal_id}/accept', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'proposals.transitionFailed')
  }

  return data as unknown as ProposalDetail
}

export async function rejectProposal(proposalId: string): Promise<ProposalDetail> {
  const { data, error, response } = await client.POST('/api/v1/proposals/{proposal_id}/reject', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'proposals.transitionFailed')
  }

  return data as unknown as ProposalDetail
}

export async function reviseProposal(proposalId: string): Promise<ProposalDetail> {
  const { data, error, response } = await client.POST('/api/v1/proposals/{proposal_id}/revise', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'proposals.transitionFailed')
  }

  return data as unknown as ProposalDetail
}

export async function createProposal(formData?: {
  title?: string
  submission_type?: string
  area?: string
  language?: string
  abstract?: string
  description?: string
  internal_notes?: string
  occurrence_count?: number
  duration_days?: number
  duration_time_per_day?: string
  is_basic_course?: boolean
  max_participants?: number
  material_cost_eur?: string
  preferred_dates?: string
  has_building_access?: boolean
  call_id?: string | null
}): Promise<ProposalSummary> {
  const { data, error, response } = await client.POST('/api/v1/proposals/create', {
    body: formData || {},
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'proposals.createFailed')
  }

  return data as unknown as ProposalSummary
}

export async function deleteProposal(proposalId: string): Promise<void> {
  const { error, response } = await client.DELETE('/api/v1/proposals/{proposal_id}', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }
}

export interface ProposalChecklistItem {
  status: 'ok' | 'error'
}

export interface ProposalHistoryEntry {
  timestamp: string  // ISO format datetime
  changed_by: string  // username of user who made the change
  change_type: string  // 'create', 'change', 'delete'
  field_name?: string | null  // which field was changed
  old_value?: string | null  // previous value
  new_value?: string | null  // new value
  summary: string  // human-readable summary of the change
}

export interface ProposalHistory {
  proposal_id: string
  entries: ProposalHistoryEntry[]
}

export interface SpeakerIn {
  email?: string
  display_name?: string
  biography?: string
}

export interface ReviewStats {
  approved: number
  revise: number
  rejected: number
  pending: number
  total: number
}

export interface ProposalListItem {
  id: string
  title: string
  status: string
  submission_type?: string | null
  owner?: UserBasic | null
  speakers: string[]
  occurrence_count: number
  accepted_event_count: number
  call_title?: string | null
  review_stats: ReviewStats
  my_review_status?: string | null
}

export async function fetchProposalsList(): Promise<ProposalListItem[]> {
  const { data, error, response } = await client.GET('/api/v1/proposals/' as never, undefined as never)
  if (error || !response.ok) {
    throw new Error('common.internalError')
  }
  return (data as unknown as ProposalListItem[]) || []
}

export interface ProposalDetail {
  id: string
  title: string
  submission_type: string
  area?: string | null
  language?: string | null
  abstract: string
  description: string
  internal_notes: string
  occurrence_count: number
  duration_days: number
  duration_time_per_day: string
  is_basic_course: boolean
  max_participants: number
  material_cost_eur: string
  preferred_dates: string
  has_building_access: boolean
  photo?: string | null
  owner?: UserBasic | null
  editors?: UserBasic[]
  moderation_comment?: string
  call_id?: string | null
}

export async function fetchProposal(proposalId: string): Promise<ProposalDetail> {
  const { data, error, response } = await client.GET('/api/v1/proposals/{proposal_id}', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as ProposalDetail
}

export async function updateProposal(proposalId: string, formData: {
  title?: string
  submission_type?: string
  area?: string | null
  language?: string | null
  abstract?: string
  description?: string
  internal_notes?: string
  occurrence_count?: number
  duration_days?: number
  duration_time_per_day?: string
  is_basic_course?: boolean
  max_participants?: number
  material_cost_eur?: string
  preferred_dates?: string
  has_building_access?: boolean
  // owner_id removed - owner is set on creation and cannot be changed
  editor_ids?: string[]
  moderation_comment?: string
  call_id?: string | null
}): Promise<ProposalDetail> {
  const { data, error, response } = await client.PUT('/api/v1/proposals/{proposal_id}', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
    body: formData,
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'proposals.validationError')
  }

  return data as unknown as ProposalDetail
}

// Speaker Management API Functions

export interface SpeakerOut {
  id: string
  email: string
  display_name: string
  biography: string
  profile_picture?: string | null
}

export interface ProposalSpeakerOut {
  id: string
  speaker: SpeakerOut
  role: string
  sort_order: number
}

export async function addSpeakerToProposal(
  proposalId: string,
  speaker: SpeakerIn
): Promise<ProposalSpeakerOut> {
  const { data, error, response } = await client.POST('/api/v1/proposals/{proposal_id}/speakers', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
    body: speaker,
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'speakers.addFailed')
  }

  return data as unknown as ProposalSpeakerOut
}

export async function fetchProposalSpeakers(proposalId: string): Promise<ProposalSpeakerOut[]> {
  const { data, error, response } = await client.GET('/api/v1/proposals/{proposal_id}/speakers', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as ProposalSpeakerOut[]
}

export async function updateSpeaker(
  proposalId: string,
  speakerId: string,
  speaker: SpeakerIn
): Promise<ProposalSpeakerOut> {
  const { data, error, response } = await client.PUT('/api/v1/proposals/{proposal_id}/speakers/{speaker_id}', {
    params: {
      path: {
        proposal_id: proposalId,
        speaker_id: speakerId,
      },
    },
    body: speaker,
  })

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'speakers.updateFailed')
  }

  return data as unknown as ProposalSpeakerOut
}

export async function removeSpeaker(proposalId: string, speakerId: string): Promise<void> {
  const { error, response } = await client.DELETE('/api/v1/proposals/{proposal_id}/speakers/{speaker_id}', {
    params: {
      path: {
        proposal_id: proposalId,
        speaker_id: speakerId,
      },
    },
  })

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }
}

export async function uploadProposalImage(
  proposalId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<ProposalDetail> {
  return await uploadMultipartJson<ProposalDetail>(
    `/api/v1/proposals/${proposalId}/photo`,
    file,
    onProgress
  )
}

export async function uploadSpeakerImage(
  proposalId: string,
  speakerId: string,
  file: File,
  onProgress?: (progress: number) => void
): Promise<ProposalSpeakerOut> {
  return await uploadMultipartJson<ProposalSpeakerOut>(
    `/api/v1/proposals/${proposalId}/speakers/${speakerId}/profile-picture`,
    file,
    onProgress
  )
}

// Lookup table API Functions

export interface LookupItem {
  code: string
  label: string
  description?: string | null
  is_active?: boolean
  sort_order?: number
}

export async function fetchSubmissionTypes(): Promise<LookupItem[]> {
  const { data, error, response } = await client.GET('/api/v1/submission_types', {})

  if (error || !response.ok) {
    console.error('Failed to fetch submission types:', response?.statusText)
    return []
  }

  return (data as unknown as LookupItem[]) || []
}

export async function fetchProposalLanguages(): Promise<LookupItem[]> {
  const { data, error, response } = await client.GET('/api/v1/proposal_languages', {})

  if (error || !response.ok) {
    console.error('Failed to fetch proposal languages:', response?.statusText)
    return []
  }

  return (data as unknown as LookupItem[]) || []
}

export async function fetchProposalAreas(): Promise<LookupItem[]> {
  const { data, error, response } = await client.GET('/api/v1/proposal_areas', {})

  if (error || !response.ok) {
    console.error('Failed to fetch proposal areas:', response?.statusText)
    return []
  }

  return (data as unknown as LookupItem[]) || []
}

export async function fetchPermissionGroups(): Promise<LookupItem[]> {
  const response = await apiFetch('/api/v1/permission_groups', { credentials: 'include' })
  if (!response.ok) {
    console.error('Failed to fetch permission groups:', response.statusText)
    return []
  }
  const data = await response.json() as Array<{ code: string; label: string }>
  return data.map((g) => ({ code: g.code, label: g.label }))
}

export interface ExternalCalendarEvent {
  id: string
  title: string
  startUtc: string
  endUtc: string
  source: string
}

export async function fetchExternalCalendarEvents(
  startUtc: string,
  endUtc: string
): Promise<ExternalCalendarEvent[]> {
  const { data, error, response } = await client.GET('/api/v1/calendar/events', {
    params: {
      query: {
        start_utc: startUtc,
        end_utc: endUtc,
      },
    },
  })

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }

  return (data as unknown as ExternalCalendarEvent[]) || []
}

export interface ProposalEventSummary {
  id: string
  name: string
  startTime: string
  endTime: string
  status: string
  series_id: string
  series_name: string
}

export interface EventTransition {
  action: string
  label_id: string  // stable i18n key, matches action name
  target_status: string
  enabled: boolean
  disable_reason?: string | null
}

export interface EventTransitions {
  event_id: string
  current_status: string
  transitions: EventTransition[]
}

export async function fetchEventTransitions(seriesId: string, eventId: string): Promise<EventTransitions> {
  const { data, error, response } = await client.GET(
    '/api/v1/series/{series_id}/events/{event_id}/transitions',
    {
      params: {
        path: { series_id: seriesId, event_id: eventId },
      },
    }
  )

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as EventTransitions
}

export async function executeEventTransition(seriesId: string, eventId: string, action: string): Promise<Event> {
  const { data, error, response } = await client.POST(
    '/api/v1/series/{series_id}/events/{event_id}/transitions/{action}',
    {
      params: {
        path: { series_id: seriesId, event_id: eventId, action },
      },
    }
  )

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'events.transitionFailed')
  }

  return toFrontendEvent(data as unknown as ApiEvent)
}

export interface FlowEdge {
  source: string
  target: string
  label_id: string
}

export interface EventFlowDiagram {
  nodes: string[]
  edges: FlowEdge[]
}

export async function fetchEventFlowDiagram(): Promise<EventFlowDiagram> {
  const response = await apiFetch('/api/v1/series/event-flow-diagram', {
    credentials: 'include',
  })
  if (!response.ok) {
    throw new Error('common.internalError')
  }
  return response.json() as Promise<EventFlowDiagram>
}

export async function fetchProposalFlowDiagram(): Promise<EventFlowDiagram> {
  const response = await apiFetch('/api/v1/proposals/flow-diagram', {
    credentials: 'include',
  })
  if (!response.ok) {
    throw new Error('common.internalError')
  }
  return response.json() as Promise<EventFlowDiagram>
}

export async function fetchProposalEvents(proposalId: string): Promise<ProposalEventSummary[]> {
  const { data, error, response } = await client.GET('/api/v1/proposals/{proposal_id}/events', {
    params: {
      path: {
        proposal_id: proposalId,
      },
    },
  })

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }

  return (data as unknown as ProposalEventSummary[]) || []
}

export interface CalculatedPrices {
  id: string
  event_id: string
  pricing_configuration_id: string | null
  member_regular_gross_eur: string | null
  member_discounted_gross_eur: string | null
  guest_regular_gross_eur: string | null
  guest_discounted_gross_eur: string | null
  business_net_eur: string | null
  internal_training_eur: string | null
}

export interface UpdateCalculatedPricesRequest {
  seriesId: string
  eventId: string
  pricing_configuration_id?: string | null
  member_regular_gross_eur?: string | null
  member_discounted_gross_eur?: string | null
  guest_regular_gross_eur?: string | null
  guest_discounted_gross_eur?: string | null
  business_net_eur?: string | null
  internal_training_eur?: string | null
}

export async function fetchCalculatedPrices(seriesId: string, eventId: string): Promise<CalculatedPrices | null> {
  const { data, error, response } = await client.GET(
    '/api/v1/pricing/{series_id}/events/{event_id}/calculated-prices',
    {
      params: {
        path: { series_id: seriesId, event_id: eventId },
      },
    }
  )

  if (response.status === 404) {
    return null
  }

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as CalculatedPrices
}

export async function createCalculatedPrices(
  seriesId: string,
  eventId: string,
  useDefaultPricingConfiguration: boolean
): Promise<CalculatedPrices> {
  const { data, error, response } = await client.POST(
    '/api/v1/pricing/{series_id}/events/{event_id}/calculated-prices/create',
    {
      params: {
        path: { series_id: seriesId, event_id: eventId },
      },
      body: {
        use_default_pricing_configuration: useDefaultPricingConfiguration,
      },
    }
  )

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as CalculatedPrices
}

export async function updateCalculatedPrices(
  request: UpdateCalculatedPricesRequest
): Promise<CalculatedPrices> {
  const { data, error, response } = await client.PUT(
    '/api/v1/pricing/{series_id}/events/{event_id}/calculated-prices',
    {
      params: {
        path: {
          series_id: request.seriesId,
          event_id: request.eventId,
        },
      },
      body: {
        pricing_configuration_id: request.pricing_configuration_id,
        member_regular_gross_eur: request.member_regular_gross_eur,
        member_discounted_gross_eur: request.member_discounted_gross_eur,
        guest_regular_gross_eur: request.guest_regular_gross_eur,
        guest_discounted_gross_eur: request.guest_discounted_gross_eur,
        business_net_eur: request.business_net_eur,
        internal_training_eur: request.internal_training_eur,
      },
    }
  )

  if (error || !response.ok || !data) {
    throw new Error(error?.code || 'common.internalError')
  }

  return data as unknown as CalculatedPrices
}

export async function deleteCalculatedPrices(seriesId: string, eventId: string): Promise<void> {
  const { error, response } = await client.DELETE(
    '/api/v1/pricing/{series_id}/events/{event_id}/calculated-prices',
    {
      params: {
        path: { series_id: seriesId, event_id: eventId },
      },
    }
  )

  if (error || !response.ok) {
    throw new Error(error?.code || 'common.internalError')
  }
}

export interface SiteConfig {
  imprint_url: string
  privacy_policy_url: string
  account_management_url: string
}

export async function fetchSiteConfig(): Promise<SiteConfig> {
  const response = await apiFetch('/api/v1/config')
  if (!response.ok) throw new Error('common.internalError')
  return response.json() as Promise<SiteConfig>
}

export interface CallOut {
  id: string
  title: string
  description: string
  execution_period_start: string  // ISO date string
  execution_period_end: string    // ISO date string
  submission_deadline: string     // ISO datetime string
  print_deadline: string          // ISO date string
  responsible_name: string
  responsible_email: string
  is_active: boolean
}

export async function fetchCalls(activeOnly = true): Promise<CallOut[]> {
  const response = await apiFetch(`/api/v1/calls/?active_only=${activeOnly}`, {
    credentials: 'include',
  })
  if (!response.ok) throw new Error('common.internalError')
  return response.json() as Promise<CallOut[]>
}

export async function fetchCall(callId: string): Promise<CallOut> {
  const response = await apiFetch(`/api/v1/calls/${callId}`, {
    credentials: 'include',
  })
  if (!response.ok) throw new Error('common.internalError')
  return response.json() as Promise<CallOut>
}

// ── Proposal Reviews ──────────────────────────────────────────────────────────

export interface ProposalReviewOut {
  id: string
  kind: 'user' | 'group'
  reviewer_id: string | null
  reviewer_username: string | null
  reviewer_is_system: boolean
  group_code: string
  group_label: string
  group_member_count: number | null
  status: string
  comment: string
  requested_by_id: string | null
  requested_by_username: string | null
  requested_at: string | null
  completed_at: string | null
  requested_directly: boolean
  requested_via_groups: string[]
  previous_status: string
  previous_comment: string
  migrated: boolean
  created_at: string
}

export interface ProposalReviewsOut {
  reviews: ProposalReviewOut[]
  pending_via_groups: string[]
}

export async function fetchProposalReviews(proposalId: string): Promise<ProposalReviewsOut> {
  const response = await apiFetch(`/api/v1/proposals/${proposalId}/reviews`, {
    credentials: 'include',
  })
  if (!response.ok) throw new Error('common.internalError')
  return response.json() as Promise<ProposalReviewsOut>
}

export interface ProposalReviewCreateIn {
  kind: 'user' | 'group'
  reviewer_id?: string | null
  group_code?: string | null
  reviewer_is_system?: boolean
  comment?: string
  status?: string
  requested_directly?: boolean
  requested_via_groups?: string[]
  migrated?: boolean
}

export async function createProposalReview(
  proposalId: string,
  payload: ProposalReviewCreateIn,
): Promise<ProposalReviewOut> {
  const csrf = await getCsrfToken()
  const response = await apiFetch(`/api/v1/proposals/${proposalId}/reviews`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error((err as { code?: string }).code || 'common.internalError')
  }
  return response.json() as Promise<ProposalReviewOut>
}

export async function updateProposalReview(
  proposalId: string,
  reviewId: string,
  status: string,
  comment: string,
): Promise<ProposalReviewOut> {
  const csrf = await getCsrfToken()
  const response = await apiFetch(`/api/v1/proposals/${proposalId}/reviews/${reviewId}`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: JSON.stringify({ status, comment }),
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({}))
    throw new Error((err as { code?: string }).code || 'common.internalError')
  }
  return response.json() as Promise<ProposalReviewOut>
}

export async function deleteProposalReview(proposalId: string, reviewId: string): Promise<void> {
  const csrf = await getCsrfToken()
  const response = await apiFetch(`/api/v1/proposals/${proposalId}/reviews/${reviewId}`, {
    method: 'DELETE',
    credentials: 'include',
    headers: { 'X-CSRFToken': csrf },
  })
  if (!response.ok && response.status !== 204) {
    throw new Error('common.internalError')
  }
}

export async function resetProposalReviews(proposalId: string): Promise<ProposalReviewOut[]> {
  const csrf = await getCsrfToken()
  const response = await apiFetch(`/api/v1/proposals/${proposalId}/reviews/reset`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    body: '{}',
  })
  if (!response.ok) throw new Error('common.internalError')
  return response.json() as Promise<ProposalReviewOut[]>
}

