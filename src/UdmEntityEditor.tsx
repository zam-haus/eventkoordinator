import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Tooltip } from 'primereact/tooltip'
import {
  udmGetEntity,
  udmGetConfigVersion,
  udmValidateEntity,
  udmValidateTransition,
  udmPatchEntity,
  udmTransitionEntity,
  udmEntityHistory,
  udmListTypes,
  udmSearchEntities,
  udmCanCreateEntity,
  UdmApiError,
  type EntityOut,
  type ConfigVersionOut,
  type FieldDefinitionOut,
  type WorkflowTransitionOut,
  type WorkflowVersionOut,
  type EditHistoryOut,
  type PolicyMessage,
  type ValidationResult,
  type UDMTypeOut,
  type EntityAutocompleteItem,
} from './apiUdm'
import { MigrationAssistant } from './UdmMigration'
import { FieldInput, getLang, PolicyMessageList } from './udm-editors'
import styles from './UdmEntityEditor.module.css'
import dsStyles from './DefaultScreen.module.css'

// ── Helpers ───────────────────────────────────────────────────────────────────

function getFieldValue(entity: EntityOut, slug: string, lang = ''): unknown {
  const fv = entity.field_values.find(v => v.field_slug === slug && v.language === lang)
  return fv?.value ?? null
}

function getAllLangValues(entity: EntityOut, slug: string): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  entity.field_values
    .filter(v => v.field_slug === slug)
    .forEach(v => { result[v.language] = v.value })
  return result
}

// ── Severity helpers ──────────────────────────────────────────────────────────

const SEVERITY_ORDER = ['success', 'info', 'warning', 'error', 'critical']

const SEVERITY_ICON: Record<string, string> = {
  critical: 'pi-times-circle',
  error:    'pi-times-circle',
  warning:  'pi-exclamation-circle',
  info:     'pi-info-circle',
  success:  'pi-check-circle',
}

const SEVERITY_CLASS: Record<string, string> = {
  critical: styles.severityIconCritical,
  error:    styles.severityIconError,
  warning:  styles.severityIconWarning,
  info:     styles.severityIconInfo,
  success:  styles.severityIconSuccess,
}

function formatPolicyMessages(msgs: PolicyMessage[], fieldLabelMap?: Record<string, string>): React.ReactNode {
  return (
    <ul style={{ margin: 0, padding: '0 0 0 1rem', fontSize: '0.8rem', maxWidth: '280px' }}>
      {msgs.map((m, i) => {
        const topSlugs = [...new Set((m.highlight_fields ?? []).map(p => p.split('.')[0]))]
        const fieldLabels = topSlugs.map(s => fieldLabelMap?.[s] ?? s)
        return (
          <li key={i}>
            {fieldLabels.length > 0 && <strong>{fieldLabels.join(', ')}: </strong>}
            {m.text}
          </li>
        )
      })}
    </ul>
  )
}

interface SeverityIndicatorProps {
  severity: string
  messages: PolicyMessage[]
  fieldSlug: string
}

function SeverityIndicator({ severity, messages, fieldSlug }: SeverityIndicatorProps) {
  const iconClass = SEVERITY_ICON[severity] ?? 'pi-info-circle'
  const colorClass = SEVERITY_CLASS[severity] ?? styles.severityIconInfo
  const targetId = `udm-sev-${fieldSlug.replace(/[^a-z0-9]/gi, '-')}`
  return (
    <>
      <Tooltip target={`#${targetId}`} position="right">
        <ul style={{ margin: 0, padding: '0 0 0 1rem', fontSize: '0.8rem', maxWidth: '260px' }}>
          {messages.map((m, i) => <li key={i}>{m.text}</li>)}
        </ul>
      </Tooltip>
      <i id={targetId} className={`pi ${iconClass} ${styles.severityIcon} ${colorClass}`} />
    </>
  )
}

// ── Workflow field widget ─────────────────────────────────────────────────────

interface WorkflowFieldWidgetProps {
  fd: FieldDefinitionOut
  entity: EntityOut
  uiLang: string
  onTransition: (fieldSlug: string, transitionName: string) => Promise<void>
  transitioning: boolean
  messages?: PolicyMessage[]
  severity?: string
  compact?: boolean
  fieldLabelMap?: Record<string, string>
}

