import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import rehypeComponents from 'rehype-components'
import type { Element, ElementContent } from 'hast'
import { udmGetTypePublicFields } from './apiUdm'

// ── Until-descriptor: recursive serialisation of <until> children ─────────────
//
// The hast tree is walked once (synchronously, in rehype-components) and turned
// into a plain JSON array.  Each element in the array is one of:
//   { t: 'text',  v: string }            – literal text node
//   { t: 'rule',  n: string }            – <query-policy-rule name="n"/>
//
// Any other element is descended into so its text / rule leaf nodes are
// captured (the recursive case).  The CountdownBadge then resolves the
// descriptors asynchronously at render time to obtain the final date string.

type UntilDescriptor =
  | { t: 'text'; v: string }
  | { t: 'rule'; n: string }

function extractDescriptors(nodes: ElementContent[]): UntilDescriptor[] {
  const out: UntilDescriptor[] = []
  for (const node of nodes) {
    if (node.type === 'text') {
      const v = node.value.trim()
      if (v) out.push({ t: 'text', v })
    } else if (node.type === 'element') {
      const el = node as Element
      if (el.tagName === 'query-policy-rule') {
        const n = String(el.properties?.name ?? '')
        if (n) out.push({ t: 'rule', n })
      } else {
        // Recurse into any other element (e.g. <span>, <strong>, …)
        out.push(...extractDescriptors(el.children ?? []))
      }
    }
  }
  return out
}

async function resolveDescriptors(
  descriptors: UntilDescriptor[],
  typeId: string,
): Promise<string> {
  // Fetch policy fields once for all rule descriptors in this batch.
  const hasRules = descriptors.some(d => d.t === 'rule')
  const fields = hasRules ? await udmGetTypePublicFields(typeId) : {}
  return descriptors
    .map(d => (d.t === 'text' ? d.v : String(fields[d.n] ?? '')))
    .join('')
    .trim()
}

// ── Countdown badge ───────────────────────────────────────────────────────────

function CountdownBadge({ typeId, untilJson }: { typeId: string; untilJson: string }) {
  const [daysLeft, setDaysLeft] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    const descriptors: UntilDescriptor[] = JSON.parse(untilJson)
    resolveDescriptors(descriptors, typeId).then(dateStr => {
      if (cancelled || !dateStr) return
      const target = new Date(dateStr)
      const today = new Date()
      today.setHours(0, 0, 0, 0)
      target.setHours(0, 0, 0, 0)
      if (!isNaN(target.getTime()))
        setDaysLeft(Math.round((target.getTime() - today.getTime()) / 86_400_000))
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [typeId, untilJson])

  if (loading) return <span style={badgeStyle('#e5e7eb', '#6b7280')}>…</span>
  if (daysLeft === null) return <span style={badgeStyle('#e5e7eb', '#6b7280')}>—</span>

  const overdue = daysLeft < 0
  const urgent  = daysLeft >= 0 && daysLeft <= 7
  const bg    = overdue ? '#fee2e2' : urgent ? '#fef3c7' : '#dbeafe'
  const color = overdue ? '#991b1b' : urgent ? '#92400e' : '#1e40af'
  const label = overdue
    ? `${-daysLeft} day${daysLeft !== -1 ? 's' : ''} overdue`
    : daysLeft === 0 ? 'Due today'
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
      .then(fields => { const v = fields[ruleName]; if (v !== undefined) setValue(String(v)) })
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
      // Find <until>, recursively extract its descriptors, serialise to JSON.
      let descriptors: UntilDescriptor[] = []
      for (const child of children) {
        if ((child as Element).tagName === 'until') {
          descriptors = extractDescriptors((child as Element).children ?? [])
          break
        }
      }
      return {
        type: 'element' as const,
        tagName: 'countdown-badge',
        properties: {
          'data-type-id': typeId,
          'data-until': JSON.stringify(descriptors),
        },
        children: [],
      }
    },

    // Standalone <query-policy-rule> outside <countdown>: show the raw value.
    'query-policy-rule': (props: Record<string, unknown>, _c: ElementContent[]) => ({
      type: 'element' as const,
      tagName: 'policy-rule-value',
      properties: { 'data-type-id': typeId, 'data-rule': String(props.name ?? '') },
      children: [],
    }),

    // <until> surviving outside <countdown> is meaningless — absorb silently.
    until: (_p: Record<string, unknown>, _c: ElementContent[]) =>
      ({ type: 'element' as const, tagName: 'span', properties: {}, children: [] }),
  }
}

// ── Insert-snippet toolbar ────────────────────────────────────────────────────

interface Snippet {
  label: string
  text: string
  selectOffset: number
  selectLength: number
}

const SNIPPETS: Snippet[] = [
  {
    label: 'Countdown badge',
    text: '<countdown><until><query-policy-rule name="get_countdown_date"/></until></countdown>',
    selectOffset: '<countdown><until><query-policy-rule name="'.length,
    selectLength: 'get_countdown_date'.length,
  },
]

// ── Public components ─────────────────────────────────────────────────────────

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
            const tid  = String((node as Element | undefined)?.properties?.['data-type-id'] ?? props['data-type-id'] ?? typeId)
            const until = String((node as Element | undefined)?.properties?.['data-until']   ?? props['data-until']   ?? '[]')
            if (!tid) return null
            return <CountdownBadge typeId={tid} untilJson={until} />
          },
          'policy-rule-value': ({ node, ...props }) => {
            const tid  = String((node as Element | undefined)?.properties?.['data-type-id'] ?? props['data-type-id'] ?? typeId)
            const rule = String((node as Element | undefined)?.properties?.['data-rule']    ?? props['data-rule']    ?? '')
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
      el.selectionEnd   = start + snippet.selectOffset + snippet.selectLength
    })
  }

  return (
    <div style={{ width: '100%' }}>
      <div style={{ marginBottom: '0.25rem' }}>
        <select
          value=""
          onChange={e => { const s = SNIPPETS.find(x => x.label === e.target.value); if (s) insertSnippet(s) }}
          style={{ fontSize: '0.8rem', padding: '0.2rem 0.4rem', border: '1px solid #cbd5e1', borderRadius: '4px', background: '#fff', cursor: 'pointer' }}
        >
          <option value="" disabled>Insert component…</option>
          {SNIPPETS.map(s => <option key={s.label} value={s.label}>{s.label}</option>)}
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
