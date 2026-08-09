import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { udmGetEntityBacklinks, type BacklinkOut } from '../apiUdm'

const STATE_COLORS: Record<string, { bg: string; color: string }> = {
  draft: { bg: '#f3f4f6', color: '#374151' },
  submitted: { bg: '#dbeafe', color: '#1e40af' },
  accepted: { bg: '#dcfce7', color: '#166534' },
  rejected: { bg: '#fee2e2', color: '#991b1b' },
}

function WorkflowBadge({ state }: { state: string | null }) {
  if (!state) return null
  const c = STATE_COLORS[state] ?? { bg: '#f3f4f6', color: '#374151' }
  return (
    <span style={{
      display: 'inline-block',
      padding: '0.05rem 0.5rem',
      borderRadius: '999px',
      fontSize: '0.72rem',
      fontWeight: 600,
      background: c.bg,
      color: c.color,
      marginLeft: '0.5rem',
    }}>
      {state}
    </span>
  )
}

export interface BacklinkListPreviewProps {
  entityId: string
  typeConfig: { source_type_ids?: string[]; source_field_slug?: string } | null | undefined
  /** Bump this to force a refetch — e.g. after a transition/action on this
   *  entity that may have created or removed a referencing entity elsewhere
   *  (a plain entityId dependency would miss that, since entityId itself
   *  doesn't change). */
  refreshToken?: number | string
}

/** events-and-sync.md §1.5: entities referencing entityId via an
 *  entity_select field, rendered as clickable preview rows. */
export function BacklinkListPreview({ entityId, typeConfig, refreshToken }: BacklinkListPreviewProps) {
  const navigate = useNavigate()
  const [backlinks, setBacklinks] = useState<BacklinkOut[] | null>(null)
  const sourceTypeIds = typeConfig?.source_type_ids
  const sourceFieldSlug = typeConfig?.source_field_slug

  useEffect(() => {
    let cancelled = false
    setBacklinks(null)
    udmGetEntityBacklinks(entityId, sourceTypeIds, sourceFieldSlug).then(rows => {
      if (!cancelled) setBacklinks(rows)
    }).catch(() => {
      if (!cancelled) setBacklinks([])
    })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, (sourceTypeIds ?? []).join(','), sourceFieldSlug, refreshToken])

  if (backlinks === null) {
    return <p style={{ color: '#9ca3af', fontSize: '0.85rem', margin: 0 }}>Loading…</p>
  }
  if (backlinks.length === 0) {
    return <p style={{ color: '#9ca3af', fontSize: '0.85rem', margin: 0 }}>No linked entities</p>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
      {backlinks.map(bl => (
        <button
          key={bl.id}
          type="button"
          onClick={() => navigate(`/udm-entity/${bl.id}`)}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            width: '100%',
            textAlign: 'left',
            padding: '0.4rem 0.6rem',
            border: '1px solid #e5e7eb',
            borderRadius: 6,
            background: '#fff',
            cursor: 'pointer',
            fontSize: '0.88rem',
            color: '#374151',
          }}
        >
          <span>{bl.preview || bl.id.slice(0, 8)}</span>
          <WorkflowBadge state={bl.workflow_state ?? null} />
        </button>
      ))}
    </div>
  )
}
