import { useState, useEffect, useRef, useMemo, type ReactNode } from 'react'
import { Tooltip } from 'primereact/tooltip'
import {
  udmSearchUsers,
  udmSearchGroups,
} from '../apiUdm'
import type { FieldDefinitionOut, PolicyMessage, ConfigVersionOut } from '../apiUdm'
import type { FieldInputProps } from './types'
import { getLang } from './types'
import { fieldEditorRegistry } from './registry'
import { PolicyMessageList } from './shared'
import { FieldInput } from './FieldInput'
import { FieldCommitWrapper, LARGE_TYPES, BLUR_COMMIT_TYPES } from './FieldCommitWrapper'
import { useUdmGrants, type NewItemGrant } from './grants'
import { ReadonlyBadge } from './shared'
import { FieldPreview } from './FieldPreview'
import { PreviewTable, type PreviewRow } from './PreviewTable'
import styles from '../UdmEntityEditor.module.css'

// ── Compact sub-field severity helpers ────────────────────────────────────────

const SUB_SEV_ICON: Record<string, string> = {
  critical: 'pi-times-circle', error: 'pi-times-circle',
  warning: 'pi-exclamation-circle', info: 'pi-info-circle',
}
const SUB_SEV_CLASS: Record<string, string> = {
  critical: styles.severityIconError, error: styles.severityIconError,
  warning: styles.severityIconWarning, info: styles.severityIconInfo,
}
const SUB_SEV_LEFT: Record<string, string> = {
  critical: styles.fieldGroupCompactError, error: styles.fieldGroupCompactError,
  warning: styles.fieldGroupCompactWarning, info: styles.fieldGroupCompactInfo,
}


// ── Helpers (SubmodelEditor-local) ────────────────────────────────────────────

const SEVERITY_ORDER = ['info', 'warning', 'error', 'critical']

function maxSeverity(msgs: PolicyMessage[]): string | undefined {
  let max: string | undefined
  for (const m of msgs) {
    if (!max || SEVERITY_ORDER.indexOf(m.level) > SEVERITY_ORDER.indexOf(max)) max = m.level
  }
  return max
}

