import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import rehypeComponents from 'rehype-components'
import type { Element, ElementContent } from 'hast'
import { udmGetTypePublicFields } from './apiUdm'

// ── Countdown badge ───────────────────────────────────────────────────────────

interface CountdownBadgeProps {
  typeId: string
  ruleName: string
}

function CountdownBadge({ typeId, ruleName }: CountdownBadgeProps) {
  const [daysLeft, setDaysLeft] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    udmGetTypePublicFields(typeId)
      .then(fields => {
        const val = fields[ruleName]
        if (typeof val === 'string') {
          const target = new Date(val)
          const today = new Date()
          today.setHours(0, 0, 0, 0)
          target.setHours(0, 0, 0, 0)
          setDaysLeft(Math.round((target.getTime() - today.getTime()) / 86_400_000))
        }
      })
      .finally(() => setLoading(false))
  }, [typeId, ruleName])

  if (loading) return <span style={badgeStyle('#e5e7eb', '#6b7280')}>…</span>
  if (daysLeft === null) return <span style={badgeStyle('#e5e7eb', '#6b7280')}>—</span>

  const overdue = daysLeft < 0
  const urgent = daysLeft >= 0 && daysLeft <= 7
  const bg = overdue ? '#fee2e2' : urgent ? '#fef3c7' : '#dbeafe'
  const color = overdue ? '#991b1b' : urgent ? '#92400e' : '#1e40af'
  const label = overdue
    ? `${-daysLeft} day${daysLeft !== -1 ? 's' : ''} overdue`
    : daysLeft === 0
    ? 'Due today'
    : `${daysLeft} day${daysLeft !== 1 ? 's' : ''} left`

  return <span style={badgeStyle(bg, color)}>{label}</span>
}

function badgeStyle(bg: string, color: string): React.CSSProperties {
  return {
    display: 'inline-block',
    padding: '0.1rem 0.5rem',
    borderRadius: '999px',
    fontSize: '0.78rem',
    fontWeight: 600,
    background: bg,
    color,
    border: '1px solid',
    borderColor: color,
    lineHeight: 1.4,
    verticalAlign: 'middle',
  }
}

// ── Policy rule value (standalone <query-policy-rule>) ────────────────────────

function PolicyRuleValue({ typeId, ruleName }: { typeId: string; ruleName: string }) {
  const [value, setValue] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    udmGetTypePublicFields(typeId)
      .then(fields => {
        const val = fields[ruleName]
        if (val !== undefined) setValue(String(val))
      })
      .finally(() => setLoading(false))
  }, [typeId, ruleName])

  if (loading) return <span style={{ color: '#9ca3af', fontStyle: 'italic' }}>…</span>
  if (value === null) return <span style={{ color: '#9ca3af', fontStyle: 'italic' }}>—</span>
  return <span>{value}</span>
}

// ── rehype-components config ──────────────────────────────────────────────────

function makeRehypeComponents(typeId: string) {
  return {
    countdown: (_props: Record<string, unknown>, children: ElementContent[]) => {
      // Traverse <until> → <query-policy-rule name="...">
      let ruleName = ''
      for (const child of children) {
        if ((child as Element).tagName === 'until') {
          for (const inner of (child as Element).children ?? []) {
            if ((inner as Element).tagName === 'query-policy-rule') {
              ruleName = String((inner as Element).properties?.name ?? '')
            }
          }
        }
      }
      return {
        type: 'element' as const,
        tagName: 'countdown-badge',
        properties: { 'data-type-id': typeId, 'data-rule': ruleName },
        children: [],
      }
    },
    // Standalone <query-policy-rule> outside <countdown> renders the raw value inline.
    'query-policy-rule': (props: Record<string, unknown>, _c: ElementContent[]) => ({
      type: 'element' as const,
      tagName: 'policy-rule-value',
      properties: { 'data-type-id': typeId, 'data-rule': String(props.name ?? '') },
      children: [],
    }),
    // <until> that survives outside <countdown> is meaningless — absorb it.
    until: (_p: Record<string, unknown>, _c: ElementContent[]) =>
      ({ type: 'element' as const, tagName: 'span', properties: {}, children: [] }),
  }
}

// ── Insert-snippet toolbar ────────────────────────────────────────────────────

