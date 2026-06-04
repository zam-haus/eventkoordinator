import createClient from 'openapi-fetch'
import type { paths, components } from './schema_udm'
import { getCsrfToken } from './api'

const CSRF_HEADER_NAME = 'X-CSRFToken'

const udmClient = createClient<paths>({
  baseUrl: '',
  fetch: async (request: Request) => {
    const token = await getCsrfToken()
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method)) {
      const headers = new Headers(request.headers)
      if (token) headers.set(CSRF_HEADER_NAME, token)
      return fetch(new Request(request, { headers, credentials: 'include' }))
    }
    return fetch(new Request(request, { credentials: 'include' }))
  },
})

// ── Structured API error ──────────────────────────────────────────────────────

export interface PydanticErrorItem {
  loc: (string | number)[]
  msg: string
  type: string
}

/** A message returned by the Rego policy on save. */
export interface PolicyMessage {
  level: string
  text: string
  field?: string
  /** Dot-separated field paths to highlight in the UI.
   *  Top-level: "slug". Sub-field: "parent_slug.child_slug". */
  highlight_fields?: string[]
}

/** Thrown by write operations when the server returns a structured error body. */
export class UdmApiError extends Error {
  /** Pydantic validation errors from `{"detail": [...]}` */
  readonly pydanticErrors: PydanticErrorItem[]
  /** Django-style field errors from `{"errors": {...}}` */
  readonly fieldErrors: Record<string, string[]>
  /** Policy messages from `{"policy_messages": [...]}` */
  readonly policyMessages: PolicyMessage[]

  constructor(
    message: string,
    pydanticErrors: PydanticErrorItem[] = [],
    fieldErrors: Record<string, string[]> = {},
    policyMessages: PolicyMessage[] = [],
  ) {
    super(message)
    this.name = 'UdmApiError'
    this.pydanticErrors = pydanticErrors
    this.fieldErrors = fieldErrors
    this.policyMessages = policyMessages
  }

  /** All human-readable error strings. Falls back to the raw message when
   *  the response has no structured Pydantic / field errors (e.g. TransitionError). */
  get allMessages(): string[] {
    const msgs: string[] = []
    for (const e of this.pydanticErrors) {
      const loc = e.loc
        .filter(s => s !== 'body' && s !== 'payload')
        .join(' → ')
      msgs.push(loc ? `${loc}: ${e.msg}` : e.msg)
    }
    for (const [field, errs] of Object.entries(this.fieldErrors)) {
      for (const err of errs) {
        msgs.push(field === '__all__' ? err : `${field}: ${err}`)
      }
    }
    for (const msg of this.policyMessages) {
      msgs.push(msg.text)
    }
    if (msgs.length === 0 && this.message) {
      msgs.push(this.message)
    }
    return msgs
  }

  /** Top-level field slugs (first path segment) that should be highlighted. */
  get highlightedSlugs(): Set<string> {
    const slugs = new Set<string>()
    for (const msg of this.policyMessages) {
      for (const path of msg.highlight_fields ?? []) {
        slugs.add(path.split('.')[0])
      }
    }
    return slugs
  }

  /** Per-parent-slug set of child slugs to highlight inside submodel fields. */
  get highlightedSubFields(): Record<string, Set<string>> {
    const result: Record<string, Set<string>> = {}
    for (const msg of this.policyMessages) {
      for (const path of msg.highlight_fields ?? []) {
        const dot = path.indexOf('.')
        if (dot === -1) continue
        const parent = path.slice(0, dot)
        const child = path.slice(dot + 1)
        if (!result[parent]) result[parent] = new Set()
        result[parent].add(child)
      }
    }
    return result
  }
}

/**
 * Parse an already-decoded error body (from openapi-fetch's `error` return value,
 * or a manually-read response body) and throw a UdmApiError.
 *
 * openapi-fetch consumes response.json() internally, so we must use the `error`
 * value it hands back rather than calling response.json() again.
 * For raw fetch() calls, pass the result of await response.json() instead.
 */
