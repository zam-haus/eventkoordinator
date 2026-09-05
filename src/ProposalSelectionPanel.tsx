import {useTranslation} from "react-i18next";
import {useCallback, useEffect, useState} from "react";
import {usePermissions} from "./usePermissions.tsx";
import {useNavigate, useParams} from "react-router-dom";
import {
    checkObjectPermission,
    copyProposal,
    createProposal,
    deleteProposal,
    fetchProposalTransitions,
    searchProposals
} from "./api.tsx";
import styles from "./SelectionPanel.module.css";
import {ProposalEditor} from "./ProposalEditor.tsx";
import {type SelectionItem, SelectionPanel} from "./SelectionPanel.tsx";

export interface ProposalItem extends SelectionItem {
    id: string
    proposalId: string
    title: string
    submission_type: string
    workflow_status?: string
}

interface ProposalSelectionPanelProps {
    onCreateProposal?: () => void
    onProposalSave?: (formData: unknown) => void
}

export function ProposalSelectionPanel({onCreateProposal, onProposalSave}: ProposalSelectionPanelProps) {
    const {t} = useTranslation()
    const [proposals, setProposals] = useState<ProposalItem[]>([])
    const [loading, setLoading] = useState(true)
    const [selectedProposalId, setSelectedProposalId] = useState<string>('')
    const [isCreating, setIsCreating] = useState(false)
    const [createError, setCreateError] = useState<string | null>(null)
    const [canViewSelectedProposal, setCanViewSelectedProposal] = useState<boolean | null>(null)
    const [canEditSelectedProposal, setCanEditSelectedProposal] = useState<boolean | null>(null)
    const [canDeleteSelectedProposal, setCanDeleteSelectedProposal] = useState(false)
    const [editorPermissionLoading, setEditorPermissionLoading] = useState(false)
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
    const {canAdd, canBrowse} = usePermissions()
    const navigate = useNavigate()
    const {proposalId: urlProposalId} = useParams<{ proposalId?: string }>()

    const formatWorkflowStatus = (status?: string) => {
        if (!status) return t('proposal.statusValues.unknown')
        return t(`proposal.statusValues.${status}`, {defaultValue: status.replace(/_/g, ' ')})
    }

    const refreshWorkflowStatuses = useCallback(async (proposalItems: ProposalItem[]) => {
        const realItems = proposalItems.filter((p) => p.proposalId !== '')
        if (realItems.length === 0) {
            return
        }

        const statusEntries = await Promise.all(
            realItems.map(async (item) => {
                try {
                    const transitions = await fetchProposalTransitions(item.proposalId)
                    return [item.id, transitions.current_status] as const
                } catch (statusError) {
                    console.warn(`Failed to load workflow status for proposal ${item.proposalId}:`, statusError)
                    return [item.id, item.workflow_status || 'unknown'] as const
                }
            })
        )

        const statusById = new Map(statusEntries)
        setProposals((prev) => prev.map((item) => {
            if (!statusById.has(item.id)) {
                return item
            }
            return {
                ...item,
                workflow_status: statusById.get(item.id) || 'unknown',
            }
        }))
    }, [])

    // Load proposals helper (used on mount and after saves)
    const loadProposals = useCallback(async () => {
        try {
            setLoading(true)
            const results = await searchProposals('')
            const items: ProposalItem[] = results.map((p) => ({
                id: String(p.id),
                proposalId: p.id,
                title: p.title,
                submission_type: p.submission_type,
                workflow_status: 'unknown',
            }))

            if (items.length === 0) {
                const emptyItem: ProposalItem = {
                    id: 'empty',
                    proposalId: '',
                    title: t('selection.noProposalsYet'),
                    submission_type: t('selection.clickCreateNewProposal'),
                }
                setProposals([emptyItem])
                setSelectedProposalId('empty')
            } else {
                setProposals(items)
                void refreshWorkflowStatuses(items)
                // Determine effective selection from URL, falling back to first proposal
                const effectiveProposalId = (urlProposalId && items.some((p) => p.id === urlProposalId))
                    ? urlProposalId
                    : items[0].id
                setSelectedProposalId(effectiveProposalId)
            }
        } catch (error) {
            console.error('Failed to load proposals:', error)
        } finally {
            setLoading(false)
        }
    }, [refreshWorkflowStatuses, urlProposalId, t])

    useEffect(() => {
        void loadProposals()
    }, [loadProposals])

    useEffect(() => {
        if (!urlProposalId && proposals.length > 0) {
            navigate(`/proposal-editor/${proposals[0].id}`, {replace: true})
        }
    }, [urlProposalId, proposals, navigate])

    const checkSelectedProposalPermissions = useCallback(async () => {
        const selected = proposals.find((item) => item.id === selectedProposalId)
        if (!selected || !selected.proposalId) {
            setCanViewSelectedProposal(true)
            setCanEditSelectedProposal(true)
            setCanDeleteSelectedProposal(false)
            return
        }

        setEditorPermissionLoading(true)
        try {
            const [canView, canChange, canDelete] = await Promise.all([
                checkObjectPermission({
                    app: 'apiv1',
                    action: 'view',
                    object_type: 'proposal',
                    object_id: selected.proposalId,
                }),
                checkObjectPermission({
                    app: 'apiv1',
                    action: 'change',
                    object_type: 'proposal',
                    object_id: selected.proposalId,
                }),
                checkObjectPermission({
                    app: 'apiv1',
                    action: 'delete',
                    object_type: 'proposal',
                    object_id: selected.proposalId,
                }),
            ])
            setCanViewSelectedProposal(canView)
            setCanEditSelectedProposal(canChange)
            setCanDeleteSelectedProposal(canDelete)
        } finally {
            setEditorPermissionLoading(false)
        }
    }, [proposals, selectedProposalId])

    useEffect(() => {
        void checkSelectedProposalPermissions()
    }, [checkSelectedProposalPermissions])

    const handleCreateProposal = async () => {
        if (hasUnsavedChanges) {
            const confirmed = window.confirm(t('selection.confirmUnsavedSwitch'))
            if (!confirmed) {
                return
            }
        }

        try {
            setIsCreating(true)
            setCreateError(null)

            const newProposal = await createProposal()

            // Add new proposal to the list
            const newItem: ProposalItem = {
                id: String(newProposal.id),
                proposalId: String(newProposal.id),
                title: newProposal.title,
                submission_type: newProposal.submission_type,
                workflow_status: 'draft',
            }

            setProposals((prev) => [newItem, ...prev])
            setSelectedProposalId(newItem.id)
            navigate(`/proposal-editor/${newItem.id}`, {replace: true})

            if (onCreateProposal) {
                onCreateProposal()
            }
        } catch (error) {
            setCreateError(t('selection.createFailed'))
            console.error('Failed to create proposal:', error)
        } finally {
            setIsCreating(false)
        }
    }

    const handleBeforeProposalChange = async (_newProposalId: string): Promise<boolean> => {
        if (!hasUnsavedChanges) {
            return true
        }
        return window.confirm(t('selection.confirmUnsavedSwitch'))
    }

    const handleProposalChange = (newProposalId: string) => {
        setSelectedProposalId(newProposalId)
        navigate(`/proposal-editor/${newProposalId}`, {replace: true})
    }

    const handleProposalCopy = async (proposalId: string) => {
        const newProposal = await copyProposal(proposalId)

        const newItem: ProposalItem = {
            id: String(newProposal.id),
            proposalId: String(newProposal.id),
            title: newProposal.title,
            submission_type: newProposal.submission_type,
            workflow_status: 'draft',
        }

        setProposals((prev) => [newItem, ...prev])
        setSelectedProposalId(newItem.id)
        navigate(`/proposal-editor/${newItem.id}`, {replace: true})
    }

    const handleProposalDelete = async (proposalId: string) => {
        await deleteProposal(proposalId)

        const remainingProposals = proposals.filter((proposal) => proposal.proposalId !== proposalId)

        if (remainingProposals.length === 0) {
            const emptyItem: ProposalItem = {
                id: 'empty',
                proposalId: '',
                title: t('selection.noProposalsYet'),
                submission_type: t('selection.clickCreateNewProposal'),
            }
            setProposals([emptyItem])
            setSelectedProposalId('empty')
            navigate('/proposal-editor', {replace: true})
            return
        }

        const nextProposalId = remainingProposals[0].id
        setProposals(remainingProposals)
        setSelectedProposalId(nextProposalId)
        navigate(`/proposal-editor/${nextProposalId}`, {replace: true})
    }

    if (loading) {
        return <div style={{padding: '2rem', textAlign: 'center'}}>{t('selection.loadingProposals')}</div>
    }

    if (!canBrowse('proposal')) {
        return (
            <div style={{padding: '2rem', textAlign: 'center', color: '#666'}}>
                <p style={{fontSize: '1.2rem', marginBottom: '1rem'}}>{t('indexView.accessDenied')}</p>
                <p>{t('selection.noPermissionBrowse')}</p>
            </div>
        )
    }

    const selectedProposalIdFromState = selectedProposalId

    const createButton = canAdd('proposal') ? (
        <div style={{marginBottom: '1rem'}}>
            <button
                type="button"
                onClick={handleCreateProposal}
                disabled={isCreating}
                style={{
                    width: '100%',
                    padding: '0.75rem',
                    marginBottom: createError ? '0.5rem' : 0,
                    backgroundColor: '#4caf50',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: isCreating ? 'not-allowed' : 'pointer',
                    fontWeight: 600,
                    opacity: isCreating ? 0.7 : 1,
                }}
            >
                {isCreating ? t('selection.creating') : t('selection.createNewProposal')}
            </button>
            {createError && (
                <div className={styles.inlineError}>
                    {createError}
                </div>
            )}
        </div>
    ) : undefined

    const renderProposalLabel = (proposal: ProposalItem) => (
        <div className={styles.proposalLabel}>
            <div className={styles.proposalLabelHeader}>
                <span className={styles.proposalTitle}>{proposal.title}</span>
                {proposal.proposalId !== '' && (
                    <span className={styles.workflowBadge} aria-hidden="true">
            {formatWorkflowStatus(proposal.workflow_status)}
          </span>
                )}
            </div>
            <div className={styles.proposalSubmissionType}>{proposal.submission_type}</div>
        </div>
    )

    return (
        <SelectionPanel<ProposalItem>
            items={proposals}
            selectedItemId={selectedProposalIdFromState}
            onSelectionChange={handleProposalChange}
            onBeforeSelectionChange={handleBeforeProposalChange}
            renderItemLabel={renderProposalLabel}
            renderContent={(proposal) => (
                proposal.proposalId === '' ? (
                    <div style={{padding: '2rem', textAlign: 'center', color: '#666'}}>
                        <p style={{fontSize: '1.2rem', marginBottom: '1rem'}}>{t('selection.noProposalsYet')}</p>
                        <p>{t('selection.clickCreateNewProposal')}</p>
                    </div>
                ) : editorPermissionLoading ? (
                    <div style={{padding: '2rem', textAlign: 'center', color: '#666'}}>
                        {t('selection.checkingPermissions')}
                    </div>
                ) : !canViewSelectedProposal ? (
                    <div style={{padding: '2rem', textAlign: 'center', color: '#666'}}>
                        <p style={{fontSize: '1.2rem', marginBottom: '1rem'}}>{t('indexView.accessDenied')}</p>
                        <p>{t('selection.noPermissionViewProposal')}</p>
                    </div>
                ) : (
                    <ProposalEditor
                        key={proposal.proposalId}
                        proposalId={proposal.proposalId}
                        canEdit={canEditSelectedProposal ?? false}
                        canDelete={canDeleteSelectedProposal}
                        onDeleteProposal={handleProposalDelete}
                        canCopy={canAdd('proposal')}
                        onCopyProposal={handleProposalCopy}
                        onUnsavedChangesChange={setHasUnsavedChanges}
                        onTransitionSuccess={() => {
                            void checkSelectedProposalPermissions()
                            void fetchProposalTransitions(proposal.proposalId).then((data) => {
                                setProposals((prev) => prev.map((p) =>
                                    p.proposalId === proposal.proposalId
                                        ? {...p, workflow_status: data.current_status}
                                        : p
                                ))
                            }).catch(console.error)
                        }}
                        onProposalSave={async (formData, freshProposal) => {
                            // Update the proposal in the sidebar with fresh data from server
                            try {
                                const transitions = await fetchProposalTransitions(freshProposal.id)
                                setProposals((prev) => prev.map((item) => {
                                    if (item.proposalId === freshProposal.id) {
                                        return {
                                            ...item,
                                            title: freshProposal.title,
                                            submission_type: freshProposal.submission_type,
                                            workflow_status: transitions.current_status,
                                        }
                                    }
                                    return item
                                }))
                            } catch (err) {
                                console.error('Failed to update proposal after save:', err)
                            }
                            // Forward event to parent if provided
                            if (onProposalSave) {
                                try {
                                    onProposalSave(formData)
                                } catch (err) {
                                    console.error('Parent onProposalSave errored:', err)
                                }
                            }
                        }}
                    />
                )
            )}
            sidebarTitle="Proposals"
            belowTitleElement={createButton}
        />
    )
}