interface Snippet {
  label: string
  /** Text inserted at the cursor. */
  text: string
  /** Offset from the insertion point to start the post-insert selection (for easy fill-in). */
  selectOffset: number
  /** Length of the post-insert selection. */
  selectLength: number
}

const SNIPPETS: Snippet[] = [
  {
    label: 'Countdown badge',
    // <countdown><until><query-policy-rule name="get_countdown_date"/></until></countdown>
    text: '<countdown><until><query-policy-rule name="get_countdown_date"/></until></countdown>',
    // select the rule name value so the user can type to replace it
    selectOffset: '<countdown><until><query-policy-rule name="'.length,
    selectLength: 'get_countdown_date'.length,
  },
]

// ── Public component ──────────────────────────────────────────────────────────

interface UdfMarkdownProps {
  content: string
  typeId?: string
  className?: string
}

export function UdfMarkdown({ content, typeId = '', className }: UdfMarkdownProps) {
  const rehypePlugins: Parameters<typeof ReactMarkdown>[0]['rehypePlugins'] = [
    rehypeRaw,
    [rehypeComponents, { components: makeRehypeComponents(typeId) }],
  ]

  return (
    <div className={className}>
    <ReactMarkdown
      rehypePlugins={rehypePlugins}
      components={{
        'countdown-badge': ({ node, ...props }) => {
          const tid = String((node as Element | undefined)?.properties?.['data-type-id'] ?? props['data-type-id'] ?? typeId)
          const rule = String((node as Element | undefined)?.properties?.['data-rule'] ?? props['data-rule'] ?? '')
          if (!tid || !rule) return null
          return <CountdownBadge typeId={tid} ruleName={rule} />
        },
        'policy-rule-value': ({ node, ...props }) => {
          const tid = String((node as Element | undefined)?.properties?.['data-type-id'] ?? props['data-type-id'] ?? typeId)
          const rule = String((node as Element | undefined)?.properties?.['data-rule'] ?? props['data-rule'] ?? '')
          if (!tid || !rule) return null
          return <PolicyRuleValue typeId={tid} ruleName={rule} />
        },
      } as Parameters<typeof ReactMarkdown>[0]['components']}
    >
      {content}
    </ReactMarkdown>
    </div>
  )
}

// ── Markdown editor with insert toolbar ───────────────────────────────────────

interface MarkdownEditorProps {
  value: string
  onChange: (v: string) => void
  rows?: number
  textareaClassName?: string
  textareaStyle?: React.CSSProperties
}

export function MarkdownEditor({ value, onChange, rows = 4, textareaClassName, textareaStyle }: MarkdownEditorProps) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  // Save cursor/selection on every interaction so it survives focus loss to the dropdown.
  const savedSel = useRef({ start: 0, end: 0 })

  function saveSel() {
    const el = taRef.current
    if (el) savedSel.current = { start: el.selectionStart, end: el.selectionEnd }
  }

  function insertSnippet(snippet: Snippet) {
    const { start, end } = savedSel.current
    const next = value.slice(0, start) + snippet.text + value.slice(end)
    onChange(next)
    requestAnimationFrame(() => {
      const el = taRef.current
      if (!el) return
      el.focus()
      el.selectionStart = start + snippet.selectOffset
      el.selectionEnd = start + snippet.selectOffset + snippet.selectLength
    })
  }

  return (
    <div style={{ width: '100%' }}>
      <div style={{ marginBottom: '0.25rem' }}>
        <select
          value=""
          onChange={e => {
            const snippet = SNIPPETS.find(s => s.label === e.target.value)
            if (snippet) insertSnippet(snippet)
          }}
          style={{ fontSize: '0.8rem', padding: '0.2rem 0.4rem', border: '1px solid #cbd5e1', borderRadius: '4px', background: '#fff', cursor: 'pointer' }}
        >
          <option value="" disabled>Insert component…</option>
          {SNIPPETS.map(s => (
            <option key={s.label} value={s.label}>{s.label}</option>
          ))}
        </select>
      </div>
      <textarea
        ref={taRef}
        value={value}
        rows={rows}
        className={textareaClassName}
        style={{ width: '100%', boxSizing: 'border-box', ...textareaStyle }}
        onChange={e => { onChange(e.target.value); saveSel() }}
        onSelect={saveSel}
        onClick={saveSel}
        onKeyUp={saveSel}
      />
    </div>
  )
}