function throwApiError(errorBody: unknown, fallback: string): never {
  let pydanticErrors: PydanticErrorItem[] = []
  let fieldErrors: Record<string, string[]> = {}
  let policyMessages: PolicyMessage[] = []
  let message = fallback

  if (errorBody !== null && typeof errorBody === 'object') {
    const body = errorBody as Record<string, unknown>
    // Pydantic / FastAPI format: {"detail": [{loc, msg, type}]}
    if (Array.isArray(body['detail'])) {
      pydanticErrors = body['detail'] as PydanticErrorItem[]
      message = pydanticErrors.map(e => e.msg).join('; ') || fallback
    } else if (typeof body['detail'] === 'string') {
      message = body['detail']
    }
    // Django-ninja field errors: {"errors": {"field": ["msg"]}}
    if (body['errors'] && typeof body['errors'] === 'object') {
      fieldErrors = body['errors'] as Record<string, string[]>
      if (!pydanticErrors.length) {
        message = Object.values(fieldErrors).flat().join('; ') || fallback
      }
    }
    // Policy messages: {"policy_messages": [{level, text, highlight_fields?}]}
    if (Array.isArray(body['policy_messages'])) {
      policyMessages = body['policy_messages'] as PolicyMessage[]
      if (!pydanticErrors.length && !Object.keys(fieldErrors).length) {
        message = policyMessages.map(m => m.text).join('; ') || fallback
      }
    }
    // TransitionError / general backend error: {"error": "some_code", ...}
    if (message === fallback && typeof body['error'] === 'string') {
      message = body['error']
    }
  }

  throw new UdmApiError(message, pydanticErrors, fieldErrors, policyMessages)
}

// ── Validation result (validate_only endpoints) ───────────────────────────────

export interface ValidationResult {
  valid: boolean
  policy_messages: PolicyMessage[]
  errors: Record<string, string[]>
}

/** Extract top-level slugs to highlight from a ValidationResult. */
export function highlightedSlugsFromResult(result: ValidationResult): Set<string> {
  const slugs = new Set<string>()
  for (const msg of result.policy_messages) {
    for (const path of msg.highlight_fields ?? []) {
      slugs.add(path.split('.')[0])
    }
  }
  for (const field of Object.keys(result.errors)) {
    if (field !== '__all__') slugs.add(field)
  }
  return slugs
}

/** Extract per-parent sub-field highlight sets from a ValidationResult. */
export function highlightedSubFieldsFromResult(result: ValidationResult): Record<string, Set<string>> {
  const out: Record<string, Set<string>> = {}
  for (const msg of result.policy_messages) {
    for (const path of msg.highlight_fields ?? []) {
      const dot = path.indexOf('.')
      if (dot === -1) continue
      const parent = path.slice(0, dot)
      const child = path.slice(dot + 1)
      if (!out[parent]) out[parent] = new Set()
      out[parent].add(child)
    }
  }
  return out
}

export async function udmValidateEntity(
  entityId: string,
  changedFields: Record<string, unknown>,
): Promise<ValidationResult> {
  const token = await getCsrfToken()
  const resp = await fetch(`/api/udm/entities/${entityId}/?validate_only=true`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-CSRFToken': token } : {}),
    },
    body: JSON.stringify({ changed_fields: changedFields }),
    credentials: 'include',
  })
  if (resp.status === 404) throw new Error('Entity not found')
  return resp.json() as Promise<ValidationResult>
}

export async function udmValidateTransition(
  entityId: string,
  field: string,
  transition: string,
): Promise<ValidationResult> {
  const token = await getCsrfToken()
  const resp = await fetch(`/api/udm/entities/${entityId}/transition/?validate_only=true`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-CSRFToken': token } : {}),
    },
    body: JSON.stringify({ field, transition }),
    credentials: 'include',
  })
  if (resp.status === 404) throw new Error('Entity not found')
  return resp.json() as Promise<ValidationResult>
}

