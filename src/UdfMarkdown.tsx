import { useState, useEffect } from 'react'
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
    // Absorb any stray <until> / <query-policy-rule> that survive outside <countdown>
    until: (_p: Record<string, unknown>, _c: ElementContent[]) =>
      ({ type: 'element' as const, tagName: 'span', properties: {}, children: [] }),
    'query-policy-rule': (_p: Record<string, unknown>, _c: ElementContent[]) =>
      ({ type: 'element' as const, tagName: 'span', properties: {}, children: [] }),
  }
}

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
      } as Parameters<typeof ReactMarkdown>[0]['components']}
    >
      {content}
    </ReactMarkdown>
    </div>
  )
}
