import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import rehypeComponents from 'rehype-components'
import type { Element, ElementContent } from 'hast'

// ── Pill badge ────────────────────────────────────────────────────────────────

const PILL_COLORS: Record<string, { bg: string; color: string }> = {
  blue:   { bg: '#dbeafe', color: '#1e40af' },
  green:  { bg: '#dcfce7', color: '#166534' },
  yellow: { bg: '#fef9c3', color: '#854d0e' },
  red:    { bg: '#fee2e2', color: '#991b1b' },
  gray:   { bg: '#f3f4f6', color: '#374151' },
}

function Pill({ color = 'blue', children }: { color?: string; children?: React.ReactNode }) {
  const { bg, col } = (() => {
    const c = PILL_COLORS[color] ?? PILL_COLORS.blue
    return { bg: c.bg, col: c.color }
  })()
  return (
    <span style={{
      display: 'inline-block',
      padding: '0.1rem 0.55rem',
      borderRadius: '999px',
      fontSize: '0.78rem',
      fontWeight: 600,
      background: bg,
      color: col,
      border: `1px solid ${col}`,
      lineHeight: 1.4,
      verticalAlign: 'middle',
    }}>
      {children}
    </span>
  )
}

// ── rehype-components config ──────────────────────────────────────────────────

const rehypeComponentMap = {
  pill: (props: Record<string, unknown>, children: ElementContent[]) => ({
    type: 'element' as const,
    tagName: 'pill-badge',
    properties: { 'data-color': String(props.color ?? 'blue') },
    // Carry text children through as hast text nodes
    children,
  }),
}

// ── Public component ──────────────────────────────────────────────────────────

interface UdfMarkdownProps {
  content: string
  className?: string
}

export function UdfMarkdown({ content, className }: UdfMarkdownProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        rehypePlugins={[rehypeRaw, [rehypeComponents, { components: rehypeComponentMap }]]}
        components={{
          'pill-badge': ({ node, children }: { node?: unknown; children?: React.ReactNode }) => {
            const color = String((node as Element | undefined)?.properties?.['data-color'] ?? 'blue')
            return <Pill color={color}>{children}</Pill>
          },
        } as Parameters<typeof ReactMarkdown>[0]['components']}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