function WorkflowFieldWidget({ fd, entity, uiLang, onTransition, transitioning, messages, severity, compact, fieldLabelMap }: WorkflowFieldWidgetProps) {
  const wfDef = (fd as FieldDefinitionOut & { workflow_version?: WorkflowVersionOut | null }).workflow_version
  const fv = entity.field_values.find(v => v.field_slug === fd.slug)
  const currentStateName = (fv?.value as string | null) ?? null

  const label = getLang(fd.label as Record<string, string>, uiLang) || fd.slug
  const helpText = getLang(fd.help_text as Record<string, string>, uiLang)

  const currentState = wfDef?.states.find(s => s.name === currentStateName) ?? null
  const stateLabel = currentState
    ? getLang(currentState.label as Record<string, string>, uiLang) || currentStateName
    : currentStateName

  // Mirror engine.py transition gate exactly
  const availableTransitions: WorkflowTransitionOut[] = (wfDef?.transitions ?? []).filter(t => {
    if (t.from_undefined_only) return currentStateName === null
    if (t.from_state !== null) return t.from_state === currentStateName
    return true // from_state null, not from_undefined_only → always available
  })

  const [transitionValidations, setTransitionValidations] = useState<Record<string, ValidationResult>>({})

  useEffect(() => {
    if (availableTransitions.length === 0) { setTransitionValidations({}); return }
    let cancelled = false
    Promise.all(
      availableTransitions.map(t =>
        udmValidateTransition(entity.id, fd.slug, t.name)
          .then(result => ({ name: t.name, result }))
          .catch(() => ({ name: t.name, result: { valid: true, policy_messages: [], errors: {} } as ValidationResult }))
      )
    ).then(results => {
      if (cancelled) return
      const map: Record<string, ValidationResult> = {}
      for (const { name, result } of results) map[name] = result
      setTransitionValidations(map)
    })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity.id, entity.updated_at, fd.slug])


  if (compact) {
    const compactHighlight = (() => {
      if (!severity) return ''
      if (severity === 'error' || severity === 'critical') return styles.fieldGroupCompactError
      if (severity === 'warning') return styles.fieldGroupCompactWarning
      return styles.fieldGroupCompactInfo
    })()
    return (
      <div className={`${styles.fieldGroupCompact} ${compactHighlight}`}>
        <div className={styles.compactHeader}>
          {severity && messages && messages.length > 0 && (
            <SeverityIndicator severity={severity} messages={messages} fieldSlug={fd.slug} />
          )}
          <span className={styles.compactLabel}>{label}</span>
        </div>
        {helpText && <div className={styles.compactHelp}>{helpText}</div>}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{
            display: 'inline-block',
            padding: '0.15rem 0.5rem',
            borderRadius: '4px',
            fontSize: '0.82rem',
            fontWeight: 600,
            background: currentState?.background_color && currentState.background_color !== '#ffffff' ? currentState.background_color : (currentStateName ? '#dbeafe' : '#f1f5f9'),
            color: currentState?.background_color && currentState.background_color !== '#ffffff' ? currentState.text_color : (currentStateName ? '#1d4ed8' : '#64748b'),
            border: '1px solid',
            borderColor: currentState?.background_color && currentState.background_color !== '#ffffff' ? currentState.background_color : (currentStateName ? '#93c5fd' : '#cbd5e1'),
          }}>
            {stateLabel ?? '(no state)'}
          </span>
          {availableTransitions.map(t => {
            const tLabel = getLang(t.label as Record<string, string>, uiLang) || t.name
            const validation = transitionValidations[t.name]
            const isBlocked = validation?.valid === false
            const blockMsgs = isBlocked ? (validation.policy_messages ?? []) : []
            const spanId = `wf-trans-${fd.slug.replace(/[^a-z0-9]/gi, '-')}-${t.name.replace(/[^a-z0-9]/gi, '-')}`
            return (
              <span key={t.name} id={spanId} style={{ display: 'inline-block' }}>
                {isBlocked && blockMsgs.length > 0 && (
                  <Tooltip target={`#${spanId}`} position="top">{formatPolicyMessages(blockMsgs, fieldLabelMap)}</Tooltip>
                )}
                <button type="button" className={styles.tabNavButton}
                  disabled={transitioning || isBlocked}
                  onClick={() => void onTransition(fd.slug, t.name)}>
                  → {tLabel}
                </button>
              </span>
            )
          })}
          {availableTransitions.length === 0 && (
            <span style={{ fontSize: '0.78rem', color: '#aaa', fontStyle: 'italic' }}>No transitions available</span>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={styles.fieldGroup}>
      <div className={styles.fieldHeader}>
        <div>
          <div className={styles.fieldLabel}>{label}</div>
          <div className={styles.fieldSlug}>{fd.slug} · workflow</div>
          {helpText && <div className={styles.fieldHelp}>{helpText}</div>}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        <span style={{
          display: 'inline-block',
          padding: '0.2rem 0.6rem',
          borderRadius: '4px',
          fontSize: '0.85rem',
          fontWeight: 600,
          background: currentState?.background_color && currentState.background_color !== '#ffffff' ? currentState.background_color : (currentStateName ? '#dbeafe' : '#f1f5f9'),
          color: currentState?.background_color && currentState.background_color !== '#ffffff' ? currentState.text_color : (currentStateName ? '#1d4ed8' : '#64748b'),
          border: '1px solid',
          borderColor: currentState?.background_color && currentState.background_color !== '#ffffff' ? currentState.background_color : (currentStateName ? '#93c5fd' : '#cbd5e1'),
        }}>
          {stateLabel ?? '(no state)'}
        </span>
        {availableTransitions.map(t => {
          const tLabel = getLang(t.label as Record<string, string>, uiLang) || t.name
          const validation = transitionValidations[t.name]
          const isBlocked = validation?.valid === false
          const blockMsgs = isBlocked ? (validation.policy_messages ?? []) : []
          const spanId = `wf-trans-${fd.slug.replace(/[^a-z0-9]/gi, '-')}-${t.name.replace(/[^a-z0-9]/gi, '-')}`
          return (
            <span key={t.name} id={spanId} style={{ display: 'inline-block' }}>
              {isBlocked && blockMsgs.length > 0 && (
                <Tooltip target={`#${spanId}`} position="top">{formatPolicyMessages(blockMsgs, fieldLabelMap)}</Tooltip>
              )}
              <button type="button" className={styles.tabNavButton}
                disabled={transitioning || isBlocked}
                onClick={() => void onTransition(fd.slug, t.name)}>
                → {tLabel}
              </button>
            </span>
          )
        })}
        {availableTransitions.length === 0 && (
          <span style={{ fontSize: '0.82rem', color: '#888', fontStyle: 'italic' }}>No transitions available</span>
        )}
      </div>
      {messages && messages.length > 0 && (
        <div style={{ marginTop: '0.5rem' }}>
          <PolicyMessageList messages={messages} />
        </div>
      )}
    </div>
  )
}

// ── Field row ─────────────────────────────────────────────────────────────────

interface FieldRowProps {
  fd: FieldDefinitionOut
  entity: EntityOut
  dirty: Record<string, unknown>
  onDirty: (slug: string, val: unknown) => void
  onReset: (slug: string) => void
  editable: boolean
  languages: string[]
  uiLang: string
  severity?: string
  messages?: PolicyMessage[]
  subFieldSeverities?: Record<string, string>
  subFieldMessages?: Record<string, PolicyMessage[]>
  onTransition: (fieldSlug: string, transitionName: string) => Promise<void>
  transitioning: boolean
  resetKey?: number
  onEntityRefresh?: (policyMessages?: PolicyMessage[]) => void | Promise<void>
  compact?: boolean
  fieldLabelMap?: Record<string, string>
}

function FieldRow({ fd, entity, dirty, onDirty, onReset, editable, languages, uiLang, severity, messages, subFieldSeverities, subFieldMessages, onTransition, transitioning, resetKey, onEntityRefresh, compact, fieldLabelMap }: FieldRowProps) {
  const [activeLang, setActiveLang] = useState(languages[0] ?? '')
  const isDirty = fd.slug in dirty
  const isSubmodel = fd.data_type === 'submodel_list' || fd.data_type === 'submodel_select'

  // Workflow fields are fully managed by WorkflowFieldWidget — no dirty/value editing
  if (fd.data_type === 'workflow') {
    return <WorkflowFieldWidget fd={fd} entity={entity} uiLang={uiLang} onTransition={onTransition} transitioning={transitioning} messages={messages} severity={severity} compact={compact} fieldLabelMap={fieldLabelMap} />
  }
  const label = getLang(fd.label as Record<string, string>, uiLang) || fd.slug
  const helpText = getLang(fd.help_text as Record<string, string>, uiLang)

  function getVal(lang = '') {
    if (isDirty) {
      const d = dirty[fd.slug]
      if (fd.is_localized && typeof d === 'object' && d !== null)
        return (d as Record<string, unknown>)[lang]
      return d
    }
    return getFieldValue(entity, fd.slug, lang)
  }

  function handleChange(lang: string, val: unknown) {
    if (isSubmodel) {
      // submodel ops passed directly — no localized wrapping
      onDirty(fd.slug, val)
      return
    }
    if (fd.is_localized) {
      const existing = isDirty && typeof dirty[fd.slug] === 'object' && dirty[fd.slug] !== null
        ? (dirty[fd.slug] as Record<string, unknown>)
        : getAllLangValues(entity, fd.slug)
      onDirty(fd.slug, { ...existing, [lang]: val })
    } else {
      onDirty(fd.slug, val)
    }
  }

  // For submodel_list: isDirty shows whether ops are pending; value is always from entity.children
  const submodelHasChanges = isSubmodel && isDirty && (() => {
    const v = dirty[fd.slug]
    if (Array.isArray(v)) return v.length > 0
    if (v && typeof v === 'object') return true
    return v !== null && v !== undefined
  })()

  const fieldIsDirty = (isDirty && !isSubmodel) || submodelHasChanges
  const highlightClass = (() => {
    if (!severity) return fieldIsDirty ? styles.fieldGroupDirty : ''
    if (severity === 'error' || severity === 'critical') return styles.fieldGroupError
    if (fieldIsDirty) return styles.fieldGroupDirty
    if (severity === 'warning') return styles.fieldGroupWarning
    return styles.fieldGroupInfo
  })()

  if (compact) {
    const compactHighlight = (() => {
      if (severity === 'error' || severity === 'critical') return styles.fieldGroupCompactError
      if (fieldIsDirty) return styles.fieldGroupCompactDirty
      if (severity === 'warning') return styles.fieldGroupCompactWarning
      if (severity === 'info') return styles.fieldGroupCompactInfo
      return ''
    })()
    const hasMessages = messages && messages.length > 0
    return (
      <div className={`${styles.fieldGroupCompact} ${compactHighlight}`}>
        <div className={styles.compactHeader}>
          {severity && hasMessages && (
            <SeverityIndicator severity={severity} messages={messages!} fieldSlug={fd.slug} />
          )}
          <span className={styles.compactLabel}>{label}</span>
          {(isDirty && !isSubmodel) && (
            <button type="button" className={styles.resetBtn} style={{ marginLeft: 'auto', fontSize: '0.72rem' }}
              onClick={() => onReset(fd.slug)}>
              Reset
            </button>
          )}
        </div>
        {helpText && <div className={styles.compactHelp}>{helpText}</div>}
        {!isSubmodel && fd.is_localized && languages.length > 1 && (
          <div className={styles.langTabs} style={{ marginBottom: '0.2rem' }}>
            {languages.map(l => (
              <button key={l} type="button"
                className={`${styles.langTab} ${activeLang === l ? styles.langTabActive : ''}`}
                onClick={() => setActiveLang(l)}>
                {l}
              </button>
            ))}
          </div>
        )}
        {isSubmodel ? (
          <FieldInput fd={fd} value={getFieldValue(entity, fd.slug, '')} onChange={val => handleChange('', val)}
            disabled={!editable} lang={uiLang} entityChildren={entity.children as Record<string, unknown[]>}
            subFieldSeverities={subFieldSeverities} subFieldMessages={subFieldMessages}
            resetKey={resetKey} onEntityRefresh={onEntityRefresh} compact={compact} />
        ) : fd.is_localized ? (
          <FieldInput fd={fd} value={getVal(activeLang)} onChange={val => handleChange(activeLang, val)}
            disabled={!editable} lang={activeLang} />
        ) : (
          <FieldInput fd={fd} value={getVal()} onChange={val => handleChange('', val)} disabled={!editable} />
        )}
      </div>
    )
  }

  return (
    <div className={`${styles.fieldGroup} ${highlightClass}`}>
      <div className={styles.fieldHeader}>
        <div>
          <div className={styles.fieldLabel}>{label}</div>
          <div className={styles.fieldSlug}>{fd.slug} · {fd.data_type}</div>
          {helpText && <div className={styles.fieldHelp}>{helpText}</div>}
        </div>
        {(isDirty && !isSubmodel) && (
          <div className={styles.fieldActions}>
            <button type="button" className={styles.resetBtn} onClick={() => onReset(fd.slug)}>
              Reset
            </button>
          </div>
        )}
      </div>

      {!isSubmodel && fd.is_localized && languages.length > 1 && (
        <div className={styles.langTabs}>
          {languages.map(l => (
            <button key={l} type="button"
              className={`${styles.langTab} ${activeLang === l ? styles.langTabActive : ''}`}
              onClick={() => setActiveLang(l)}>
              {l}
            </button>
          ))}
        </div>
      )}

      {isSubmodel ? (
        // Submodels always receive full entity.children context; value = FK UUID for submodel_select
        <FieldInput
          fd={fd}
          value={getFieldValue(entity, fd.slug, '')}
          onChange={val => handleChange('', val)}
          disabled={!editable}
          lang={uiLang}
          entityChildren={entity.children as Record<string, unknown[]>}
          subFieldSeverities={subFieldSeverities}
          subFieldMessages={subFieldMessages}
          resetKey={resetKey}
          onEntityRefresh={onEntityRefresh}
        />
      ) : fd.is_localized ? (
        <FieldInput
          fd={fd}
          value={getVal(activeLang)}
          onChange={val => handleChange(activeLang, val)}
          disabled={!editable}
          lang={activeLang}
        />
      ) : (
        <FieldInput
          fd={fd}
          value={getVal()}
          onChange={val => handleChange('', val)}
          disabled={!editable}
        />
      )}
      {messages && messages.length > 0 && <PolicyMessageList messages={messages} />}
    </div>
  )
}

// ── Transition message popup ──────────────────────────────────────────────────

function TransitionMessagePopup({ messages, onClose }: { messages: PolicyMessage[]; onClose: () => void }) {
  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: '#fff', borderRadius: '8px', padding: '1.5rem',
          maxWidth: '440px', width: '90%', boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>Transition messages</span>
          <button
            type="button"
            onClick={onClose}
            style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1.2rem', color: '#888', lineHeight: 1, padding: '0 0.2rem' }}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <PolicyMessageList messages={messages} />
        <div style={{ marginTop: '1rem', textAlign: 'right' }}>
          <button
            type="button"
            onClick={onClose}
            style={{ padding: '0.4rem 1rem', background: '#f5f5f5', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer', fontSize: '0.875rem' }}
          >
            OK
          </button>
        </div>
      </div>
    </div>
  )
}

// ── History panel helpers ─────────────────────────────────────────────────────

type PolicyActionValue = Record<string, unknown>

function renderPolicyAction(kind: string, newValue: unknown): React.ReactNode {
  const phase = kind === 'policy_pre_action' ? 'pre' : 'post'
  const act = newValue as PolicyActionValue | null

  const phaseBadge = (
    <span style={{
      fontFamily: 'monospace', fontSize: '0.72rem',
      background: '#e8f0fe', borderRadius: '3px',
      padding: '0.05rem 0.3rem', marginRight: '0.35rem', color: '#1a56db',
    }}>
      {phase}
    </span>
  )

  if (!act) {
    return <span style={{ color: '#777' }}>{phaseBadge}⚙ system action</span>
  }

  const error = act._error as string | undefined
  const type = act.type as string
  let detail: React.ReactNode

  switch (type) {
    case 'send_notification': {
      const dest = act.template_name || act.subject || '—'
      const to = act.recipient_field
        ? ` → ${act.recipient_field}`
        : (act.extra_recipients as string[] | undefined)?.length
          ? ` → ${(act.extra_recipients as string[]).join(', ')}`
          : ''
      detail = <>send notification · <em>{dest as string}</em>{to}</>
      break
    }
    case 'set_field_value':
      detail = <>set <strong>{act.field_path as string}</strong> = {JSON.stringify(act.value)}</>
      break
    case 'trigger_transition': {
      const scope = act.target_scope !== 'self' ? ` (${act.target_scope})` : ''
      detail = <>trigger <strong>{act.field_slug as string}</strong> / <em>{act.transition_name as string}</em>{scope}</>
      break
    }
    case 'create_submodel_item':
      detail = <>create item in <strong>{act.field_slug as string}</strong></>
      break
    default:
      detail = <>{type}</>
  }

  return (
    <span style={{ color: error ? '#c0392b' : 'inherit' }}>
      {phaseBadge}⚙ {detail}
      {error ? <span style={{ marginLeft: '0.5rem' }}>✗ {error}</span> : null}
    </span>
  )
}

// ── History panel ─────────────────────────────────────────────────────────────

function HistoryPanel({ entityId }: { entityId: string }) {
  const [history, setHistory] = useState<EditHistoryOut | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    udmEntityHistory(entityId)
      .then(h => setHistory(h))
      .catch(() => setHistory(null))
      .finally(() => setLoading(false))
  }, [entityId])

  if (loading) return <div>Loading history…</div>
  if (!history || history.results.length === 0)
    return <div style={{ color: '#888', fontSize: '0.875rem' }}>No edit history yet.</div>

  return (
    <div>
      {history.results.map(group => (
        <div key={group.id} className={styles.historyGroup}>
          <div className={styles.historyMeta}>
            {new Date(group.saved_at).toLocaleString()}{' '}
            {group.saved_by ? `by ${group.saved_by.display_name}` : ''}
            {' · '}{group.node_type}
          </div>
          {group.edits.map((edit, i) => (
            <div key={i} className={styles.historyEdit}>
              {edit.change_kind === 'field_value' ? (
                <span>
                  <strong>{edit.field_label ?? edit.field_slug}</strong>
                  {edit.language ? (
                    <span style={{ fontFamily: 'monospace', fontSize: '0.72rem', background: '#f0f0f0', borderRadius: '3px', padding: '0.05rem 0.3rem', marginLeft: '0.3rem', color: '#555' }}>
                      {edit.language}
                    </span>
                  ) : null}
                  {': '}
                  {edit.old_file_name
                    ? <>{edit.old_file_name} → {edit.new_file_name ?? '—'}</>
                    : <>{JSON.stringify(edit.old_value)} → {JSON.stringify(edit.new_value)}</>
                  }
                </span>
              ) : edit.change_kind === 'node_transition' ? (
                <span>
                  <strong>{edit.field_label ?? edit.field_slug}</strong>:{' '}
                  {(edit.old_value as Record<string, unknown> | null)?.state as string ?? '—'}
                  {' → '}
                  {(edit.new_value as Record<string, unknown> | null)?.state as string ?? '—'}
                </span>
              ) : edit.change_kind === 'node_added' ? (
                <span>+ <strong>{edit.field_label ?? edit.field_slug}</strong> item added</span>
              ) : edit.change_kind === 'node_removed' ? (
                <span>− <strong>{edit.field_label ?? edit.field_slug}</strong> item removed</span>
              ) : edit.change_kind === 'node_reordered' ? (
                <span>
                  <strong>{edit.field_label ?? edit.field_slug}</strong> reordered:{' '}
                  {(edit.old_value as Record<string, unknown> | null)?.sort_order as number}
                  {' → '}
                  {(edit.new_value as Record<string, unknown> | null)?.sort_order as number}
                </span>
              ) : (edit.change_kind === 'policy_pre_action' || edit.change_kind === 'policy_post_action') ? (
                renderPolicyAction(edit.change_kind, edit.new_value)
              ) : (
                <span>{edit.change_kind}</span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

// ── Main entity editor ────────────────────────────────────────────────────────

export function UdmEntityEditor() {
  const { entityId } = useParams<{ entityId: string }>()
  const navigate = useNavigate()
  const { i18n } = useTranslation()

  const [entity, setEntity] = useState<EntityOut | null>(null)
  const [config, setConfig] = useState<ConfigVersionOut | null>(null)
  const [dirty, setDirty] = useState<Record<string, unknown>>({})
  const [discardCount, setDiscardCount] = useState(0)
  const [saving, setSaving] = useState(false)
  const [transitioning, setTransitioning] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [success, setSuccess] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [policyMessages, setPolicyMessages] = useState<PolicyMessage[]>([])
  const [transitionPopup, setTransitionPopup] = useState<PolicyMessage[]>([])
  const [compact, setCompact] = useState(false)
  const [activeTab, setActiveTab] = useState(0)
  const [validationPending, setValidationPending] = useState(false)

  const fieldSeverities = useMemo(() => {
    const out: Record<string, string> = {}
    for (const m of policyMessages) {
      for (const p of m.highlight_fields ?? []) {
        const slug = p.split('.')[0]
        if (!out[slug] || SEVERITY_ORDER.indexOf(m.level) > SEVERITY_ORDER.indexOf(out[slug]))
          out[slug] = m.level
      }
    }
    return out
  }, [policyMessages])

  const subFieldSeverities = useMemo(() => {
    const out: Record<string, Record<string, string>> = {}
    for (const m of policyMessages) {
      for (const p of m.highlight_fields ?? []) {
        const dot = p.indexOf('.')
        if (dot === -1) continue
        const parent = p.slice(0, dot); const child = p.slice(dot + 1)
        const parentMap = (out[parent] ??= {})
        if (!parentMap[child] || SEVERITY_ORDER.indexOf(m.level) > SEVERITY_ORDER.indexOf(parentMap[child]))
          parentMap[child] = m.level
      }
    }
    return out
  }, [policyMessages])

  // Messages keyed by top-level slug (for rendering below each FieldRow)
  const fieldMessages = useMemo(() => {
    const out: Record<string, PolicyMessage[]> = {}
    for (const m of policyMessages)
      for (const p of m.highlight_fields ?? []) {
        const slug = p.split('.')[0]
        ;(out[slug] ??= []).includes(m) || out[slug].push(m)
      }
    return out
  }, [policyMessages])

  // Messages with no field assignment — shown near the save button
  const globalPolicyMessages = useMemo(
    () => policyMessages.filter(m => !m.highlight_fields?.length),
    [policyMessages],
  )

  // Messages keyed by parent slug → child slug (for rendering below sub-fields)
  const subFieldMessages = useMemo(() => {
    const out: Record<string, Record<string, PolicyMessage[]>> = {}
    for (const m of policyMessages)
      for (const p of m.highlight_fields ?? []) {
        const dot = p.indexOf('.')
        if (dot === -1) continue
        const parent = p.slice(0, dot); const child = p.slice(dot + 1)
        ;((out[parent] ??= {})[child] ??= []).push(m)
      }
    return out
  }, [policyMessages])

  const uiLang = i18n.language.split('-')[0]

  const fieldLabelMap = useMemo(() => {
    const out: Record<string, string> = {}
    for (const fd of config?.fields ?? []) {
      out[fd.slug] = getLang(fd.label as Record<string, string>, uiLang) || fd.slug
    }
    return out
  }, [config?.fields, uiLang])

  const load = useCallback(async () => {
    if (!entityId) return
    try {
      const e = await udmGetEntity(entityId)
      setEntity(e)
      // Load the entity's ACTUAL pinned config version (not the type's current
      // published config). This keeps the form aligned with the stored data even
      // when the entity is stuck on an archived version awaiting migration.
      const cfg = await udmGetConfigVersion(e.config_version_id)
      setConfig(cfg)
      // Run save policy with no pending changes to surface ambient warnings
      // (rules that inspect input.entity.fields rather than input.changed_fields).
      try {
        const validation = await udmValidateEntity(entityId, {})
        setPolicyMessages(validation.policy_messages ?? [])
      } catch { /* validation is best-effort */ }
    } catch (err) {
      if (err instanceof UdmApiError && err.policyMessages.length > 0) {
        setPolicyMessages(err.policyMessages)
      }
      setErrors([err instanceof Error ? err.message : 'Failed to load entity'])
    }
  }, [entityId])

  useEffect(() => { void load() }, [load])

  const pendingValidation = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Set to true after save/transition so the useEffect skips overwriting server messages for one cycle
  const skipAmbientValidation = useRef(false)

  useEffect(() => {
    if (!entityId) return
    if (Object.keys(dirty).length === 0) {
      if (skipAmbientValidation.current) {
        skipAmbientValidation.current = false
        return  // keep server-response messages after save/transition
      }
      // Re-run ambient validation to keep messages current (e.g. after discard)
      void udmValidateEntity(entityId, {})
        .then(r => setPolicyMessages(r.policy_messages ?? []))
        .catch(() => {})
      return
    }
    if (pendingValidation.current) clearTimeout(pendingValidation.current)
    setValidationPending(true)
    pendingValidation.current = setTimeout(async () => {
      try {
        const result = await udmValidateEntity(entityId, dirty)
        setPolicyMessages(result.policy_messages ?? [])
      } catch {
        // Validation is best-effort — ignore lock conflicts and network errors
      } finally {
        setValidationPending(false)
      }
    }, 600)
    return () => {
      if (pendingValidation.current) clearTimeout(pendingValidation.current)
    }
  }, [dirty, entityId])

  if (!entity || !entityId) {
    return (
      <div className={styles.page}>
        {errors.length > 0
          ? <PolicyMessageList messages={errors.map(m => ({ level: 'error' as const, text: m }))} />
          : <div>Loading…</div>}
        {policyMessages.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <PolicyMessageList messages={policyMessages} />
          </div>
        )}
      </div>
    )
  }
  // entityId is now narrowed to string (not undefined) below this point
  const resolvedEntityId: string = entityId

  // An entity pinned to a non-published (archived) config version is read-only
  // until it is migrated to the current version.
  const isArchived = config?.status === 'archived'

  // Determine editability: archived overrides everything; otherwise defer to
  // the per-field editable_fields list returned by the policy.
  const editable = !isArchived
  const editableFieldSlugs: Set<string> | null =
    entity.editable_fields != null ? new Set(entity.editable_fields) : null

  const languages = (config?.languages ?? []).map(l => l.code)
  if (languages.length === 0) languages.push('')

  const allFields = config?.fields ?? []
  const viewableFieldSlugs = entity.viewable_fields ? new Set(entity.viewable_fields) : null
  const fields = viewableFieldSlugs
    ? allFields.filter(fd => viewableFieldSlugs.has(fd.slug))
    : allFields

  function handleDirty(slug: string, val: unknown) {
    setDirty(prev => ({ ...prev, [slug]: val }))
    setSuccess(null)
  }

  function handleReset(slug: string) {
    setDirty(prev => {
      const n = { ...prev }
      delete n[slug]
      return n
    })
  }

  async function handleSave() {
    if (Object.keys(dirty).length === 0) return
    if (pendingValidation.current) clearTimeout(pendingValidation.current)
    setSaving(true)
    setErrors([])
    setSuccess(null)
    try {
      const updated = await udmPatchEntity(resolvedEntityId, dirty)
      setEntity(updated)
      skipAmbientValidation.current = true
      setDirty({})
      setPolicyMessages((updated.policy_messages ?? []) as PolicyMessage[])
      setSuccess('Saved successfully.')
    } catch (e) {
      if (e instanceof UdmApiError) {
        const plainErrors: string[] = [
          ...e.pydanticErrors.map(err => {
            const loc = err.loc.filter(s => s !== 'body' && s !== 'payload').join(' → ')
            return loc ? `${loc}: ${err.msg}` : err.msg
          }),
          ...Object.entries(e.fieldErrors).flatMap(([field, errs]) =>
            errs.map(err => (field === '__all__' ? err : `${field}: ${err}`)),
          ),
        ]
        // Fall back to the raw message only when there is no structured data at all
        if (plainErrors.length === 0 && e.policyMessages.length === 0) plainErrors.push(e.message)
        setErrors(plainErrors)
        setPolicyMessages(e.policyMessages)
      } else {
        setErrors([e instanceof Error ? e.message : 'Save failed'])
      }
    } finally {
      setSaving(false)
    }
  }

  async function handleTransition(fieldSlug: string, transitionName: string) {
    setTransitioning(true)
    setErrors([])
    setSuccess(null)
    try {
      const updated = await udmTransitionEntity(resolvedEntityId, fieldSlug, transitionName, dirty)
      skipAmbientValidation.current = true
      setDirty({})
      const globalMsgs = ((updated.policy_messages ?? []) as PolicyMessage[]).filter((m: PolicyMessage) => !m.highlight_fields?.length)
      if (globalMsgs.length > 0) {
        setTransitionPopup(globalMsgs)
      } else {
        setSuccess(`Transition applied: ${transitionName}`)
      }
      await load()
    } catch (e) {
      if (e instanceof UdmApiError) {
        const globalMsgs = e.policyMessages.filter(m => !m.highlight_fields?.length)
        if (globalMsgs.length > 0) {
          setTransitionPopup(globalMsgs)
        } else {
          const plainErrors: string[] = [
            ...e.pydanticErrors.map(err => {
              const loc = err.loc.filter(s => s !== 'body' && s !== 'payload').join(' → ')
              return loc ? `${loc}: ${err.msg}` : err.msg
            }),
            ...Object.entries(e.fieldErrors).flatMap(([field, errs]) =>
              errs.map(err => (field === '__all__' ? err : `${field}: ${err}`)),
            ),
          ]
          if (plainErrors.length === 0 && e.policyMessages.length === 0) plainErrors.push(e.message)
          setErrors(plainErrors)
          setPolicyMessages(e.policyMessages)
        }
      } else {
        setErrors([e instanceof Error ? e.message : 'Transition failed'])
      }
    } finally {
      setTransitioning(false)
    }
  }

  const dirtyCount = Object.keys(dirty).length
  const hasBlockingMessages = policyMessages.some(m => m.level === 'error' || m.level === 'critical')
  const saveDisabled = saving || dirtyCount === 0 || !editable || validationPending || hasBlockingMessages

  // ── Layout parsing ──────────────────────────────────────────────────────────
  const STRUCTURAL = new Set(['tab_container', 'tab', 'save_button', 'hstack', 'hstack_group', 'tab_prev', 'tab_next'])
  const sortedFields = [...fields].sort((a, b) => a.sort_order - b.sort_order)
  const tabContainerField = sortedFields.find(f => f.data_type === 'tab_container') ?? null
  const tabFields = sortedFields.filter(f => f.data_type === 'tab')
  const hasTabs = tabContainerField !== null && tabFields.length > 0

  // Only root fields (no parent_slug) go into above/below — children are pulled by their parent containers
  const aboveFields = hasTabs
    ? sortedFields.filter(f => !f.parent_slug && f.sort_order < tabContainerField!.sort_order && f.data_type !== 'tab_container' && f.data_type !== 'tab')
    : sortedFields.filter(f => !f.parent_slug && f.data_type !== 'tab_container' && f.data_type !== 'tab')

  const tabsWithFields = hasTabs
    ? tabFields.map(tab => ({
        tab,
        fields: sortedFields.filter(f => f.parent_slug === tab.slug && f.data_type !== 'tab'),
      }))
    : []

  const belowFields = hasTabs
    ? sortedFields.filter(f => !f.parent_slug && f.sort_order > tabContainerField!.sort_order && f.data_type !== 'tab_container' && f.data_type !== 'tab')
    : []

  // Check if any save button exists in the config (inline save buttons suppress the toolbar save)
  const hasSaveInConfig = sortedFields.some(f => f.data_type === 'save_button')

  function getTabTitle(tab: (typeof sortedFields)[0]): string {
    const tc = tab.type_config as { title?: string } | undefined
    return tc?.title || tab.slug
  }

  // ── Inline structural field renderer ───────────────────────────────────────
  const onEntityRefreshCb = async (msgs?: PolicyMessage[]) => {
    await load()
    const globalMsgs = (msgs ?? []).filter((m: PolicyMessage) => !m.highlight_fields?.length)
    if (globalMsgs.length > 0) setTransitionPopup(globalMsgs)
  }

  function renderFieldRow(fd: (typeof sortedFields)[0]) {
    if (STRUCTURAL.has(fd.data_type)) return null  // structural fields rendered elsewhere
    return (
      <FieldRow
        key={fd.slug}
        fd={fd}
        entity={entity!}
        dirty={dirty}
        onDirty={handleDirty}
        onReset={handleReset}
        editable={editable && (editableFieldSlugs == null || editableFieldSlugs.has(fd.slug))}
        languages={fd.is_localized ? languages.filter(Boolean) : ['']}
        uiLang={uiLang}
        severity={fieldSeverities[fd.slug]}
        messages={fieldMessages[fd.slug]}
        subFieldSeverities={subFieldSeverities[fd.slug]}
        subFieldMessages={subFieldMessages[fd.slug]}
        onTransition={handleTransition}
        transitioning={transitioning}
        fieldLabelMap={fieldLabelMap}
        resetKey={discardCount}
        compact={compact}
        onEntityRefresh={onEntityRefreshCb}
      />
    )
  }

  function renderToolbarSaveButton() {
    const blockingMsgs = policyMessages.filter(m => m.level === 'error' || m.level === 'critical')
    const showTooltip = hasBlockingMessages && blockingMsgs.length > 0
    return (
      <span id="save-btn-toolbar" style={{ display: 'inline-block' }}>
        {showTooltip && (
          <Tooltip target="#save-btn-toolbar" position="top">{formatPolicyMessages(blockingMsgs, fieldLabelMap)}</Tooltip>
        )}
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
          onClick={() => void handleSave()} disabled={saveDisabled}>
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </span>
    )
  }

  function renderSaveButton(label?: string, variant?: string) {
    const isSuccess = variant === 'success'
    const blockingMsgs = policyMessages.filter(m => m.level === 'error' || m.level === 'critical')
    const showTooltip = hasBlockingMessages && blockingMsgs.length > 0
    return (
      <span id="save-btn-inline" style={{ display: 'inline-block' }}>
        {showTooltip && (
          <Tooltip target="#save-btn-inline" position="top">{formatPolicyMessages(blockingMsgs, fieldLabelMap)}</Tooltip>
        )}
        <button
          type="button"
          className={`${styles.inlineBtn} ${isSuccess ? styles.inlineBtnSuccess : styles.inlineBtnPrimary}`}
          onClick={() => void handleSave()}
          disabled={saveDisabled}
        >
          {saving ? 'Saving…' : (label || 'Save')}
        </button>
      </span>
    )
  }

  // Build child map from all sortedFields for hstack rendering
  const childMap = new Map<string, typeof sortedFields>()
  for (const f of sortedFields) {
    if (f.parent_slug) {
      ;(childMap.get(f.parent_slug) ?? (childMap.set(f.parent_slug, []), childMap.get(f.parent_slug)!)).push(f)
    }
  }

  function renderHstackGroupButton(fd: (typeof sortedFields)[0]) {
    if (fd.data_type === 'save_button') {
      const tc = fd.type_config as { label?: string; variant?: string } | undefined
      return <span key={fd.slug}>{renderSaveButton(tc?.label, tc?.variant)}</span>
    }
    if (fd.data_type === 'tab_prev') {
      const tc = fd.type_config as { label?: string } | undefined
      return (
        <button key={fd.slug} type="button" className={styles.tabNavButton}
          disabled={activeTab === 0} onClick={() => setActiveTab(t => Math.max(0, t - 1))}>
          {tc?.label || '← Previous'}
        </button>
      )
    }
    if (fd.data_type === 'tab_next') {
      const tc = fd.type_config as { label?: string } | undefined
      return (
        <button key={fd.slug} type="button" className={styles.tabNavButton}
          disabled={activeTab >= tabsWithFields.length - 1}
          onClick={() => setActiveTab(t => Math.min(tabsWithFields.length - 1, t + 1))}>
          {tc?.label || 'Next →'}
        </button>
      )
    }
    return null
  }

  function renderStructuralField(fd: (typeof sortedFields)[0]) {
    if (fd.data_type === 'save_button') {
      const tc = fd.type_config as { label?: string; variant?: string } | undefined
      return <div key={fd.slug}>{renderSaveButton(tc?.label, tc?.variant)}</div>
    }
    if (fd.data_type === 'tab_prev') {
      const tc = fd.type_config as { label?: string } | undefined
      return (
        <div key={fd.slug}>
          <button type="button" className={styles.tabNavButton}
            disabled={activeTab === 0} onClick={() => setActiveTab(t => Math.max(0, t - 1))}>
            {tc?.label || '← Previous'}
          </button>
        </div>
      )
    }
    if (fd.data_type === 'tab_next') {
      const tc = fd.type_config as { label?: string } | undefined
      return (
        <div key={fd.slug}>
          <button type="button" className={styles.tabNavButton}
            disabled={activeTab >= tabsWithFields.length - 1}
            onClick={() => setActiveTab(t => Math.min(tabsWithFields.length - 1, t + 1))}>
            {tc?.label || 'Next →'}
          </button>
        </div>
      )
    }
    if (fd.data_type === 'hstack') {
      // Render hstack groups (children of this hstack)
      const groups = childMap.get(fd.slug) ?? []
      if (groups.length === 0) return null

      const hasMultipleGroups = groups.length > 1
      const groupElements = groups.map(group => {
        const tc = group.type_config as { align?: string } | undefined
        const align = tc?.align ?? 'left'
        const groupItems = (childMap.get(group.slug) ?? []).map(renderHstackGroupButton).filter(Boolean)
        if (groupItems.length === 0) return null
        const alignClass = align === 'center' ? styles.hstackCenter : align === 'right' ? styles.hstackRight : styles.hstackLeft
        return <div key={group.slug} className={`${styles.hstack} ${alignClass}`}>{groupItems}</div>
      }).filter(Boolean)

      if (groupElements.length === 0) return null
      if (!hasMultipleGroups) return <div key={fd.slug}>{groupElements}</div>
      return (
        <div key={fd.slug} className={`${styles.hstack} ${styles.hstackSpaceBetween}`}>
          {groupElements}
        </div>
      )
    }
    return null
  }

  // hstack_group, tab_prev, tab_next are rendered inside their hstack parent — exclude from lists
  const CHILD_ONLY = new Set(['hstack_group', 'tab_prev', 'tab_next'])

  function renderFieldList(fieldList: typeof sortedFields) {
    const allItems = fieldList
      .filter(fd => !CHILD_ONLY.has(fd.data_type))
      .map(fd => {
      if (STRUCTURAL.has(fd.data_type)) return renderStructuralField(fd)
      return renderFieldRow(fd)
    }).filter(Boolean)
    if (allItems.length === 0) return null
    return (
      <div className={compact ? styles.formCompact : styles.form} style={{ marginBottom: '0.5rem' }}>
        {allItems}
      </div>
    )
  }

  return (
    <div className={styles.page}>
      {transitionPopup.length > 0 && (
        <TransitionMessagePopup messages={transitionPopup} onClose={() => setTransitionPopup([])} />
      )}
      <div className={styles.header}>
        <button type="button" className={styles.backBtn} onClick={() => navigate(-1)}>
          ← Back
        </button>
        <h1 className={styles.pageTitle}>
          Entity
          <span className={styles.metaInfo} style={{ marginLeft: '0.75rem', display: 'inline' }}>
            {entityId.slice(0, 8)}…
          </span>
        </h1>
      </div>

      <div className={styles.metaInfo}>
        Type: {entity.user_defined_model_type_id ?? '—'} ·
        Created: {new Date(entity.created_at).toLocaleString()} ·
        Updated: {new Date(entity.updated_at).toLocaleString()}
        {config && ` · Config version: ${entity.config_version_id.slice(0, 8)}…`}
      </div>

      {isArchived && (
        <MigrationAssistant
          entityId={resolvedEntityId}
          targetTypeId={entity.user_defined_model_type_id}
          sourceConfig={config}
          onMigrated={updated => { setEntity(updated); setDirty({}); void load() }}
        />
      )}

      {fields.length === 0 && (
        <div style={{ color: '#888', fontStyle: 'italic', padding: '1rem' }}>
          {config ? 'This config has no fields defined.' : 'No config loaded for this entity type.'}
        </div>
      )}

      {/* Fields above tabs (or all fields if no tabs) */}
      {renderFieldList(aboveFields)}

      {/* Tab navigation */}
      {hasTabs && (
        <>
          <div className={styles.tabNavigation} role="tablist">
            {tabsWithFields.map(({ tab }, idx) => {
              // Tab icon and tooltip come ONLY from policy messages that highlight the tab's own slug
              const tabSeverity = fieldSeverities[tab.slug]
              const tabMsgs = fieldMessages[tab.slug] ?? []
              const tabTitle = getTabTitle(tab)
              const targetId = `tab-sev-${tab.slug.replace(/[^a-z0-9]/gi, '-')}`
              const tabIconChar = tabSeverity === 'success' ? '✓'
                : (tabSeverity === 'error' || tabSeverity === 'critical') ? '✕'
                : tabSeverity === 'info' ? 'ℹ'
                : '⚠'
              const tabIconClass = tabSeverity === 'success' ? styles.tabSuccessIcon
                : (tabSeverity === 'error' || tabSeverity === 'critical') ? styles.tabErrorIcon
                : styles.tabWarningIcon
              return (
                <button
                  key={tab.slug}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === idx}
                  className={`${styles.tabButton} ${activeTab === idx ? styles.tabButtonActive : ''}`}
                  onClick={() => setActiveTab(idx)}
                  disabled={saving || transitioning}
                >
                  <span className={styles.tabNumberContainer}>
                    {tabSeverity && tabMsgs.length > 0 ? (
                      <>
                        <Tooltip target={`#${targetId}`} position="top">
                          <ul style={{ margin: 0, padding: '0 0 0 1rem', fontSize: '0.78rem', maxWidth: '220px' }}>
                            {tabMsgs.map((m, i) => <li key={i}>{m.text}</li>)}
                          </ul>
                        </Tooltip>
                        <span id={targetId} className={tabIconClass}>
                          {tabIconChar}
                        </span>
                      </>
                    ) : (
                      <span className={styles.tabNumber}>{idx + 1}</span>
                    )}
                  </span>
                  <span className={styles.tabLabel}>{tabTitle}</span>
                </button>
              )
            })}
          </div>

          {/* Mobile dropdown */}
          <div className={styles.tabNavigationDropdown}>
            <select
              value={activeTab}
              onChange={e => setActiveTab(parseInt(e.target.value, 10))}
              disabled={saving || transitioning}
            >
              {tabsWithFields.map(({ tab }, idx) => (
                <option key={tab.slug} value={idx}>
                  {idx + 1}. {getTabTitle(tab)}
                </option>
              ))}
            </select>
          </div>

          {/* Tab panels */}
          <div className={styles.tabContent}>
            {tabsWithFields.map(({ tab, fields: tabFieldList }, idx) => (
              <div
                key={tab.slug}
                role="tabpanel"
                className={activeTab === idx ? styles.tabPanelActive : styles.tabPanelHidden}
              >
                {renderFieldList(tabFieldList)}
              </div>
            ))}
          </div>
        </>
      )}

      {/* Fields below tabs */}
      {renderFieldList(belowFields)}

      {/* Errors and success messages */}
      {errors.length > 0 && (
        <div style={{ marginTop: '0.5rem' }}>
          <PolicyMessageList messages={errors.map(m => ({ level: 'error' as const, text: m }))} />
        </div>
      )}
      {globalPolicyMessages.length > 0 && (
        <div style={{ marginTop: '0.5rem' }}>
          <PolicyMessageList messages={globalPolicyMessages} />
        </div>
      )}
      {success && (
        <div className={styles.successWithIcon}>
          <span className={styles.successCheckIcon}>✓</span>
          {success}
        </div>
      )}

      {/* Default toolbar — hidden if the config has inline save buttons */}
      <div className={styles.toolbar}>
        <div style={{ fontSize: '0.875rem', color: '#888' }}>
          {dirtyCount > 0 ? `${dirtyCount} unsaved change${dirtyCount > 1 ? 's' : ''}` : 'No changes'}
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button type="button"
            className={`${styles.compactModeBtn} ${compact ? styles.compactModeBtnActive : ''}`}
            onClick={() => setCompact(c => !c)}
            title="Toggle compact view">
            Compact
          </button>
          {dirtyCount > 0 && (
            <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => { setDirty({}); setDiscardCount(c => c + 1) }}>
              Discard All
            </button>
          )}
          <button type="button" className={`${styles.btn} ${styles.btnSecondary}`}
            onClick={() => setShowHistory(!showHistory)}>
            {showHistory ? 'Hide History' : 'View History'}
          </button>
          {!hasSaveInConfig && renderToolbarSaveButton()}
        </div>
      </div>

      {showHistory && (
        <div className={styles.historySection}>
          <div className={styles.historyTitle}>Edit History</div>
          <HistoryPanel entityId={resolvedEntityId} />
        </div>
      )}
    </div>
  )
}

// ── Entity selector / create panel ───────────────────────────────────────────

export function UdmEntityPanel() {
  const navigate = useNavigate()
  const [types, setTypes] = useState<UDMTypeOut[]>([])
  const [entityMap, setEntityMap] = useState<Record<string, EntityAutocompleteItem[]>>({})
  const [canCreateMap, setCanCreateMap] = useState<Record<string, boolean>>({})
  const [loading, setLoading] = useState(true)
  const [creatingTypeId, setCreatingTypeId] = useState<string | null>(null)
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const loadedTypes = await udmListTypes()
        setTypes(loadedTypes)
        const typesWithConfig = loadedTypes.filter(t => t.field_config_id !== null)
        const [entityResults, createResults] = await Promise.all([
          Promise.all(loadedTypes.map(t => udmSearchEntities('', t.id).catch(() => [] as EntityAutocompleteItem[]))),
          Promise.all(typesWithConfig.map(t => udmCanCreateEntity(t.id).catch(() => ({ valid: false, policy_messages: [], errors: {} })))),
        ])
        const entityMap: Record<string, EntityAutocompleteItem[]> = {}
        loadedTypes.forEach((t, i) => { entityMap[t.id] = entityResults[i] })
        setEntityMap(entityMap)
        const canCreate: Record<string, boolean> = {}
        typesWithConfig.forEach((t, i) => { canCreate[t.id] = createResults[i].valid })
        setCanCreateMap(canCreate)
      } catch {
        // silently fail — types stays empty
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [])

  async function handleCreate(typeId: string) {
    setCreatingTypeId(typeId)
    setCreateErrors(prev => { const n = { ...prev }; delete n[typeId]; return n })
    try {
      const { udmCreateEntity } = await import('./apiUdm')
      const e = await udmCreateEntity({ user_defined_model_type_id: typeId })
      navigate(`/udm-entity/${e.id}`)
    } catch (err) {
      setCreateErrors(prev => ({ ...prev, [typeId]: err instanceof Error ? err.message : 'Create failed' }))
    } finally {
      setCreatingTypeId(null)
    }
  }

  return (
    <div className={dsStyles.container}>
      <div className={dsStyles.content}>
        {loading && <p className={dsStyles.stateBox}>Loading…</p>}

        {!loading && types.length === 0 && (
          <p className={dsStyles.stateBox}>No types available.</p>
        )}

        {!loading && types.length > 0 && (
          <div className={dsStyles.callList}>
            {types.map(type => {
              const entities = entityMap[type.id] ?? []
              const canCreate = canCreateMap[type.id] === true
              const isCreating = creatingTypeId === type.id
              const createError = createErrors[type.id]
              return (
                <div key={type.id} className={dsStyles.callCard}>
                  <div className={dsStyles.callCardBody}>
                    <h2 className={dsStyles.callCardTitle}>{type.label || type.name}</h2>
                  </div>
                  <div className={dsStyles.submissionsSection}>
                    {entities.length === 0 ? (
                      <p style={{ color: '#9ca3af', fontSize: '0.85rem', margin: 0 }}>No entities</p>
                    ) : (
                      entities.map(e => (
                        <div key={e.id} className={dsStyles.submissionRow}>
                          <div className={dsStyles.submissionInfo}>
                            <span>{e.display || e.id.slice(0, 8)}</span>
                          </div>
                          <button
                            type="button"
                            className={dsStyles.submissionOpenBtn}
                            onClick={() => navigate(`/udm-entity/${e.id}`)}
                          >
                            Open
                          </button>
                        </div>
                      ))
                    )}
                  </div>
                  {canCreate && (
                    <div className={dsStyles.callCardFooter}>
                      <button
                        type="button"
                        className={dsStyles.btnSubmit}
                        onClick={() => void handleCreate(type.id)}
                        disabled={isCreating}
                      >
                        {isCreating ? 'Creating…' : 'Create New'}
                      </button>
                    </div>
                  )}
                  {createError && (
                    <div style={{ padding: '0.5rem 1.5rem', color: '#dc2626', fontSize: '0.85rem' }}>
                      {createError}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