/** For raw fetch() calls: read the body, then throw. */
async function throwRawFetchError(response: Response, fallback: string): Promise<never> {
  let body: unknown = null
  try { body = await response.json() } catch { /* non-JSON — leave null */ }
  throwApiError(body, fallback)
}

// ── Type aliases ──────────────────────────────────────────────────────────────

export type FieldConfigOut = components['schemas']['FieldConfigOut']
export type FieldConfigCreateIn = components['schemas']['FieldConfigCreateIn']
export type FieldConfigUpdateIn = components['schemas']['FieldConfigUpdateIn']
export type ConfigVersionOut = components['schemas']['ConfigVersionOut']
export type ConfigDraftIn = components['schemas']['ConfigDraftIn']
export type FieldDefinitionIn = components['schemas']['FieldDefinitionIn']
export type FieldDefinitionOut = components['schemas']['FieldDefinitionOut']
export type ConfigLanguageOut = components['schemas']['ConfigLanguageOut']
export type ConfigLanguageIn = components['schemas']['ConfigLanguageIn']
export type PolicyOut = components['schemas']['PolicyOut']
export type PolicyCreateIn = components['schemas']['PolicyCreateIn']
export type PolicyUpdateIn = components['schemas']['PolicyUpdateIn']
export type PolicyAssignIn = components['schemas']['PolicyAssignIn']
export type UDMTypeOut = components['schemas']['UDMTypeOut']
export type UDMTypeCreateIn = components['schemas']['UDMTypeCreateIn']
export interface DashboardColumnOut {
  key: string
  label: string
  renderer: 'text' | 'progress_bar' | 'meter'
  value: unknown
}

export type EntityOut = components['schemas']['EntityOut'] & {
  dashboard_columns?: DashboardColumnOut[]
}
export type EntityCreateIn = components['schemas']['EntityCreateIn']
export type EntityPatchIn = components['schemas']['EntityPatchIn']
export type FieldValueOut = components['schemas']['FieldValueOut']
export type WorkflowStateOut = components['schemas']['WorkflowStateOut']
export type WorkflowTransitionOut = components['schemas']['WorkflowTransitionOut']
export type WorkflowDefinitionOut = components['schemas']['WorkflowDefinitionOut']
export type WorkflowVersionOut = components['schemas']['WorkflowVersionOut']
export type DataType = components['schemas']['DataType']
export type TransitionIn = components['schemas']['TransitionIn']
export type EditHistoryOut = components['schemas']['EditHistoryOut']
export type UserAutocompleteItem = components['schemas']['UserAutocompleteItem']
export type GroupAutocompleteItem = components['schemas']['GroupAutocompleteItem']
export type EntityAutocompleteItem = components['schemas']['EntityAutocompleteItem']
export type StagingFileOut = components['schemas']['StagingFileOut']
export type MigrationPreviewOut = components['schemas']['MigrationPreviewOut']
export type MigrationPreviewFieldOut = components['schemas']['MigrationPreviewFieldOut']
export type MigrationFieldMappingIn = components['schemas']['MigrationFieldMappingIn']
export type MigrationExecuteIn = components['schemas']['MigrationExecuteIn']
export type MigrationAction = components['schemas']['MigrationAction']
export type BulkMigrationCreateIn = components['schemas']['BulkMigrationCreateIn']
export type BulkMigrationOut = components['schemas']['BulkMigrationOut']
export type SubmodelMigrationIn = components['schemas']['SubmodelMigrationIn']
export type WorkflowFieldStateMappingIn = components['schemas']['WorkflowFieldStateMappingIn']
export type PolicyEvalOut = components['schemas']['PolicyEvalOut']

// Version list item returned by list_config_versions (content?: never in schema but backend returns JSON)
export interface ConfigVersionListItem {
  id: string
  status: string
  notes: string
  published_at: string | null
  created_at: string
  /** Number of root entities currently pinned to this version. */
  entity_count: number
}

