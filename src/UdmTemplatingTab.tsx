import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  udmCreateMailTemplate,
  udmDeleteMailTemplate,
  udmGetMailTemplate,
  udmListMailTemplates,
  udmPreviewMailTemplate,
  udmUpdateMailTemplate,
  type MailTemplateOut,
  type MailTemplatePreviewOut,
  type MailTemplateSummaryOut,
} from './apiUdm'
import styles from './UdmAdminPage.module.css'

const EMPTY: MailTemplateOut = {
  slug: '',
  description: '',
  subject: '',
  body_text: '',
  body_html: '',
  example_input: {},
}

const TEXT_PLACEHOLDER = `Hallo {{ user.username }},

deine Einreichung wurde am {{ entity.submitted_at | timezone("Europe/Berlin") | isoformat() }} empfangen.

Dein Text:

{{ fields.abstract | userinput }}
`

const HTML_PLACEHOLDER = `<p>Hallo {{ user.username }},</p>
<blockquote class="user-input">{{ fields.abstract | htmlquote }}</blockquote>
`

export default function TemplatingTab() {
  const [templates, setTemplates] = useState<MailTemplateSummaryOut[]>([])
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null)
  const [draft, setDraft] = useState<MailTemplateOut>(EMPTY)
  const [isNew, setIsNew] = useState(false)
  const [exampleJson, setExampleJson] = useState('{}')
  const [preview, setPreview] = useState<MailTemplatePreviewOut | null>(null)
  const [previewTab, setPreviewTab] = useState<'text' | 'html'>('text')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const loadList = useCallback(async () => {
    try {
      setTemplates(await udmListMailTemplates())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to list templates')
    }
  }, [])

  useEffect(() => { void loadList() }, [loadList])

  /** Parsed example JSON, or null while the editor content is invalid. */
  const exampleContext = useMemo(() => {
    try {
      const parsed: unknown = JSON.parse(exampleJson)
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return null
      return parsed as Record<string, unknown>
    } catch {
      return null
    }
  }, [exampleJson])

  // Live preview, debounced so typing doesn't hammer the endpoint.
  useEffect(() => {
    if (exampleContext === null) return
    const handle = setTimeout(() => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      void udmPreviewMailTemplate(
        {
          subject: draft.subject ?? '',
          body_text: draft.body_text ?? '',
          body_html: draft.body_html ?? '',
          context: exampleContext,
        },
        controller.signal,
      )
        .then(setPreview)
        .catch(() => { /* aborted or offline; keep the last preview */ })
    }, 400)
    return () => clearTimeout(handle)
  }, [draft.subject, draft.body_text, draft.body_html, exampleContext])

  async function selectTemplate(slug: string) {
    setError(null)
    setSuccess(null)
    try {
      const template = await udmGetMailTemplate(slug)
      setDraft(template)
      setSelectedSlug(slug)
      setIsNew(false)
      setExampleJson(JSON.stringify(template.example_input ?? {}, null, 2))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load template')
    }
  }

  function startNew() {
    setDraft(EMPTY)
    setSelectedSlug(null)
    setIsNew(true)
    setExampleJson('{}')
    setError(null)
    setSuccess(null)
  }

  async function handleSave() {
    setError(null)
    setSuccess(null)
    if (exampleContext === null) {
      setError('Example JSON is not valid JSON.')
      return
    }
    const body = {
      description: draft.description ?? '',
      subject: draft.subject ?? '',
      body_text: draft.body_text ?? '',
      body_html: draft.body_html ?? '',
      example_input: exampleContext,
    }
    try {
      if (isNew) {
        const slug = draft.slug.trim()
        if (!slug) { setError('Slug is required.'); return }
        await udmCreateMailTemplate({ slug, ...body })
        setIsNew(false)
        setSelectedSlug(slug)
      } else if (selectedSlug) {
        await udmUpdateMailTemplate(selectedSlug, body)
      }
      setSuccess('Template saved.')
      void loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    }
  }

  async function handleDelete() {
    if (!selectedSlug) return
    if (!confirm(`Delete template "${selectedSlug}"?`)) return
    try {
      await udmDeleteMailTemplate(selectedSlug)
      setSelectedSlug(null)
      setDraft(EMPTY)
      void loadList()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  const editing = isNew || selectedSlug !== null

  return (
    <div className={styles.row} style={{ alignItems: 'flex-start', gap: '1.5rem' }}>
      <div style={{ flex: '0 0 16rem' }}>
        <div className={styles.section}>
          <div className={styles.sectionTitle}>Templates</div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {templates.map(t => (
              <li key={t.slug}>
                <button
                  type="button"
                  className={styles.btn}
                  style={{ width: '100%', textAlign: 'left', marginBottom: '0.25rem' }}
                  aria-current={t.slug === selectedSlug}
                  onClick={() => void selectTemplate(t.slug)}
                >
                  {t.slug}
                </button>
              </li>
            ))}
            {templates.length === 0 && <li className={styles.label}>No templates yet.</li>}
          </ul>
          <button
            type="button"
            className={`${styles.btn} ${styles.btnPrimary}`}
            style={{ marginTop: '0.75rem' }}
            onClick={startNew}
          >
            + New Template
          </button>
        </div>
      </div>

      {editing && (
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className={styles.section}>
            <div className={styles.sectionTitle}>{isNew ? 'New Template' : draft.slug}</div>
            <div className={styles.formGroup}>
              <label className={styles.label} htmlFor="tpl-slug">Slug *</label>
              <input
                id="tpl-slug"
                className={styles.input}
                value={draft.slug}
                disabled={!isNew}
                onChange={e => setDraft({ ...draft, slug: e.target.value })}
                placeholder="proposal-submitted-owner"
              />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label} htmlFor="tpl-description">Description</label>
              <input
                id="tpl-description"
                className={styles.input}
                value={draft.description ?? ''}
                onChange={e => setDraft({ ...draft, description: e.target.value })}
              />
            </div>
            <div className={styles.formGroup}>
              <label className={styles.label} htmlFor="tpl-subject">Subject</label>
              <input
                id="tpl-subject"
                className={styles.input}
                value={draft.subject ?? ''}
                onChange={e => setDraft({ ...draft, subject: e.target.value })}
              />
            </div>
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>Plaintext body</div>
            <textarea
              aria-label="Plaintext body"
              className={`${styles.textarea} ${styles.editorTextarea}`}
              rows={14}
              value={draft.body_text ?? ''}
              placeholder={TEXT_PLACEHOLDER}
              onChange={e => setDraft({ ...draft, body_text: e.target.value })}
            />
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>HTML body</div>
            <textarea
              aria-label="HTML body"
              className={`${styles.textarea} ${styles.editorTextarea}`}
              rows={16}
              value={draft.body_html ?? ''}
              placeholder={HTML_PLACEHOLDER}
              onChange={e => setDraft({ ...draft, body_html: e.target.value })}
            />
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>Example input (JSON)</div>
            <textarea
              aria-label="Example input JSON"
              className={`${styles.textarea} ${styles.editorTextarea}`}
              rows={10}
              value={exampleJson}
              onChange={e => setExampleJson(e.target.value)}
            />
            {exampleContext === null && (
              <div className={styles.error}>Not valid JSON — preview paused.</div>
            )}
          </div>

          <div className={styles.section}>
            <div className={styles.sectionTitle}>Preview</div>
            <div className={styles.row}>
              <button
                type="button"
                className={`${styles.btn} ${previewTab === 'text' ? styles.btnPrimary : ''}`}
                onClick={() => setPreviewTab('text')}
              >
                Text
              </button>
              <button
                type="button"
                className={`${styles.btn} ${previewTab === 'html' ? styles.btnPrimary : ''}`}
                onClick={() => setPreviewTab('html')}
              >
                HTML
              </button>
            </div>
            {preview?.error && <div className={styles.error}>{preview.error}</div>}
            {preview?.subject && (
              <div className={styles.label} style={{ marginTop: '0.75rem' }}>
                Subject: {preview.subject}
              </div>
            )}
            {previewTab === 'text' ? (
              <pre className={styles.previewPre}>{preview?.text ?? ''}</pre>
            ) : (
              // Rendered mail HTML is untrusted: an opaque-origin sandbox with no
              // allow-* tokens means no scripts, no forms and no navigation.
              <iframe
                title="HTML preview"
                className={styles.previewFrame}
                sandbox=""
                referrerPolicy="no-referrer"
                srcDoc={preview?.html ?? ''}
              />
            )}
          </div>

          {error && <div className={styles.error}>{error}</div>}
          {success && <div className={styles.success}>{success}</div>}
          <div className={styles.row}>
            <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={handleSave}>
              Save
            </button>
            {!isNew && selectedSlug && (
              <button type="button" className={`${styles.btn} ${styles.btnDanger}`} onClick={handleDelete}>
                Delete
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