function subFieldColor(severity: string | undefined): string {
  if (severity === 'critical' || severity === 'error') return '#dc2626'
  if (severity === 'warning') return '#d97706'
  if (severity === 'info') return '#2563eb'
  return '#444'
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface ChildNode {
  id: string
  field_values: Array<{ field_slug: string; data_type: string; value: unknown; language: string }>
  children: Record<string, unknown[]>
  current_state: string | null
}

interface SubmodelOp {
  op: 'create' | 'update' | 'delete'
  id?: string
  fields?: Record<string, unknown>
  sort_order?: number
}

interface LocalChild {
  key: string
  id: string | null
  dirty: Record<string, unknown>
  saved: ChildNode | null
  deleted: boolean
}

function buildOps(items: LocalChild[]): SubmodelOp[] {
  const ops: SubmodelOp[] = []
  for (const item of items) {
    if (item.deleted && item.id) {
      ops.push({ op: 'delete', id: item.id })
    } else if (!item.id && !item.deleted) {
      ops.push({ op: 'create', fields: item.dirty })
    } else if (item.id && !item.deleted && Object.keys(item.dirty).length > 0) {
      ops.push({ op: 'update', id: item.id, fields: item.dirty })
    }
  }
  return ops
}

function getChildFieldValue(child: LocalChild, slug: string, lang = ''): unknown {
  if (slug in child.dirty) {
    const d = child.dirty[slug]
    if (lang && typeof d === 'object' && d !== null) return (d as Record<string, unknown>)[lang]
    return d
  }
  const fv = child.saved?.field_values.find(v => v.field_slug === slug && v.language === lang)
  return fv?.value ?? null
}

function resolvePreviewValue(fd: FieldDefinitionOut, val: unknown, nameMap: Record<string, string>): string | null {
  if (val === null || val === undefined || val === '') return null
  const dt = fd.data_type
  if (dt === 'user_select' || dt === 'group_select') {
    return nameMap[String(val)] ?? String(val)
  }
  if (dt === 'user_select_multi' || dt === 'group_select_multi') {
    const ids = Array.isArray(val) ? (val as unknown[]) : [val]
    if (ids.length === 0) return null
    return ids.map(id => nameMap[String(id)] ?? String(id)).join(', ')
  }
  return String(val)
}

function buildPreviewLabel(
  subFields: FieldDefinitionOut[],
  fieldValues: Array<{ field_slug: string; language: string; value: unknown }>,
  dirty: Record<string, unknown>,
  uiLang: string,
  fallback: string,
  nameMap: Record<string, string> = {},
): string {
  const previewFields = subFields.filter(f => f.is_preview)
  if (previewFields.length === 0) return fallback
  const parts: string[] = []
  for (const fd of previewFields) {
    let val: unknown
    if (fd.slug in dirty) {
      const d = dirty[fd.slug]
      if (fd.is_localized && typeof d === 'object' && d !== null) {
        val = (d as Record<string, unknown>)[uiLang] ?? Object.values(d as object)[0]
      } else {
        val = d
      }
    } else if (fd.is_localized) {
      const fv = fieldValues.find(v => v.field_slug === fd.slug && v.language === uiLang)
        ?? fieldValues.find(v => v.field_slug === fd.slug)
      val = fv?.value
    } else {
      val = fieldValues.find(v => v.field_slug === fd.slug && v.language === '')?.value
    }
    const resolved = resolvePreviewValue(fd, val, nameMap)
    if (resolved !== null) parts.push(resolved)
  }
  return parts.length > 0 ? parts.join(' · ') : fallback
}

// ── Collapsed preview helper ──────────────────────────────────────────────────

function buildCollapsedRows(
  subFields: FieldDefinitionOut[],
  getValue: (fd: FieldDefinitionOut) => unknown,
  lang: string,
  entityChildren?: Record<string, unknown[]>,
): PreviewRow[] {
  const visible = subFields.filter(f => f.is_preview).length > 0
    ? subFields.filter(f => f.is_preview)
    : subFields.slice(0, 4)
  return visible.map(fd => ({
    key: fd.slug,
    label: getLang(fd.label as Record<string, string>, lang) || fd.slug,
    value: <FieldPreview fd={fd} value={getValue(fd)} lang={lang} entityChildren={entityChildren} />,
  }))
}

// ── SubmodelChildCard ─────────────────────────────────────────────────────────

interface SubmodelChildCardProps {
  item: LocalChild
  subFields: FieldDefinitionOut[]
  subLanguages: string[]
  uiLang: string
  disabled: boolean
  onChange: (dirty: Record<string, unknown>) => void
  onDelete: () => void
  subFieldSeverities?: Record<string, string>
  subFieldMessages?: Record<string, PolicyMessage[]>
  nameMap?: Record<string, string>
  onEntityRefresh?: (policyMessages?: PolicyMessage[]) => void | Promise<void>
  compact?: boolean
  expanded: boolean
  onToggleExpanded: () => void
  /** §6: whether the delete/restore buttons are enabled (policy grant). */
  deleteAllowed?: boolean
  /** §6: for NEW items — visible field slugs of the unsaved-item form; null = all. */
  visibleSlugs?: string[] | null
  /** §6: field slugs the user may edit on this item; null = all (legacy). */
  editableSlugs?: string[] | null
  /** Immediate mode: commit a single sub-field's staged value now. Throws on failure. */
  onCommitField?: (subSlug: string) => Promise<void>
  /** Immediate mode: whether the delete button is busy. */
  opBusy?: boolean
}

function SubmodelChildCard({ item, subFields, subLanguages, uiLang, disabled, onChange, onDelete, subFieldSeverities, subFieldMessages, nameMap = {}, onEntityRefresh, compact, expanded, onToggleExpanded, deleteAllowed, visibleSlugs, editableSlugs, onCommitField, opBusy }: SubmodelChildCardProps) {
  const canDelete = deleteAllowed ?? !disabled
  const shownFields = visibleSlugs != null ? subFields.filter(f => visibleSlugs.includes(f.slug)) : subFields
  const fieldEditable = (slug: string) => editableSlugs == null || editableSlugs.includes(slug)
  const hasHighlightedFields = Object.keys(subFieldSeverities ?? {}).length > 0
  const [activeLang, setActiveLang] = useState(subLanguages[0] ?? '')
  const fallbackLabel = item.id ? item.id.slice(0, 8) + '…' : 'New (unsaved)'
  const label = item.id
    ? buildPreviewLabel(subFields, item.saved?.field_values ?? [], item.dirty, uiLang, fallbackLabel, nameMap)
    : fallbackLabel

  function handleFieldChange(slug: string, lang: string, val: unknown) {
    const subFd = subFields.find(f => f.slug === slug)
    let update: Record<string, unknown>
    if (subFd?.is_localized) {
      const existing = (typeof item.dirty[slug] === 'object' && item.dirty[slug] !== null)
        ? item.dirty[slug] as Record<string, unknown>
        : subLanguages.reduce((acc, l) => {
            acc[l] = getChildFieldValue(item, slug, l)
            return acc
          }, {} as Record<string, unknown>)
      update = { ...item.dirty, [slug]: { ...existing, [lang]: val } }
    } else {
      update = { ...item.dirty, [slug]: val }
    }
    onChange(update)
  }

  const hasChanges = Object.keys(item.dirty).length > 0

  // Per-subfield commit (immediate mode): only for saved items with a commit callback
  const [subSaving, setSubSaving] = useState<Record<string, boolean>>({})
  const commitEnabled = !!onCommitField && item.id != null
  async function commitSub(slug: string) {
    if (!onCommitField || subSaving[slug]) return
    setSubSaving(s => ({ ...s, [slug]: true }))
    try {
      await onCommitField(slug)
    } catch {
      // error surfaced by the parent editor under the submodel field
    } finally {
      setSubSaving(s => ({ ...s, [slug]: false }))
    }
  }
  function cancelSub(slug: string) {
    const d = { ...item.dirty }
    delete d[slug]
    onChange(d)
  }
  // Nested submodels/workflows manage their own persistence — no commit wrapper
  const subCommitable = (subFd: FieldDefinitionOut) =>
    commitEnabled && !subFd.data_type.startsWith('submodel') && subFd.data_type !== 'workflow'
  const wrapCommit = (subFd: FieldDefinitionOut, children: ReactNode, fieldDisabled: boolean) =>
    subCommitable(subFd) ? (
      <FieldCommitWrapper
        dirty={subFd.slug in item.dirty}
        saving={!!subSaving[subFd.slug]}
        large={LARGE_TYPES.has(subFd.data_type)}
        blurCommit={BLUR_COMMIT_TYPES.has(subFd.data_type)}
        disabled={fieldDisabled}
        onCommit={() => void commitSub(subFd.slug)}
        onCancel={() => cancelSub(subFd.slug)}>
        {children}
      </FieldCommitWrapper>
    ) : children

  const topSeverity = hasHighlightedFields ? maxSeverity(
    Object.entries(subFieldSeverities ?? {}).flatMap(([, sev]) => [{ level: sev } as PolicyMessage])
  ) : undefined
  const cardBorderColor = item.deleted ? '#fca5a5'
    : ((() => {
        if (!topSeverity) return hasChanges ? '#f9a825' : '#e0e0e0'
        if (topSeverity === 'critical' || topSeverity === 'error') return '#dc2626'
        if (hasChanges) return '#f9a825'
        if (topSeverity === 'warning') return '#eab308'
        return '#3b82f6'
      })())
  return (
    <div style={{
      border: `1px solid ${cardBorderColor}`,
      borderRadius: '6px', marginBottom: '0.5rem', background: item.deleted ? '#fef2f2' : '#fafafa',
      ...(hasHighlightedFields ? { boxShadow: '0 0 0 2px rgba(220,38,38,0.15)' } : {}),
    }}>
      <div style={{ display: 'flex', justifyContent: item.deleted ? 'space-between' : 'flex-end', alignItems: 'center', padding: '0.5rem 0.75rem' }}>
        {item.deleted && (
          <span style={{ fontSize: '0.85rem', fontFamily: 'monospace', color: '#555' }}>
            {label} (will be deleted)
          </span>
        )}
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          {!item.deleted && (
            <>
              <button type="button"
                style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer', background: '#fff' }}
                onClick={onToggleExpanded}>
                {expanded ? 'Collapse' : 'Edit'}
              </button>
              {canDelete && (
                <button type="button"
                  style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #dc2626', borderRadius: '4px', cursor: 'pointer', background: '#fff', color: '#dc2626' }}
                  onClick={onDelete} disabled={opBusy}>
                  Delete
                </button>
              )}
            </>
          )}
          {item.deleted && canDelete && (
            <button type="button"
              style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer', background: '#fff' }}
              onClick={() => onChange({ ...item.dirty, _undelete: true })}>
              Restore
            </button>
          )}
        </div>
      </div>

      {!expanded && !item.deleted && (() => {
        const rows = buildCollapsedRows(
          subFields,
          fd => getChildFieldValue(item, fd.slug, fd.is_localized ? uiLang : ''),
          uiLang,
          item.saved?.children,
        )
        return rows.length > 0 ? (
          <div style={{ padding: '0 0.75rem 0.5rem', borderTop: '1px solid #f3f4f6' }}>
            <PreviewTable borderless rows={rows} />
          </div>
        ) : null
      })()}

      {expanded && !item.deleted && (
        <div style={{ padding: '0.5rem 0.75rem', borderTop: '1px solid #e8e8e8' }}>
          {subLanguages.length > 1 && (
            <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.5rem' }}>
              {subLanguages.map(l => (
                <button key={l} type="button"
                  style={{ padding: '0.2rem 0.5rem', border: '1px solid #ccc', borderRadius: '4px 4px 0 0', cursor: 'pointer', fontSize: '0.78rem', background: activeLang === l ? '#0066cc' : '#fff', color: activeLang === l ? '#fff' : '#555' }}
                  onClick={() => setActiveLang(l)}>
                  {l}
                </button>
              ))}
            </div>
          )}
          {shownFields.map(subFd => {
            const subLabel = getLang(subFd.label as Record<string, string>, uiLang) || subFd.slug
            const langs = subFd.is_localized ? subLanguages.filter(Boolean) : ['']
            const sev = subFieldSeverities?.[subFd.slug]
            const subColor = subFieldColor(sev)
            const subMsgs = subFieldMessages?.[subFd.slug] ?? []
            if (compact) {
              const tooltipId = `udm-lst-${item.key.replace(/[^a-z0-9]/gi, '-')}-${subFd.slug.replace(/[^a-z0-9]/gi, '-')}`
              return (
                <div key={subFd.slug} className={`${styles.fieldGroupCompact} ${sev ? (SUB_SEV_LEFT[sev] ?? '') : ''}`}>
                  <div className={styles.compactHeader}>
                    {sev && subMsgs.length > 0 && (
                      <>
                        <Tooltip target={`#${tooltipId}`} position="right">
                          <ul style={{ margin: 0, padding: '0 0 0 1rem', fontSize: '0.8rem', maxWidth: '260px' }}>
                            {subMsgs.map((m, i) => <li key={i}>{m.text}</li>)}
                          </ul>
                        </Tooltip>
                        <i id={tooltipId} className={`pi ${SUB_SEV_ICON[sev] ?? 'pi-info-circle'} ${styles.severityIcon} ${SUB_SEV_CLASS[sev] ?? ''}`} />
                      </>
                    )}
                    <span className={styles.compactLabel}>{subLabel}{(disabled || !fieldEditable(subFd.slug) || !!(subFd.type_config as Record<string, unknown>)?.default_current_user || (item.id == null && subFd.data_type === 'workflow')) && <ReadonlyBadge />}</span>
                  </div>
                  {(() => {
                    const fieldDisabled = disabled || !fieldEditable(subFd.slug) || !!(subFd.type_config as Record<string, unknown>)?.default_current_user
                    return wrapCommit(subFd, langs.map(lang => (
                      <FieldInput key={lang || 'nolang'} fd={subFd}
                        value={getChildFieldValue(item, subFd.slug, lang)}
                        onChange={val => handleFieldChange(subFd.slug, lang, val)}
                        disabled={fieldDisabled}
                        lang={lang} entityChildren={item.saved?.children} nodeId={item.id}
                        onEntityRefresh={onEntityRefresh} />
                    )), fieldDisabled)
                  })()}
                </div>
              )
            }
            return (
              <div key={subFd.slug} style={{
                marginBottom: '0.6rem',
                ...(sev ? { outline: `2px solid ${subColor}`, borderRadius: '4px', padding: '0.25rem' } : {}),
              }}>
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: sev ? subColor : '#444', marginBottom: '0.2rem' }}>
                  {subLabel} <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#999' }}>({subFd.data_type})</span>
                  {(disabled || !fieldEditable(subFd.slug) || !!(subFd.type_config as Record<string, unknown>)?.default_current_user || (item.id == null && subFd.data_type === 'workflow')) && <ReadonlyBadge />}
                </div>
                {(() => {
                  const fieldDisabled = disabled || !fieldEditable(subFd.slug) || !!(subFd.type_config as Record<string, unknown>)?.default_current_user
                  return wrapCommit(subFd, langs.map(lang => (
                    <FieldInput
                      key={lang || 'nolang'}
                      fd={subFd}
                      value={getChildFieldValue(item, subFd.slug, lang)}
                      onChange={val => handleFieldChange(subFd.slug, lang, val)}
                      disabled={fieldDisabled}
                      lang={lang}
                      entityChildren={item.saved?.children}
                      nodeId={item.id}
                      onEntityRefresh={onEntityRefresh}
                    />
                  )), fieldDisabled)
                })()}
                {subMsgs.length > 0 && <PolicyMessageList messages={subMsgs} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── SubmodelEditor ────────────────────────────────────────────────────────────

function SubmodelEditorComponent({ fd, existingChildren, existingValue, disabled, uiLang, onChange, subFieldSeverities, subFieldMessages, resetKey, onEntityRefresh, compact, nodeId, onCommitOps }: {
  fd: FieldDefinitionOut
  existingChildren: unknown[]
  existingValue: unknown
  disabled: boolean
  uiLang: string
  nodeId?: string | null
  onChange: (ops: SubmodelOp[] | { op: string; fields?: Record<string, unknown> } | null) => void
  subFieldSeverities?: Record<string, string>
  subFieldMessages?: Record<string, PolicyMessage[]>
  resetKey?: number
  onEntityRefresh?: (policyMessages?: PolicyMessage[]) => void | Promise<void>
  compact?: boolean
  /** Immediate mode: persist ops right away (per-op PATCH). Absent = legacy staging. */
  onCommitOps?: (ops: unknown) => Promise<void>
}) {
  const isList = fd.data_type === 'submodel_list'
  // §6 grants: null context (admin previews etc.) keeps legacy `disabled` behavior.
  const grants = useUdmGrants()
  const createGrant: NewItemGrant | null | undefined =
    grants == null ? undefined
    : nodeId != null ? (grants.creatableSubmodels[nodeId]?.[fd.slug] ?? null)
    : null
  const canCreate = createGrant === undefined ? !disabled : createGrant != null
  const itemDeleteAllowed = (itemId: string | null) => {
    if (itemId == null) return true // unsaved item: removing it is a no-op server-side
    if (grants == null) return !disabled
    return grants.deletableNodes.includes(itemId)
  }
  const itemEditableSlugs = (itemId: string | null): string[] | null => {
    if (itemId == null) return createGrant === undefined ? null : (createGrant?.editable ?? [])
    if (grants == null) return null
    return grants.editableFields[itemId] ?? []
  }
  const subConfig = fd.submodel_config as ConfigVersionOut | null | undefined
  const subFields = subConfig?.fields ?? []
  const subLanguages = (subConfig?.languages ?? []).map(l => l.code)
  if (subLanguages.length === 0) subLanguages.push('')

  // Always keep the latest onChange in a ref so handlers never capture a stale closure
  const onChangeRef = useRef(onChange)
  useEffect(() => { onChangeRef.current = onChange })

  // For submodel_list: manage a list of LocalChild items
  const toItems = (children: unknown[]): LocalChild[] =>
    (children as ChildNode[]).map(c => ({ key: c.id, id: c.id, dirty: {}, saved: c, deleted: false }))

  const [items, setItems] = useState<LocalChild[]>(() => toItems(existingChildren))
  const nextKeyRef = useRef(0)
  // Immediate mode: busy flag for create/delete PATCHes; expand items created just now
  const [opBusy, setOpBusy] = useState(false)
  const justCreatedRef = useRef(false)

  // Lifted expansion state — keyed by item.key (= id for saved items, "_new_N" for pending ones)
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set)

  // Sync when the server refreshes the entity.
  // Only relevant for submodel_list; the submodel_select branch manages its own
  // pending state below and must NOT emit a list-shaped [] op (which would be
  // written into the single FK column and rejected as "not a valid UUID").
  const prevServerIds = useRef(new Set((existingChildren as ChildNode[]).map(c => c.id)))
  useEffect(() => {
    if (!isList) return
    const incoming = existingChildren as ChildNode[]
    const incomingIds = new Set(incoming.map(c => c.id))
    const sameIds =
      incomingIds.size === prevServerIds.current.size &&
      [...incomingIds].every(id => prevServerIds.current.has(id))
    if (!sameIds) {
      // Preserve expansion across the reset: carry over expanded existing items
      // and expand any newly-added server items if a pending-new item was open.
      const newExpandedKeys = new Set<string>()
      for (const c of incoming) {
        if (expandedKeys.has(c.id)) newExpandedKeys.add(c.id)
      }
      const hasExpandedNew = justCreatedRef.current || items.some(it => !it.id && expandedKeys.has(it.key))
      justCreatedRef.current = false
      if (hasExpandedNew) {
        for (const c of incoming) {
          if (!prevServerIds.current.has(c.id)) newExpandedKeys.add(c.id)
        }
      }
      prevServerIds.current = incomingIds
      setItems(toItems(incoming))
      setExpandedKeys(newExpandedKeys)
      // After a server refresh there are no pending local ops
      onChangeRef.current([])
    } else {
      // IDs are the same but server data may have changed (e.g. after a child-node
      // workflow transition). Update the saved snapshot for each existing item so
      // field values (including workflow state badges) reflect the new server state,
      // while preserving any in-progress dirty edits.
      setItems(prev => prev.map(item => {
        if (!item.id) return item
        const fresh = incoming.find(c => c.id === item.id)
        return fresh ? { ...item, saved: fresh } : item
      }))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existingChildren])


  // Helpers that mutate items AND immediately propagate ops — no useEffect lag
  function applyItemChange(newItems: LocalChild[]) {
    setItems(newItems)
    const ops = buildOps(newItems)
    onChangeRef.current(ops.length > 0 ? ops : [])
  }

  async function runOp(ops: SubmodelOp[]) {
    if (!onCommitOps || opBusy) return
    setOpBusy(true)
    try {
      await onCommitOps(ops)
    } catch {
      // error surfaced by the parent editor under the submodel field
    } finally {
      setOpBusy(false)
    }
  }

  function addItem() {
    if (onCommitOps) {
      // Immediate mode: create on the server right away; the entity refresh
      // rebuilds the item list with the fresh child, expanded via justCreatedRef.
      justCreatedRef.current = true
      void runOp([{ op: 'create', fields: {} }])
      return
    }
    const key = `_new_${nextKeyRef.current++}`
    applyItemChange([...items, { key, id: null, dirty: {}, saved: null, deleted: false }])
    setExpandedKeys(prev => new Set([...prev, key]))
  }

  function updateItem(key: string, dirty: Record<string, unknown>) {
    applyItemChange(items.map(it => it.key === key ? { ...it, dirty, deleted: false } : it))
  }

  function deleteItem(key: string) {
    const target = items.find(it => it.key === key)
    if (onCommitOps && target?.id) {
      // Immediate mode: delete on the server right away
      void runOp([{ op: 'delete', id: target.id }])
      return
    }
    applyItemChange(
      items
        .map(it => {
          if (it.key !== key) return it
          if (!it.id) return { ...it, deleted: true }
          return { ...it, deleted: true, dirty: {} }
        })
        .filter(it => !(it.deleted && !it.id))
    )
  }

  /** Immediate mode: commit one staged sub-field of one item, then un-stage it. */
  async function commitItemField(key: string, subSlug: string) {
    const target = items.find(it => it.key === key)
    if (!onCommitOps || !target?.id || !(subSlug in target.dirty)) return
    await onCommitOps([{ op: 'update', id: target.id, fields: { [subSlug]: target.dirty[subSlug] } }])
    // Success: drop the committed sub-slug, keep other staged edits pending
    setItems(prev => {
      const next = prev.map(it => {
        if (it.key !== key) return it
        const d = { ...it.dirty }
        delete d[subSlug]
        return { ...it, dirty: d }
      })
      const ops = buildOps(next)
      onChangeRef.current(ops.length > 0 ? ops : [])
      return next
    })
  }

  // For submodel_select: the current op to send (or null = no change)
  const selectNodeId = typeof existingValue === 'string' ? existingValue : null
  const ownedChild = (existingChildren as ChildNode[]).find(c => c.id === selectNodeId) ?? null

  const [selectDirty, setSelectDirty] = useState<Record<string, unknown>>({})
  const [selectActiveLang, setSelectActiveLang] = useState(subLanguages[0] ?? '')
  const [selectExpanded, setSelectExpanded] = useState(false)
  // pendingNew = user clicked "Create", form shown optimistically before save
  const [pendingNew, setPendingNew] = useState(false)
  // pendingRemoval = user clicked Delete/Clear; hide the form before the save round-trip
  const [pendingRemoval, setPendingRemoval] = useState(false)

  // When the entity refreshes after save, clear the pending state
  const prevSelectNodeId = useRef(selectNodeId)
  useEffect(() => {
    if (selectNodeId !== prevSelectNodeId.current) {
      if (pendingNew && selectNodeId) {
        setPendingNew(false)
        setSelectDirty({})
      }
      // FK cleared by the server → drop the optimistic removal flag
      if (!selectNodeId) setPendingRemoval(false)
    }
    prevSelectNodeId.current = selectNodeId
  }, [selectNodeId, pendingNew])

  // Reset local UI state when the parent discards all changes.
  // We only reset local state here — the parent already cleared its own dirty map.
  const prevResetKey = useRef(resetKey)
  useEffect(() => {
    if (resetKey === prevResetKey.current) return
    prevResetKey.current = resetKey
    if (isList) {
      setItems(toItems(existingChildren as ChildNode[]))
      setExpandedKeys(new Set())
    } else {
      setSelectDirty({})
      setPendingNew(false)
      setPendingRemoval(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  // ── Resolve user/group display names for preview fields ──────────────────
  const [previewNameMap, setPreviewNameMap] = useState<Record<string, string>>({})

  const previewUserGroupFields = useMemo(
    () => subFields.filter(f => f.is_preview && (
      f.data_type === 'user_select' || f.data_type === 'user_select_multi' ||
      f.data_type === 'group_select' || f.data_type === 'group_select_multi'
    )),
    [subFields],
  )

  useEffect(() => {
    if (previewUserGroupFields.length === 0) return

    const userIds = new Set<string>()
    const groupIds = new Set<string>()

    function collect(values: Array<{ field_slug: string; language: string; value: unknown }>, dirty: Record<string, unknown>) {
      for (const fd of previewUserGroupFields) {
        const val = fd.slug in dirty
          ? dirty[fd.slug]
          : values.find(v => v.field_slug === fd.slug && v.language === '')?.value
        const ids = Array.isArray(val) ? (val as unknown[]) : (val != null ? [val] : [])
        const isUser = fd.data_type === 'user_select' || fd.data_type === 'user_select_multi'
        for (const id of ids) {
          const key = String(id)
          if (!(key in previewNameMap)) {
            if (isUser) userIds.add(key)
            else groupIds.add(key)
          }
        }
      }
    }

    if (isList) {
      for (const item of items) collect(item.saved?.field_values ?? [], item.dirty)
    } else {
      collect(ownedChild?.field_values ?? [], selectDirty)
    }

    if (userIds.size === 0 && groupIds.size === 0) return

    if (userIds.size > 0) {
      udmSearchUsers('').then(users => {
        setPreviewNameMap(prev => {
          const next = { ...prev }
          for (const u of users) if (userIds.has(u.id)) next[u.id] = u.display_name
          return next
        })
      }).catch(() => {})
    }
    if (groupIds.size > 0) {
      udmSearchGroups('').then(groups => {
        setPreviewNameMap(prev => {
          const next = { ...prev }
          for (const g of groups) if (groupIds.has(String(g.id))) next[String(g.id)] = g.name
          return next
        })
      }).catch(() => {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    isList ? items.map(it => it.key).join(',') : selectNodeId,
    previewUserGroupFields,
  ])

  // ── submodel_list UI ──
  if (isList) {
    const visible = items.filter(it => !(it.deleted && !it.id))

    return (
      <div>
        {visible.length === 0 && (
          <div style={{ fontSize: '0.85rem', color: '#888', fontStyle: 'italic', marginBottom: '0.5rem' }}>No items yet.</div>
        )}
        {visible.map(item => (
          <SubmodelChildCard
            key={item.key}
            item={item}
            subFields={subFields}
            subLanguages={subLanguages}
            uiLang={uiLang}
            disabled={disabled || (item.id != null && (itemEditableSlugs(item.id)?.length ?? 1) === 0)}
            onChange={dirty => updateItem(item.key, dirty)}
            onDelete={() => deleteItem(item.key)}
            deleteAllowed={!disabled && itemDeleteAllowed(item.id)}
            visibleSlugs={item.id == null && createGrant !== undefined ? (createGrant?.viewable ?? []) : null}
            editableSlugs={itemEditableSlugs(item.id)}
            subFieldSeverities={subFieldSeverities}
            subFieldMessages={subFieldMessages}
            nameMap={previewNameMap}
            onEntityRefresh={onEntityRefresh}
            compact={compact}
            onCommitField={onCommitOps ? subSlug => commitItemField(item.key, subSlug) : undefined}
            opBusy={opBusy}
            expanded={expandedKeys.has(item.key)}
            onToggleExpanded={() => setExpandedKeys(prev => {
              const next = new Set(prev)
              if (next.has(item.key)) next.delete(item.key)
              else next.add(item.key)
              return next
            })}
          />
        ))}
        {!disabled && canCreate && (
          <button type="button"
            style={{ fontSize: '0.82rem', padding: '0.3rem 0.75rem', border: '1px dashed #aaa', borderRadius: '4px', cursor: 'pointer', background: '#fff', color: '#555', marginTop: '0.25rem' }}
            onClick={addItem} disabled={opBusy}>
            {opBusy ? 'Saving…' : '+ Add item'}
          </button>
        )}
      </div>
    )
  }

  // ── submodel_select UI ──
  function handleCreate() {
    setPendingNew(true)
    setPendingRemoval(false)
    setSelectDirty({})
    setSelectExpanded(true)
    if (onCommitOps) {
      // Immediate mode: create the child now; the refresh sets the FK and
      // the prevSelectNodeId effect clears pendingNew.
      setOpBusy(true)
      onCommitOps({ op: 'create', fields: {} })
        .catch(() => setPendingNew(false))
        .finally(() => setOpBusy(false))
      return
    }
    onChange({ op: 'create', fields: {} })
  }

  function handleCancelPending() {
    setPendingNew(false)
    setSelectDirty({})
    if (!onCommitOps) onChange(null)
  }

  function handleDelete() {
    setPendingRemoval(true)
    setSelectExpanded(false)
    if (onCommitOps) {
      setOpBusy(true)
      onCommitOps({ op: 'delete' })
        .catch(() => setPendingRemoval(false))
        .finally(() => setOpBusy(false))
      return
    }
    onChange({ op: 'delete' })
  }

  function handleClear() {
    setPendingRemoval(true)
    setSelectExpanded(false)
    if (onCommitOps) {
      // Clearing the FK = PATCH the field to null
      setOpBusy(true)
      onCommitOps(null)
        .catch(() => setPendingRemoval(false))
        .finally(() => setOpBusy(false))
      return
    }
    onChange(null)
  }

  // Immediate mode: per-subfield commit/cancel for the owned child's fields
  const [selectSubSaving, setSelectSubSaving] = useState<Record<string, boolean>>({})
  async function commitSelectField(subSlug: string) {
    if (!onCommitOps || !(subSlug in selectDirty) || selectSubSaving[subSlug]) return
    setSelectSubSaving(s => ({ ...s, [subSlug]: true }))
    try {
      await onCommitOps({ op: 'update', fields: { [subSlug]: selectDirty[subSlug] } })
      setSelectDirty(prev => {
        const n = { ...prev }
        delete n[subSlug]
        // Re-stage any remaining uncommitted edits; [] = nothing pending
        onChangeRef.current(Object.keys(n).length > 0 ? { op: 'update', fields: n } : [])
        return n
      })
    } catch {
      // error surfaced by the parent editor under the submodel field
    } finally {
      setSelectSubSaving(s => ({ ...s, [subSlug]: false }))
    }
  }
  function cancelSelectField(subSlug: string) {
    setSelectDirty(prev => {
      const n = { ...prev }
      delete n[subSlug]
      onChangeRef.current(Object.keys(n).length > 0 ? { op: 'update', fields: n } : [])
      return n
    })
  }
  const selectCommitEnabled = !!onCommitOps && ownedChild != null && !pendingNew
  const wrapSelectCommit = (subFd: FieldDefinitionOut, children: ReactNode) =>
    selectCommitEnabled && !subFd.data_type.startsWith('submodel') && subFd.data_type !== 'workflow' ? (
      <FieldCommitWrapper
        dirty={subFd.slug in selectDirty}
        saving={!!selectSubSaving[subFd.slug]}
        large={LARGE_TYPES.has(subFd.data_type)}
        blurCommit={BLUR_COMMIT_TYPES.has(subFd.data_type)}
        disabled={disabled}
        onCommit={() => void commitSelectField(subFd.slug)}
        onCancel={() => cancelSelectField(subFd.slug)}>
        {children}
      </FieldCommitWrapper>
    ) : children

  function handleSelectFieldChange(slug: string, lang: string, val: unknown) {
    const subFd = subFields.find(f => f.slug === slug)
    let updated: Record<string, unknown>
    if (subFd?.is_localized) {
      const existing = (typeof selectDirty[slug] === 'object' && selectDirty[slug] !== null)
        ? selectDirty[slug] as Record<string, unknown>
        : subLanguages.reduce((acc, l) => {
            const fv = ownedChild?.field_values.find(v => v.field_slug === slug && v.language === l)
            acc[l] = fv?.value ?? null
            return acc
          }, {} as Record<string, unknown>)
      updated = { ...selectDirty, [slug]: { ...existing, [lang]: val } }
    } else {
      updated = { ...selectDirty, [slug]: val }
    }
    setSelectDirty(updated)
    if (pendingNew) {
      // still creating — carry the fields along with the create op
      onChange({ op: 'create', fields: updated })
    } else if (ownedChild) {
      // submodel_select expects a single dict op, never a list (that is the
      // submodel_list shape and would be written into the FK column).
      onChange({ op: 'update', fields: updated })
    }
  }

  // Show the form when: pending new creation, OR already has a saved/owned child.
  // An optimistic delete/clear hides it immediately.
  const showForm = !pendingRemoval && (pendingNew || ownedChild !== null || selectNodeId !== null)

  return (
    <div style={{ border: '1px solid #e0e0e0', borderRadius: '6px', padding: '0.75rem', background: '#fafafa' }}>
      {!showForm && (
        <div>
          <div style={{ fontSize: '0.85rem', color: '#888', fontStyle: 'italic', marginBottom: '0.5rem' }}>No submodel selected.</div>
          {!disabled && subConfig && (
            <button type="button"
              style={{ fontSize: '0.82rem', padding: '0.3rem 0.75rem', border: '1px dashed #aaa', borderRadius: '4px', cursor: 'pointer', background: '#fff', color: '#555' }}
              onClick={handleCreate} disabled={!canCreate}>
              + Create new submodel
            </button>
          )}
          {!disabled && !subConfig && (
            <div style={{ fontSize: '0.82rem', color: '#dc2626' }}>No submodel config assigned to this field.</div>
          )}
        </div>
      )}

      {showForm && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginBottom: '0.5rem' }}>
            <div style={{ display: 'flex', gap: '0.4rem' }}>
              {!disabled && (
                <>
                  {!pendingNew && (
                    <button type="button"
                      style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #ccc', borderRadius: '4px', cursor: 'pointer', background: '#fff' }}
                      onClick={() => setSelectExpanded(e => !e)}>
                      {selectExpanded ? 'Collapse' : 'Edit fields'}
                    </button>
                  )}
                  {pendingNew ? (
                    <button type="button"
                      style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #aaa', borderRadius: '4px', cursor: 'pointer', background: '#fff', color: '#666' }}
                      onClick={handleCancelPending}>
                      Cancel
                    </button>
                  ) : (
                    <>
                      <button type="button"
                        style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #dc2626', borderRadius: '4px', cursor: 'pointer', background: '#fff', color: '#dc2626' }}
                        onClick={handleDelete}>
                        Delete
                      </button>
                      <button type="button"
                        style={{ fontSize: '0.78rem', padding: '0.2rem 0.5rem', border: '1px solid #aaa', borderRadius: '4px', cursor: 'pointer', background: '#fff', color: '#666' }}
                        onClick={handleClear}>
                        Clear
                      </button>
                    </>
                  )}
                </>
              )}
            </div>
          </div>

          {!pendingNew && !selectExpanded && ownedChild && (() => {
            const rows = buildCollapsedRows(
              subFields,
              fd => {
                if (selectDirty[fd.slug] !== undefined) {
                  const d = selectDirty[fd.slug]
                  return fd.is_localized && typeof d === 'object' && d !== null
                    ? (d as Record<string, unknown>)[uiLang] ?? Object.values(d as object)[0]
                    : d
                }
                return ownedChild.field_values.find(v =>
                  v.field_slug === fd.slug && v.language === (fd.is_localized ? uiLang : '')
                )?.value ?? ownedChild.field_values.find(v => v.field_slug === fd.slug)?.value ?? null
              },
              uiLang,
              ownedChild.children,
            )
            return rows.length > 0 ? (
              <div style={{ marginTop: '0.5rem', borderTop: '1px solid #f3f4f6', paddingTop: '0.5rem' }}>
                <PreviewTable borderless rows={rows} />
              </div>
            ) : null
          })()}

          {(pendingNew || selectExpanded) && subFields.length > 0 && (
            <div style={{ borderTop: '1px solid #e8e8e8', paddingTop: '0.5rem' }}>
              {subLanguages.length > 1 && (
                <div style={{ display: 'flex', gap: '0.25rem', marginBottom: '0.5rem' }}>
                  {subLanguages.map(l => (
                    <button key={l} type="button"
                      style={{ padding: '0.2rem 0.5rem', border: '1px solid #ccc', borderRadius: '4px 4px 0 0', cursor: 'pointer', fontSize: '0.78rem', background: selectActiveLang === l ? '#0066cc' : '#fff', color: selectActiveLang === l ? '#fff' : '#555' }}
                      onClick={() => setSelectActiveLang(l)}>
                      {l}
                    </button>
                  ))}
                </div>
              )}
              {subFields.map(subFd => {
                const subLabel = getLang(subFd.label as Record<string, string>, uiLang) || subFd.slug
                const langs = subFd.is_localized ? subLanguages.filter(Boolean) : ['']
                const sev = subFieldSeverities?.[subFd.slug]
                const subColor = subFieldColor(sev)
                const subMsgs = subFieldMessages?.[subFd.slug] ?? []
                const fieldValue = (lang: string) =>
                  selectDirty[subFd.slug] !== undefined
                    ? (subFd.is_localized
                        ? (selectDirty[subFd.slug] as Record<string, unknown>)?.[lang]
                        : selectDirty[subFd.slug])
                    : (ownedChild?.field_values.find(v => v.field_slug === subFd.slug && v.language === lang)?.value ?? null)
                if (compact) {
                  const tooltipId = `udm-sel-${fd.slug.replace(/[^a-z0-9]/gi, '-')}-${subFd.slug.replace(/[^a-z0-9]/gi, '-')}`
                  return (
                    <div key={subFd.slug} className={`${styles.fieldGroupCompact} ${sev ? (SUB_SEV_LEFT[sev] ?? '') : ''}`}>
                      <div className={styles.compactHeader}>
                        {sev && subMsgs.length > 0 && (
                          <>
                            <Tooltip target={`#${tooltipId}`} position="right">
                              <ul style={{ margin: 0, padding: '0 0 0 1rem', fontSize: '0.8rem', maxWidth: '260px' }}>
                                {subMsgs.map((m, i) => <li key={i}>{m.text}</li>)}
                              </ul>
                            </Tooltip>
                            <i id={tooltipId} className={`pi ${SUB_SEV_ICON[sev] ?? 'pi-info-circle'} ${styles.severityIcon} ${SUB_SEV_CLASS[sev] ?? ''}`} />
                          </>
                        )}
                        <span className={styles.compactLabel}>{subLabel}</span>
                      </div>
                      {wrapSelectCommit(subFd, langs.map(lang => (
                        <FieldInput key={lang || 'nolang'} fd={subFd}
                          value={fieldValue(lang)}
                          onChange={val => handleSelectFieldChange(subFd.slug, lang, val)}
                          disabled={disabled} lang={lang}
                          entityChildren={ownedChild?.children} nodeId={ownedChild?.id}
                          onEntityRefresh={onEntityRefresh} />
                      )))}
                    </div>
                  )
                }
                return (
                  <div key={subFd.slug} style={{
                    marginBottom: '0.6rem',
                    ...(sev ? { outline: `2px solid ${subColor}`, borderRadius: '4px', padding: '0.25rem' } : {}),
                  }}>
                    <div style={{ fontSize: '0.82rem', fontWeight: 600, color: sev ? subColor : '#444', marginBottom: '0.2rem' }}>
                      {subLabel} <span style={{ fontFamily: 'monospace', fontSize: '0.75rem', color: '#999' }}>({subFd.data_type})</span>
                    </div>
                    {wrapSelectCommit(subFd, langs.map(lang => (
                      <FieldInput
                        key={lang || 'nolang'}
                        fd={subFd}
                        value={fieldValue(lang)}
                        onChange={val => handleSelectFieldChange(subFd.slug, lang, val)}
                        disabled={disabled}
                        lang={lang}
                        entityChildren={ownedChild?.children}
                        nodeId={ownedChild?.id}
                        onEntityRefresh={onEntityRefresh}
                      />
                    )))}
                    {subMsgs.length > 0 && <PolicyMessageList messages={subMsgs} />}
                  </div>
                )
              })}
            </div>
          )}

          {!pendingNew && selectExpanded && !ownedChild && (
            <div style={{ fontSize: '0.82rem', color: '#888', fontStyle: 'italic', paddingTop: '0.5rem', borderTop: '1px solid #e8e8e8' }}>
              Referenced submodel is not directly owned by this entity — field editing not available here.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── FieldInputProps adapter ───────────────────────────────────────────────────
// SubmodelEditor wraps SubmodelEditorComponent to match the FieldInputProps interface.

function SubmodelEditorAdapter({ fd, value, onChange, disabled, lang = 'en', entityChildren, subFieldSeverities, subFieldMessages, resetKey, onEntityRefresh, compact, nodeId, onCommitOps }: FieldInputProps) {
  return (
    <SubmodelEditorComponent
      fd={fd}
      nodeId={nodeId}
      onCommitOps={onCommitOps}
      existingChildren={(entityChildren?.[fd.slug] ?? []) as unknown[]}
      existingValue={value}
      disabled={disabled}
      uiLang={lang || 'en'}
      onChange={onChange as (ops: unknown) => void}
      subFieldSeverities={subFieldSeverities}
      subFieldMessages={subFieldMessages}
      resetKey={resetKey}
      onEntityRefresh={onEntityRefresh}
      compact={compact}
    />
  )
}

fieldEditorRegistry.register(['submodel_list', 'submodel_select'], SubmodelEditorAdapter)