// ── FieldConfig ───────────────────────────────────────────────────────────────

export async function udmListConfigs(): Promise<FieldConfigOut[]> {
  const { data, error, response } = await udmClient.GET('/api/udm/configs/')
  if (error || !response.ok) throw new Error('Failed to list configs')
  return data as FieldConfigOut[]
}

export async function udmCreateConfig(payload: FieldConfigCreateIn): Promise<FieldConfigOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/configs/', { body: payload })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to create config')
  return data as unknown as FieldConfigOut
}

export async function udmGetConfig(configId: string): Promise<FieldConfigOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/configs/{config_id}/', {
    params: { path: { config_id: configId } },
  })
  if (error || !response.ok || !data) throw new Error('Config not found')
  return data as FieldConfigOut
}

export async function udmUpdateConfig(configId: string, payload: FieldConfigUpdateIn): Promise<FieldConfigOut> {
  const { data, error, response } = await udmClient.PATCH('/api/udm/configs/{config_id}/', {
    params: { path: { config_id: configId } },
    body: payload,
  })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to update config')
  return data as FieldConfigOut
}

export async function udmDeleteConfig(configId: string): Promise<void> {
  const { response } = await udmClient.DELETE('/api/udm/configs/{config_id}/', {
    params: { path: { config_id: configId } },
  })
  if (!response.ok) {
    let msg = 'Failed to delete config'
    try { msg = ((await response.json()) as { detail?: string }).detail ?? msg } catch { /* ignore */ }
    throw new Error(msg)
  }
}

// ── Config Versions ───────────────────────────────────────────────────────────

export async function udmListConfigVersions(configId: string): Promise<ConfigVersionListItem[]> {
  const response = await fetch(`/api/udm/configs/${configId}/versions/`, { credentials: 'include' })
  if (!response.ok) throw new Error('Failed to list versions')
  return response.json() as Promise<ConfigVersionListItem[]>
}

export async function udmGetDraftVersion(configId: string): Promise<ConfigVersionOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/configs/{config_id}/versions/draft/', {
    params: { path: { config_id: configId } },
  })
  if (error || !response.ok || !data) throw new Error('No draft version')
  return data as ConfigVersionOut
}

export async function udmGetPublishedVersion(configId: string): Promise<ConfigVersionOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/configs/{config_id}/versions/published/', {
    params: { path: { config_id: configId } },
  })
  if (error || !response.ok || !data) throw new Error('No published version')
  return data as ConfigVersionOut
}

/** Fetch a single config version by id (any status). The backend route is
 *  not in the generated schema, so this uses a raw fetch. */
export async function udmGetConfigVersion(versionId: string): Promise<ConfigVersionOut> {
  const response = await fetch(`/api/udm/config-versions/${versionId}/`, { credentials: 'include' })
  if (!response.ok) return throwRawFetchError(response, 'Config version not found')
  return response.json() as Promise<ConfigVersionOut>
}

export async function udmReplaceDraft(configId: string, payload: ConfigDraftIn): Promise<ConfigVersionOut> {
  const { data, error, response } = await udmClient.PUT('/api/udm/configs/{config_id}/versions/draft/', {
    params: { path: { config_id: configId } },
    body: payload,
  })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to replace draft')
  return data as ConfigVersionOut
}

export async function udmPublishDraft(configId: string): Promise<ConfigVersionOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/configs/{config_id}/versions/draft/publish/', {
    params: { path: { config_id: configId } },
  })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to publish draft')
  return data as ConfigVersionOut
}

// ── UDM Types ─────────────────────────────────────────────────────────────────

export async function udmListTypes(): Promise<UDMTypeOut[]> {
  const { data, error, response } = await udmClient.GET('/api/udm/types/')
  if (error || !response.ok) throw new Error('Failed to list types')
  return data as UDMTypeOut[]
}

export async function udmCreateType(payload: UDMTypeCreateIn): Promise<UDMTypeOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/types/', { body: payload })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to create type')
  return data as unknown as UDMTypeOut
}

