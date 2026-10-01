import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { translateApiError } from './apiError'
import mermaid from 'mermaid'
import {
  fetchEventTransitions,
  executeEventTransition,
  fetchEventFlowDiagram,
  type EventFlowDiagram,
  type EventTransition,
  type Event,
} from './api'
import { EventStatusBadge } from './EventStatusBadge'
import { confirmMailAction } from './mailConfirm'
import styles from './EventTransitionButtons.module.css'

mermaid.initialize({ startOnLoad: false, theme: 'default' })

let diagramCounter = 0

interface EventTransitionButtonsProps {
  seriesId: string
  eventId: string
  currentStatus: string
  onTransitionSuccess?: (updatedEvent: Event) => void
  onTransitionError?: (error: string) => void
}

export function EventTransitionButtons({
  seriesId,
  eventId,
  currentStatus,
  onTransitionSuccess,
  onTransitionError,
}: EventTransitionButtonsProps) {
  const { t, i18n } = useTranslation()
  const [transitions, setTransitions] = useState<EventTransition[]>([])
  const [status, setStatus] = useState(currentStatus)
  const [loading, setLoading] = useState(false)
  const [executing, setExecuting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [flowChartOpen, setFlowChartOpen] = useState(false)
  const [flowDiagram, setFlowDiagram] = useState<EventFlowDiagram | null>(null)
  const [flowChartLoading, setFlowChartLoading] = useState(false)
  const [flowChartError, setFlowChartError] = useState<string | null>(null)
  const [renderedSvg, setRenderedSvg] = useState<string | null>(null)
  const renderingRef = useRef(false)

  const loadTransitions = async () => {
    try {
      setLoading(true)
      setError(null)
      const data = await fetchEventTransitions(seriesId, eventId)
      setTransitions(data.transitions)
      setStatus(data.current_status)
    } catch (err) {
      setError(t('event.failedToLoadTransitions'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (seriesId && eventId) {
      void loadTransitions()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seriesId, eventId])

  useEffect(() => {
    setStatus(currentStatus)
  }, [currentStatus])

  // Re-render the diagram whenever the status, language, or diagram data changes
  useEffect(() => {
    if (!flowDiagram || !flowChartOpen) return
    void renderMermaid(flowDiagram, status)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, flowDiagram, flowChartOpen, i18n.language])

  const buildMermaidDefinition = (diagram: EventFlowDiagram, activeStatus: string): string => {
    const lines: string[] = ['flowchart TD']
    for (const node of diagram.nodes) {
      const label = t(`event.statusValues.${node}`, { defaultValue: node })
      const cls = node === activeStatus ? ':::activeState' : ''
      lines.push(`    ${node}["${label}"]${cls}`)
    }
    for (const edge of diagram.edges) {
      const label = t(`event.transition.${edge.label_id}`, { defaultValue: edge.label_id })
      lines.push(`    ${edge.source} -->|"${label}"| ${edge.target}`)
    }
    lines.push('    classDef activeState fill:#1565c0,stroke:#0d47a1,color:#fff,font-weight:bold')
    return lines.join('\n')
  }

  const renderMermaid = async (diagram: EventFlowDiagram, activeStatus: string) => {
    if (renderingRef.current) return
    renderingRef.current = true
    try {
      const definition = buildMermaidDefinition(diagram, activeStatus)
      const id = `event-flow-${++diagramCounter}`
      const { svg } = await mermaid.render(id, definition)
      setRenderedSvg(svg)
    } catch (err) {
      setFlowChartError(translateApiError(err instanceof Error ? err.message : undefined))
    } finally {
      renderingRef.current = false
    }
  }

  const handleToggleFlowChart = async () => {
    const nextOpen = !flowChartOpen
    setFlowChartOpen(nextOpen)
    if (nextOpen && flowDiagram === null && !flowChartLoading) {
      try {
        setFlowChartLoading(true)
        setFlowChartError(null)
        const diagram = await fetchEventFlowDiagram()
        setFlowDiagram(diagram)
      } catch (err) {
        setFlowChartError(translateApiError(err instanceof Error ? err.message : undefined))
      } finally {
        setFlowChartLoading(false)
      }
    }
  }

  const handleExecute = async (transition: EventTransition) => {
    const action = transition.action
    if (!confirmMailAction(t, transition.mail_warning_id)) {
      return
    }
    try {
      setExecuting(action)
      setError(null)
      const updatedEvent = await executeEventTransition(seriesId, eventId, action)
      setStatus(updatedEvent.status ?? status)
      await loadTransitions()
      onTransitionSuccess?.(updatedEvent)
    } catch (err) {
      const msg = translateApiError(err instanceof Error ? err.message : undefined)
      setError(msg)
      onTransitionError?.(msg)
    } finally {
      setExecuting(null)
    }
  }

  return (
    <div className={styles.section}>
      <div className={styles.statusRow}>
        <span className={styles.statusLabel}>{t('event.status')}:</span>
        <EventStatusBadge status={status} />
      </div>

      {error && (
        <div className={styles.errorBox} role="alert">
          {error}
        </div>
      )}

      {!loading && transitions.length > 0 && (
        <div className={styles.buttonRow}>
          {transitions.map((t_item) => (
            <button
              key={t_item.action}
              className={`${styles.transitionButton} ${executing !== null && executing !== t_item.action ? styles.busy : ''}`}
              onClick={() => void handleExecute(t_item)}
              disabled={!t_item.enabled || executing !== null}
              title={t_item.disable_reason ?? undefined}
              aria-busy={executing === t_item.action}
            >
              {executing === t_item.action ? t('event.processing') : t(`event.transition.${t_item.label_id}`, { defaultValue: t_item.label_id })}
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div className={styles.flowChartLoading}>{t('event.loadingTransitions')}</div>
      )}

      <button
        type="button"
        className={styles.flowChartToggle}
        onClick={() => void handleToggleFlowChart()}
        aria-expanded={flowChartOpen}
      >
        <span className={`${styles.flowChartArrow} ${flowChartOpen ? styles.open : ''}`}>▶</span>
        {t('event.lifecycleDiagram')}
      </button>

      {flowChartOpen && (
        <div className={styles.flowChartContainer} aria-label={t('event.lifecycleDiagramAria')}>
          {flowChartLoading && <p className={styles.flowChartLoading}>{t('event.loadingDiagram')}</p>}
          {flowChartError && <p className={styles.errorBox}>{flowChartError}</p>}
          {renderedSvg && (
            // SVG is generated locally by mermaid – safe to inject
            <div dangerouslySetInnerHTML={{ __html: renderedSvg }} />
          )}
        </div>
      )}
    </div>
  )
}
