import { useEffect, useState } from 'react'
import { udmGetTypeEditorTabConfig, udmPutTypeEditorTabConfig, UdmApiError } from '../apiUdm'
import type { TypeEditorTabProps } from './types'
import styles from '../UdmAdminPage.module.css'

/** Fallback for any type-editor tab id with no registered component
 *  (`typeEditorTabRegistry`). Reads/writes the tab's raw config JSON via
 *  the generic `GET/PUT /config-versions/{id}/tab-configs/{tab_id}/`
 *  endpoints — deliberately just a textarea, not a rich editor; a real
 *  plugin UI should register its own component instead. */
export function JsonTabFallback({ tabId, configVersionId }: TypeEditorTabProps) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    udmGetTypeEditorTabConfig(configVersionId, tabId)
      .then(res => { if (!cancelled) setText(JSON.stringify(res.config, null, 2)) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load tab config') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tabId, configVersionId])

  async function handleSave() {
    setError(null)
    setSuccess(null)
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(text) as Record<string, unknown>
    } catch {
      setError('Invalid JSON — fix the syntax before saving.')
      return
    }
    setSaving(true)
    try {
      const res = await udmPutTypeEditorTabConfig(configVersionId, tabId, parsed)
      setText(JSON.stringify(res.config, null, 2))
      setSuccess('Saved.')
    } catch (e) {
      setError(e instanceof UdmApiError ? e.allMessages.join('; ') : (e instanceof Error ? e.message : 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className={styles.emptyState}>Loading…</div>

  return (
    <div>
      <p style={{ color: '#888', fontSize: '0.85rem', marginTop: 0 }}>
        No dedicated editor is registered for the "{tabId}" tab yet — edit its raw config JSON directly.
      </p>
      <textarea
        className={styles.textarea}
        rows={14}
        value={text}
        onChange={e => setText(e.target.value)}
        spellCheck={false}
        style={{ fontFamily: 'monospace' }}
      />
      {error && <div className={styles.error}>{error}</div>}
      {success && <div className={styles.success}>{success}</div>}
      <div className={styles.row} style={{ marginTop: '0.5rem' }}>
        <button type="button" className={`${styles.btn} ${styles.btnPrimary}`}
          onClick={() => void handleSave()} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}
