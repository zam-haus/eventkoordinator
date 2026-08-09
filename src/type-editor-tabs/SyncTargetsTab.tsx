import { useEffect, useState } from 'react'
import {
  udmGetTypeEditorTabConfig, udmListSyncTargets, udmPutTypeEditorTabConfig, UdmApiError,
  type SyncTargetOut,
} from '../apiUdm'
import type { TypeEditorTabProps } from './types'
import styles from '../UdmAdminPage.module.css'

interface SyncTargetsTabConfig {
  target_keys: string[]
}

/** Dedicated editor for sync_core's "sync_targets" tab (events-and-sync.md
 *  §5/§8, Step 11): which SyncBaseTarget keys this type may mark_sync
 *  against. Targets are picked from the real SyncBaseTarget rows
 *  (GET /sync-targets/) rather than typed as raw "kind:slug" strings —
 *  targets themselves are still created in Django admin, this only binds
 *  existing ones to the type. */
export function SyncTargetsTab({ tabId, configVersionId }: TypeEditorTabProps) {
  const [keys, setKeys] = useState<string[]>([])
  const [targets, setTargets] = useState<SyncTargetOut[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([
      udmGetTypeEditorTabConfig(configVersionId, tabId),
      udmListSyncTargets(),
    ])
      .then(([tabConfig, targetList]) => {
        if (cancelled) return
        const config = tabConfig.config as Partial<SyncTargetsTabConfig>
        setKeys(Array.isArray(config.target_keys) ? config.target_keys : [])
        setTargets(targetList)
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load tab config') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tabId, configVersionId])

  async function save(nextKeys: string[]) {
    setError(null)
    setSuccess(null)
    setSaving(true)
    try {
      const res = await udmPutTypeEditorTabConfig(configVersionId, tabId, { target_keys: nextKeys })
      const config = res.config as Partial<SyncTargetsTabConfig>
      setKeys(Array.isArray(config.target_keys) ? config.target_keys : nextKeys)
      setSuccess('Saved.')
    } catch (e) {
      setError(e instanceof UdmApiError ? e.allMessages.join('; ') : (e instanceof Error ? e.message : 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  function handleAdd() {
    if (!selected || keys.includes(selected)) return
    const key = selected
    setSelected('')
    void save([...keys, key])
  }

  function handleRemove(key: string) {
    void save(keys.filter(k => k !== key))
  }

  function targetLabel(key: string): string {
    const target = targets.find(t => t.key === key)
    if (!target) return `${key} (target not found — was it deleted?)`
    return `${target.name} (${target.type}${target.enabled ? '' : ', disabled'})`
  }

  const availableTargets = targets.filter(t => !keys.includes(t.key))

  if (loading) return <div className={styles.emptyState}>Loading…</div>

  return (
    <div>
      <p style={{ color: '#888', fontSize: '0.85rem', marginTop: 0 }}>
        Sync targets this type's entities may be marked pending against via mark_sync.
        Targets themselves (URLs, credentials) are created in Django admin.
      </p>
      {keys.length === 0 && <div className={styles.emptyState}>No targets bound yet.</div>}
      {keys.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 0.75rem' }}>
          {keys.map(key => (
            <li key={key} className={styles.row} style={{ justifyContent: 'space-between', padding: '0.25rem 0' }}>
              <span>{targetLabel(key)}</span>
              <button type="button" className={styles.btn} disabled={saving} onClick={() => handleRemove(key)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {targets.length === 0 ? (
        <div className={styles.emptyState}>
          No sync targets exist yet — create one in Django admin (SyncBaseTarget) first.
        </div>
      ) : (
        <div className={styles.row}>
          <select value={selected} onChange={e => setSelected(e.target.value)} disabled={saving}>
            <option value="">Select a target…</option>
            {availableTargets.map(t => (
              <option key={t.key} value={t.key}>
                {t.name} — {t.type}{t.enabled ? '' : ' (disabled)'}
              </option>
            ))}
          </select>
          <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} disabled={saving || !selected} onClick={handleAdd}>
            Add
          </button>
        </div>
      )}
      {error && <div className={styles.error}>{error}</div>}
      {success && <div className={styles.success}>{success}</div>}
    </div>
  )
}