export async function udmGetType(typeId: string): Promise<UDMTypeOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/types/{type_id}/', {
    params: { path: { type_id: typeId } },
  })
  if (error || !response.ok || !data) throw new Error('Type not found')
  return data as UDMTypeOut
}

export async function udmEvalPolicy(
  typeId: string,
  entityId: string,
  userId: string,
  action: string,
  transition?: string,
): Promise<PolicyEvalOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/types/{type_id}/eval-policy/', {
    params: {
      path: { type_id: typeId },
      query: { entity_id: entityId, user_id: userId, action, transition: transition ?? null },
    },
  })
  if (error || !response.ok || !data) throw new Error('Evaluation failed')
  return data as PolicyEvalOut
}

export async function udmGetTypeDescriptions(typeId: string): Promise<Record<string, string>> {
  const resp = await fetch(`/api/udm/types/${typeId}/public-fields/`, { credentials: 'include' })
  if (!resp.ok) return {}
  const data = await resp.json() as { descriptions: Record<string, string> }
  return data.descriptions ?? {}
}

export async function udmGetTypeConfig(typeId: string): Promise<ConfigVersionOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/types/{type_id}/config/', {
    params: { path: { type_id: typeId } },
  })
  if (error || !response.ok || !data) throw new Error('No config for type')
  return data as ConfigVersionOut
}

export async function udmUpdateTypeMeta(
  typeId: string,
  payload: { name?: string; label?: string; description?: string },
): Promise<UDMTypeOut> {
  const token = await getCsrfToken()
  const resp = await fetch(`/api/udm/types/${typeId}/`, {
    method: 'PATCH',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { [CSRF_HEADER_NAME]: token } : {}),
    },
    body: JSON.stringify(payload),
  })
  if (!resp.ok) return throwRawFetchError(resp, 'Failed to update UDM type')
  return resp.json() as Promise<UDMTypeOut>
}

export async function udmUpdateType(typeId: string, fieldConfigId: string | null): Promise<UDMTypeOut> {
  const url = fieldConfigId
    ? `/api/udm/types/${typeId}/?field_config_id=${fieldConfigId}`
    : `/api/udm/types/${typeId}/`
  const token = await getCsrfToken()
  const resp = await fetch(url, {
    method: 'PATCH',
    credentials: 'include',
    headers: token ? { [CSRF_HEADER_NAME]: token } : {},
  })
  if (!resp.ok) return throwRawFetchError(resp, 'Failed to update UDM type')
  return resp.json() as Promise<UDMTypeOut>
}

export async function udmDeleteType(typeId: string): Promise<void> {
  const token = await getCsrfToken()
  const resp = await fetch(`/api/udm/types/${typeId}/`, {
    method: 'DELETE',
    credentials: 'include',
    headers: token ? { [CSRF_HEADER_NAME]: token } : {},
  })
  if (!resp.ok) await throwRawFetchError(resp, 'Failed to delete UDM type')
}

// ── Policies ──────────────────────────────────────────────────────────────────

export async function udmListPolicies(): Promise<PolicyOut[]> {
  const { data, error, response } = await udmClient.GET('/api/udm/policies/')
  if (error || !response.ok) throw new Error('Failed to list policies')
  return data as PolicyOut[]
}

export async function udmCreatePolicy(payload: PolicyCreateIn): Promise<PolicyOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/policies/', { body: payload })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to create policy')
  return data as unknown as PolicyOut
}

export async function udmUpdatePolicy(slug: string, payload: PolicyUpdateIn): Promise<PolicyOut> {
  const { data, error, response } = await udmClient.PUT('/api/udm/policies/{slug}/', {
    params: { path: { slug } },
    body: payload,
  })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to update policy')
  return data as PolicyOut
}

