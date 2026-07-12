import { useState } from 'react'
import { udmTransitionEntity } from '../apiUdm'
import type { WorkflowVersionOut, WorkflowTransitionOut, FieldDefinitionOut, PolicyMessage } from '../apiUdm'
import type { FieldInputProps } from './types'
import { getLang } from './types'
import { fieldEditorRegistry } from './registry'
import styles from '../UdmEntityEditor.module.css'

function WorkflowCellEditor({ fd, value, disabled, lang = '', nodeId, onEntityRefresh }: FieldInputProps) {
  const wfDef = (fd as FieldDefinitionOut & { workflow_version?: WorkflowVersionOut | null }).workflow_version
  const currentStateName = (value as string | null) ?? null
  const currentState = wfDef?.states.find(s => s.name === currentStateName) ?? null
  const stateLabel = currentState
    ? getLang(currentState.label as Record<string, string>, lang || 'en') || currentStateName
    : currentStateName
  const availableTransitions: WorkflowTransitionOut[] = (wfDef?.transitions ?? []).filter(t => {
    if (t.from_undefined_only) return currentStateName === null
    if (t.from_state !== null) return t.from_state === currentStateName
    return true
  })
  const [childTransitioning, setChildTransitioning] = useState(false)

  async function handleChildTransition(transitionName: string) {
    if (!nodeId || !onEntityRefresh) return
    setChildTransitioning(true)
    try {
      const result = await udmTransitionEntity(nodeId, fd.slug, transitionName)
      await onEntityRefresh((result.policy_messages ?? []) as PolicyMessage[])
    } finally {
      setChildTransitioning(false)
    }
  }

  const hasBg = currentState && currentState.background_color && currentState.background_color !== '#ffffff'
  const badgeBg = hasBg ? currentState!.background_color : (currentStateName ? '#f1f5f9' : '#f1f5f9')
  const badgeFg = hasBg ? currentState!.text_color : (currentStateName ? '#374151' : '#64748b')
  const badgeBorder = hasBg ? currentState!.background_color : (currentStateName ? '#d1d5db' : '#cbd5e1')

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
      <span style={{
        display: 'inline-block',
        padding: '0.2rem 0.7rem',
        borderRadius: '999px',
        fontSize: '0.82rem',
        fontWeight: 600,
        background: badgeBg,
        color: badgeFg,
        border: `1px solid ${badgeBorder}`,
        letterSpacing: '0.01em',
      }}>
        {stateLabel ?? '(no state)'}
      </span>
      {!nodeId && (
        <span style={{ fontSize: '0.78rem', color: '#888', fontStyle: 'italic' }}>
          Save this item first — workflow transitions become available after saving.
        </span>
      )}
      {nodeId && onEntityRefresh && availableTransitions.length > 0 && (
        <>
          <span style={{ color: '#111', fontSize: '1.1rem', fontWeight: 700, flexShrink: 0, userSelect: 'none', display: 'flex', alignItems: 'center' }}>→</span>
          {availableTransitions.map(t => {
            const tLabel = getLang(t.label as Record<string, string>, lang || 'en') || t.name
            return (
              <button
                key={t.name}
                type="button"
                className={styles.transitionBtn}
                disabled={disabled || childTransitioning}
                onClick={() => void handleChildTransition(t.name)}
              >
                {tLabel}
              </button>
            )
          })}
        </>
      )}
    </div>
  )
}

fieldEditorRegistry.register('workflow', WorkflowCellEditor)
