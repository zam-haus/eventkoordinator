import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  fetchProposalTransitions,
  submitProposal,
  acceptProposal,
  rejectProposal,
  reviseProposal,
  submitProposalOnBehalf,
  undoAcceptProposal,
  type ProposalTransition,
  type ProposalDetail,
} from './api'
import { translateApiError } from './apiError'
import styles from './ProposalTransitionButtons.module.css'

interface ProposalTransitionButtonsProps {
  proposalId: string
  onTransitionSuccess?: (updatedProposal: ProposalDetail) => void
  onTransitionError?: (error: string) => void
}

export function ProposalTransitionButtons({
  proposalId,
  onTransitionSuccess,
  onTransitionError,
}: ProposalTransitionButtonsProps) {
  const { t } = useTranslation()
  const [transitions, setTransitions] = useState<ProposalTransition[]>([])
  const [loading, setLoading] = useState(false)
  const [executing, setExecuting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Load transitions on mount and when proposalId changes
  useEffect(() => {
    const loadTransitions = async () => {
      try {
        setLoading(true)
        setError(null)
        const data = await fetchProposalTransitions(proposalId)
        setTransitions(data.transitions)
      } catch (err) {
        const errorMsg = translateApiError(err instanceof Error ? err.message : undefined)
        setError(errorMsg)
        console.error('Failed to load transitions:', err)
      } finally {
        setLoading(false)
      }
    }

    if (proposalId && proposalId.trim()) {
      loadTransitions()
    }
  }, [proposalId])

  const executeTransition = async (action: string) => {
    try {
      setExecuting(action)
      setError(null)

      let result: ProposalDetail
      switch (action) {
        case 'submit':
          result = await submitProposal(proposalId)
          break
        case 'accept':
          result = await acceptProposal(proposalId)
          break
        case 'reject':
          result = await rejectProposal(proposalId)
          break
        case 'revise':
          result = await reviseProposal(proposalId)
          break
        case 'submit_on_behalf':
          result = await submitProposalOnBehalf(proposalId)
          break
        case 'undo_accept':
          result = await undoAcceptProposal(proposalId)
          break
        default:
          throw new Error(`Unknown action: ${action}`)
      }

      // Reload transitions after success
      try {
        const data = await fetchProposalTransitions(proposalId)
        setTransitions(data.transitions)
      } catch (err) {
        console.error('Failed to reload transitions:', err)
      }

      if (onTransitionSuccess) {
        onTransitionSuccess(result)
      }
      } catch (err) {
        const errorMsg = translateApiError(err instanceof Error ? err.message : undefined)
        setError(errorMsg)
        if (onTransitionError) {
          onTransitionError(errorMsg)
        }
        console.error(`Failed to execute ${action}:`, err)
    } finally {
      setExecuting(null)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: '0.5rem 0', color: '#666', fontSize: '0.9rem' }}>
        {t('common.loading')}
      </div>
    )
  }

  if (transitions.length === 0) {
    return null
  }

  return (
    <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {error && (
        <div className={styles.errorBox}>
          {t('common.error')}: {error}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {transitions.map((transition) => (
          <button
            key={transition.action}
            onClick={() => executeTransition(transition.action)}
            disabled={!transition.enabled || executing !== null}
            style={{
              padding: '0.6rem 1.2rem',
              borderRadius: '4px',
              border: '1px solid #ddd',
              backgroundColor: transition.enabled ? '#2196f3' : '#e0e0e0',
              color: transition.enabled ? '#fff' : '#999',
              cursor: transition.enabled && executing === null ? 'pointer' : 'not-allowed',
              fontSize: '0.9rem',
              fontWeight: 600,
              transition: 'all 0.2s ease',
              opacity: executing !== null && executing !== transition.action ? 0.5 : 1,
            }}
            title={transition.disable_reason || undefined}
          >
            {executing === transition.action ? t('common.processing') : t(`proposal.transition.${transition.label_id}`, { defaultValue: transition.label_id })}
          </button>
        ))}
      </div>
    </div>
  )
}