export async function udmDeletePolicy(slug: string): Promise<void> {
  const { error, response } = await udmClient.DELETE('/api/udm/policies/{slug}/', {
    params: { path: { slug } },
  })
  if (error || !response.ok) throw new Error('Failed to delete policy')
}

export async function udmListTypePolicies(typeId: string): Promise<PolicyOut[]> {
  const { data, error, response } = await udmClient.GET('/api/udm/types/{type_id}/policies/', {
    params: { path: { type_id: typeId } },
  })
  if (error || !response.ok) throw new Error('Failed to list type policies')
  return data as PolicyOut[]
}

export async function udmAssignPolicy(typeId: string, payload: PolicyAssignIn): Promise<PolicyOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/types/{type_id}/policies/', {
    params: { path: { type_id: typeId } },
    body: payload,
  })
  if (error || !response.ok || !data) throw new Error('Failed to assign policy')
  return data as unknown as PolicyOut
}

export async function udmRemovePolicy(typeId: string, slug: string): Promise<void> {
  const { error, response } = await udmClient.DELETE('/api/udm/types/{type_id}/policies/{slug}/', {
    params: { path: { type_id: typeId, slug } },
  })
  if (error || !response.ok) throw new Error('Failed to remove policy')
}

// ── Entities ──────────────────────────────────────────────────────────────────

export async function udmListEntitiesByType(typeId: string, pageSize = 200): Promise<EntityOut[]> {
  const resp = await fetch(
    `/api/udm/entities/?type_id=${encodeURIComponent(typeId)}&page_size=${pageSize}`,
    { credentials: 'include' },
  )
  if (!resp.ok) return throwRawFetchError(resp, 'Failed to list entities')
  return resp.json() as Promise<EntityOut[]>
}

export async function udmCanCreateEntity(typeId: string): Promise<ValidationResult> {
  const token = await getCsrfToken()
  const resp = await fetch('/api/udm/entities/?validate=true', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-CSRFToken': token } : {}),
    },
    body: JSON.stringify({ user_defined_model_type_id: typeId }),
    credentials: 'include',
  })
  if (!resp.ok) return { valid: false, policy_messages: [], errors: {} }
  return resp.json() as Promise<ValidationResult>
}

export async function udmCreateEntity(payload: EntityCreateIn): Promise<EntityOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/entities/', { body: payload })
  if (error || !response.ok || !data) throw new Error('Failed to create entity')
  return data as unknown as EntityOut
}

export async function udmGetEntity(entityId: string): Promise<EntityOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/entities/{entity_id}/', {
    params: { path: { entity_id: entityId } },
  })
  if (error || !response.ok || !data) throwApiError(error, 'Entity not found')
  return data as EntityOut
}

export async function udmPatchEntity(entityId: string, changedFields: Record<string, unknown>): Promise<EntityOut> {
  const { data, error, response } = await udmClient.PATCH('/api/udm/entities/{entity_id}/', {
    params: { path: { entity_id: entityId } },
    body: { changed_fields: changedFields },
  })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to patch entity')
  return data as EntityOut
}

export async function udmDeleteEntity(entityId: string): Promise<void> {
  const { error, response } = await udmClient.DELETE('/api/udm/entities/{entity_id}/', {
    params: { path: { entity_id: entityId } },
  })
  if (error || !response.ok) throw new Error('Failed to delete entity')
}

export async function udmTransitionEntity(
  entityId: string,
  field: string,
  transition: string,
  changedFields: Record<string, unknown> = {},
): Promise<EntityOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/entities/{entity_id}/transition/', {
    params: { path: { entity_id: entityId } },
    body: { field, transition, changed_fields: changedFields },
  })
  if (error || !response.ok || !data) throwApiError(error, 'Transition failed')
  return data as EntityOut
}

export async function udmListWorkflows(): Promise<WorkflowDefinitionOut[]> {
  const { data, error, response } = await udmClient.GET('/api/udm/workflows/', {})
  if (error || !response.ok || !data) throw new Error('Failed to load workflows')
  return data as WorkflowDefinitionOut[]
}

export async function udmEntityHistory(entityId: string, page = 1): Promise<EditHistoryOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/entities/{entity_id}/history/', {
    params: { path: { entity_id: entityId }, query: { page } },
  })
  if (error || !response.ok || !data) throw new Error('Failed to load history')
  return data as EditHistoryOut
}

// ── Migration ─────────────────────────────────────────────────────────────────

/** Build a migration preview for one entity. Targets either the published config
 *  of a UDM type, or an explicit target config version. NOTE: each call creates a
 *  migration row server-side, so only invoke on explicit user action. */
export async function udmMigrationPreview(
  entityId: string,
  opts: { targetTypeId?: string; targetVersionId?: string },
): Promise<MigrationPreviewOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/entities/{entity_id}/migration-preview/', {
    params: {
      path: { entity_id: entityId },
      query: {
        target_user_defined_model_type: opts.targetTypeId ?? null,
        target_version: opts.targetVersionId ?? null,
      },
    },
  })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to build migration preview')
  return data as MigrationPreviewOut
}

export async function udmExecuteMigration(
  entityId: string,
  migrationId: string,
  fieldMappings: MigrationFieldMappingIn[],
): Promise<EntityOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/entities/{entity_id}/migrate/', {
    params: { path: { entity_id: entityId } },
    body: { migration_id: migrationId, confirmed: true, field_mappings: fieldMappings },
  })
  if (error || !response.ok || !data) throwApiError(error, 'Migration failed')
  return data as EntityOut
}

export interface BulkMigrationPreviewResult {
  affected_entity_count: number
  source_version_id: string
  target_version_id: string
}

export async function udmBulkMigrationPreview(
  sourceVersionId: string,
  targetVersionId: string,
  typeFilterId?: string,
): Promise<BulkMigrationPreviewResult> {
  // Endpoint returns a bare JsonResponse (no response schema), so use raw fetch.
  const params = new URLSearchParams({
    source_version_id: sourceVersionId,
    target_version_id: targetVersionId,
  })
  if (typeFilterId) params.set('type_filter_id', typeFilterId)
  const token = await getCsrfToken()
  const response = await fetch(`/api/udm/bulk-migrations/preview/?${params.toString()}`, {
    method: 'POST',
    credentials: 'include',
    headers: token ? { [CSRF_HEADER_NAME]: token } : {},
  })
  if (!response.ok) return throwRawFetchError(response, 'Bulk preview failed')
  return response.json() as Promise<BulkMigrationPreviewResult>
}

export async function udmCreateBulkMigration(payload: BulkMigrationCreateIn): Promise<BulkMigrationOut> {
  const { data, error, response } = await udmClient.POST('/api/udm/bulk-migrations/', { body: payload })
  if (error || !response.ok || !data) throwApiError(error, 'Failed to create bulk migration')
  return data as unknown as BulkMigrationOut
}

export async function udmExecuteBulkMigration(planId: string): Promise<void> {
  const { error, response } = await udmClient.POST('/api/udm/bulk-migrations/{plan_id}/execute/', {
    params: { path: { plan_id: planId } },
  })
  if (error || !response.ok) throwApiError(error, 'Failed to start bulk migration')
}

// ── Autocomplete ──────────────────────────────────────────────────────────────

export async function udmSearchUsers(q = '', groupIds?: string): Promise<UserAutocompleteItem[]> {
  const { data, response } = await udmClient.GET('/api/udm/users/', {
    params: { query: { q, group_ids: groupIds } },
  })
  if (!response.ok) return []
  return (data as UserAutocompleteItem[]) || []
}

export async function udmSearchGroups(q = ''): Promise<GroupAutocompleteItem[]> {
  const { data, response } = await udmClient.GET('/api/udm/groups/', {
    params: { query: { q } },
  })
  if (!response.ok) return []
  return (data as GroupAutocompleteItem[]) || []
}

export async function udmSearchEntities(q = '', typeIds?: string, ids?: string): Promise<EntityAutocompleteItem[]> {
  const { data, response } = await udmClient.GET('/api/udm/entity-search/', {
    params: { query: { q, type_ids: typeIds, ids } },
  })
  if (!response.ok) return []
  return (data as EntityAutocompleteItem[]) || []
}

// ── Staging files ─────────────────────────────────────────────────────────────

export async function udmUploadStagingFile(
  file: File,
  intendedFieldId?: string,
  onProgress?: (p: number) => void,
): Promise<StagingFileOut> {
  const token = await getCsrfToken()
  return new Promise<StagingFileOut>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const url = intendedFieldId
      ? `/api/udm/staging-files/?intended_field_id=${intendedFieldId}`
      : '/api/udm/staging-files/'
    xhr.open('POST', url)
    xhr.withCredentials = true
    if (token) xhr.setRequestHeader('X-CSRFToken', token)
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable && e.total > 0)
        onProgress(Math.min(100, Math.round((e.loaded / e.total) * 100)))
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as StagingFileOut) }
        catch { reject(new Error('Upload response unreadable')) }
      } else {
        reject(new Error(`Upload failed: ${xhr.status}`))
      }
    }
    const fd = new FormData()
    fd.append('file', file)
    onProgress?.(0)
    xhr.send(fd)
  })
}

export async function udmDeleteStagingFile(stagingId: string): Promise<void> {
  const { error, response } = await udmClient.DELETE('/api/udm/staging-files/{staging_id}/', {
    params: { path: { staging_id: stagingId } },
  })
  if (error || !response.ok) throw new Error('Failed to delete staging file')
}

// ── Bulk migration ────────────────────────────────────────────────────────────

export async function udmGetBulkMigration(planId: string): Promise<BulkMigrationOut> {
  const { data, error, response } = await udmClient.GET('/api/udm/bulk-migrations/{plan_id}/', {
    params: { path: { plan_id: planId } },
  })
  if (error || !response.ok || !data) throw new Error('Bulk migration not found')
  return data as BulkMigrationOut
}

// ── Bundle export / import (ZIP) ───────────────────────────────────────────────

export async function udmExportBundleZip(scopeTypeIds: string[]): Promise<Blob> {
  const token = await getCsrfToken()
  const resp = await fetch('/api/udm/export-bundle-zip/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-CSRFToken': token } : {}),
    },
    body: JSON.stringify({ scope_type_ids: scopeTypeIds }),
    credentials: 'include',
  })
  if (!resp.ok) await throwRawFetchError(resp, 'Export failed')
  return resp.blob()
}

export async function udmParseBundleZip(
  file: File | Blob,
): Promise<{ scope_type_ids: string[]; udm_types: Array<{ id: string; name: string; description: string }>; error?: string }> {
  const token = await getCsrfToken()
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch('/api/udm/parse-bundle-zip/', {
    method: 'POST',
    headers: { ...(token ? { 'X-CSRFToken': token } : {}) },
    body: form,
    credentials: 'include',
  })
  if (!resp.ok) return { scope_type_ids: [], error: 'Parse failed' }
  return resp.json() as Promise<{ scope_type_ids: string[]; error?: string }>
}

export async function udmImportBundleZip(
  file: File | Blob,
  scopeTypeIds: string[],
): Promise<{ status: string; imported_workflows: number; imported_configs: number; imported_policies: number }> {
  const token = await getCsrfToken()
  const form = new FormData()
  form.append('file', file)
  form.append('scope_type_ids', scopeTypeIds.join(','))
  const resp = await fetch('/api/udm/import-bundle-zip/', {
    method: 'POST',
    headers: { ...(token ? { 'X-CSRFToken': token } : {}) },
    body: form,
    credentials: 'include',
  })
  if (!resp.ok) await throwRawFetchError(resp, 'Import failed')
  return resp.json() as Promise<{ status: string; imported_workflows: number; imported_configs: number; imported_policies: number }>
}
