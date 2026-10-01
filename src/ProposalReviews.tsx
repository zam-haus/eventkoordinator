import { useState, useEffect, useMemo, useRef, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getCurrentUser,
  searchUsers,
  fetchPermissionGroups,
  fetchProposalReviews,
  createProposalReview,
  updateProposalReview,
  deleteProposalReview,
  type UserBasic,
  type LookupItem,
  type ProposalReviewOut,
} from './api'
import { confirmMailAction } from './mailConfirm'
import styles from './ProposalReviews.module.css'

// ── Types ─────────────────────────────────────────────────────────────────────

type ReviewStatus = 'approved' | 'rejected' | 'revise' | 'pending' | 'note'
type PickResult =
  | { kind: 'user'; user: UserBasic }
  | { kind: 'group'; group: LookupItem }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type TFn = (key: any, opts?: any) => string

// ── Helpers ───────────────────────────────────────────────────────────────────

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join('')
    .toUpperCase()
}

function fmtRel(iso: string | null | undefined, t: TFn): string {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return t('reviews.relative.justNow')
  if (diff < 3600) return t('reviews.relative.minutesAgo', { count: Math.floor(diff / 60) })
  if (diff < 86400) return t('reviews.relative.hoursAgo', { count: Math.floor(diff / 3600) })
  return t('reviews.relative.daysAgo', { count: Math.floor(diff / 86400) })
}

function deriveGroupStatus(
  groupCode: string,
  reviews: ProposalReviewOut[],
): ReviewStatus {
  const memberReviews = reviews.filter(
    (r) => r.kind === 'user' && (r.requested_via_groups || []).includes(groupCode),
  )
  if (memberReviews.some((r) => r.status === 'rejected')) return 'rejected'
  if (memberReviews.some((r) => r.status === 'revise')) return 'revise'
  if (memberReviews.some((r) => r.status === 'approved')) return 'approved'
  return 'pending'
}

function worstMemberVote(
  groupCode: string,
  reviews: ProposalReviewOut[],
): ReviewStatus | null {
  const RANK: Record<string, number> = { rejected: 3, revise: 2, approved: 1 }
  const decided = reviews.filter(
    (r) =>
      r.kind === 'user' &&
      (r.requested_via_groups || []).includes(groupCode) &&
      r.status !== 'pending',
  )
  if (decided.length === 0) return null
  return decided.reduce<ReviewStatus | null>(
    (w, r) =>
      !w
        ? (r.status as ReviewStatus)
        : (RANK[r.status] || 0) > (RANK[w] || 0)
        ? (r.status as ReviewStatus)
        : w,
    null,
  )
}

function statusCardClass(status: ReviewStatus): string {
  return (
    {
      approved: styles.reviewCardApproved,
      rejected: styles.reviewCardRejected,
      revise: styles.reviewCardRevise,
      pending: styles.reviewCardPending,
      note: styles.reviewCardNote,
    }[status] ?? ''
  )
}

function statusBadgeClass(status: ReviewStatus): string {
  return (
    {
      approved: styles.badgeApproved,
      rejected: styles.badgeRejected,
      revise: styles.badgeRevise,
      pending: styles.badgePending,
      note: styles.badgeNote,
    }[status] ?? ''
  )
}

// ── StatusBadge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ReviewStatus }) {
  const { t } = useTranslation()
  return (
    <span className={`${styles.statusBadge} ${statusBadgeClass(status)}`}>
      <span className={styles.badgeDot} />
      {t(`reviews.status.${status}`)}
    </span>
  )
}

// ── UserPicker ────────────────────────────────────────────────────────────────

interface UserPickerProps {
  excludeIds: string[]
  groups: LookupItem[]
  onPick: (result: PickResult) => void
}

function UserPicker({ excludeIds, groups, onPick }: UserPickerProps) {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [userResults, setUserResults] = useState<UserBasic[]>([])
  const [activeIndex, setActiveIndex] = useState(-1)
  const ref = useRef<HTMLDivElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
        setActiveIndex(-1)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  useEffect(() => {
    let cancelled = false
    searchUsers(q).then((results) => {
      if (!cancelled) setUserResults(results)
    })
    return () => {
      cancelled = true
    }
  }, [q])

  useEffect(() => {
    setActiveIndex(-1)
  }, [q])

  const filteredGroups = useMemo(
    () =>
      groups.filter(
        (g) => !q.trim() || g.label.toLowerCase().includes(q.toLowerCase()),
      ),
    [q, groups],
  )

  // Flat list of selectable (non-disabled) items for keyboard nav
  const selectableItems = useMemo(() => {
    const items: Array<{ kind: 'group'; group: LookupItem } | { kind: 'user'; user: UserBasic }> = []
    for (const g of filteredGroups) {
      if (!excludeIds.includes(g.code)) items.push({ kind: 'group', group: g })
    }
    for (const u of userResults) {
      if (!excludeIds.includes(u.id)) items.push({ kind: 'user', user: u })
    }
    return items
  }, [filteredGroups, userResults, excludeIds])

  const pick = (item: (typeof selectableItems)[number]) => {
    onPick(item.kind === 'group' ? { kind: 'group', group: item.group } : { kind: 'user', user: item.user })
    setQ('')
    setOpen(false)
    setActiveIndex(-1)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        setOpen(true)
        setActiveIndex(0)
        e.preventDefault()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(i + 1, selectableItems.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (activeIndex >= 0 && activeIndex < selectableItems.length) {
        pick(selectableItems[activeIndex])
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
      setActiveIndex(-1)
    }
  }

  // Scroll active item into view
  useEffect(() => {
    if (activeIndex < 0 || !menuRef.current) return
    const el = menuRef.current.querySelector(`[data-picker-index="${activeIndex}"]`) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  const getSelectableIndex = (kind: 'group' | 'user', id: string) =>
    selectableItems.findIndex(
      (item) => item.kind === kind && (item.kind === 'group' ? item.group.code : item.user.id) === id,
    )

  const listboxId = 'user-picker-listbox'

  return (
    <div className={styles.userPickerWrap} ref={ref}>
      <input
        className={styles.userPickerInput}
        placeholder={t('reviews.picker.placeholder')}
        value={q}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-activedescendant={activeIndex >= 0 ? `picker-item-${activeIndex}` : undefined}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onKeyDown={handleKeyDown}
      />
      {open && (filteredGroups.length > 0 || userResults.length > 0) && (
        <div className={styles.userPickerMenu} role="listbox" id={listboxId} ref={menuRef}>
          {filteredGroups.length > 0 && (
            <div className={styles.pickerSectionLabel}>{t('reviews.picker.groupsSection')}</div>
          )}
          {filteredGroups.map((g) => {
            const disabled = excludeIds.includes(g.code)
            const sIdx = disabled ? -1 : getSelectableIndex('group', g.code)
            const isActive = sIdx >= 0 && sIdx === activeIndex
            return (
              <div
                key={g.code}
                id={sIdx >= 0 ? `picker-item-${sIdx}` : undefined}
                data-picker-index={sIdx >= 0 ? sIdx : undefined}
                role="option"
                aria-selected={isActive}
                aria-disabled={disabled}
                className={`${styles.userPickerItem} ${styles.userPickerItemGroup} ${
                  disabled ? styles.userPickerItemDisabled : ''
                } ${isActive ? styles.userPickerItemActive : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  if (disabled) return
                  pick({ kind: 'group', group: g })
                }}
              >
                <span className={`${styles.avatar} ${styles.avatarGroup}`}>
                  {initials(g.label)}
                </span>
                <span>{g.label}</span>
                <span className={styles.userPickerItemRole}>
                  {disabled ? t('reviews.picker.alreadyAdded') : t('reviews.picker.area')}
                </span>
              </div>
            )
          })}

          {userResults.length > 0 && (
            <div className={styles.pickerSectionLabel}>{t('reviews.picker.peopleSection')}</div>
          )}
          {userResults.map((u) => {
            const disabled = excludeIds.includes(u.id)
            const sIdx = disabled ? -1 : getSelectableIndex('user', u.id)
            const isActive = sIdx >= 0 && sIdx === activeIndex
            return (
              <div
                key={u.id}
                id={sIdx >= 0 ? `picker-item-${sIdx}` : undefined}
                data-picker-index={sIdx >= 0 ? sIdx : undefined}
                role="option"
                aria-selected={isActive}
                aria-disabled={disabled}
                className={`${styles.userPickerItem} ${
                  disabled ? styles.userPickerItemDisabled : ''
                } ${isActive ? styles.userPickerItemActive : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  if (disabled) return
                  pick({ kind: 'user', user: u })
                }}
              >
                <span className={styles.avatar}>{initials(u.username)}</span>
                <span>{u.username}</span>
                {disabled && (
                  <span className={styles.userPickerItemRole}>{t('reviews.picker.alreadyAdded')}</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── ReviewCard ────────────────────────────────────────────────────────────────

interface ReviewCardProps {
  review: ProposalReviewOut
  currentUserId: string
  groups: LookupItem[]
  canModerate: boolean
  canDeleteReview: boolean
  onRemove: () => void
  onWithdraw: () => void
  onEditOwn: () => void
}

function ReviewCard({ review, currentUserId, groups, canModerate, canDeleteReview, onRemove, onWithdraw, onEditOwn }: ReviewCardProps) {
  const { t } = useTranslation()
  const isSelf = review.reviewer_id === currentUserId
  const isSystem = review.reviewer_is_system

  const cardClasses = [
    styles.reviewCard,
    statusCardClass(review.status as ReviewStatus),
    isSelf ? styles.reviewCardSelf : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={cardClasses}>
      <div className={styles.reviewHead}>
        <span className={styles.reviewer}>
          <span className={`${styles.avatar} ${isSystem ? styles.avatarSystem : ''}`}>
            {isSystem ? '⚙' : initials(review.reviewer_username || '?')}
          </span>
          <span>{isSystem ? t('reviews.card.system') : review.reviewer_username}</span>
          {isSelf && <span className={styles.selfTag}>{t('reviews.card.you')}</span>}
          {isSystem && (
            <span
              className={styles.tagSoft}
              title={t('reviews.card.migratedTitle')}
            >
              {t('reviews.card.migratedTag')}
            </span>
          )}
        </span>
        <StatusBadge status={review.status as ReviewStatus} />
        <span className={styles.reviewMeta}>
          {review.status === 'pending' ? (
            <span>{t('reviews.card.requested', { when: fmtRel(review.requested_at, t) })}</span>
          ) : (
            <span>{fmtRel(review.completed_at, t)}</span>
          )}
          {isSelf && review.status !== 'pending' && !isSystem && (
            <button type="button" className={styles.btnSubtle} onClick={onEditOwn}>
              {t('reviews.card.edit')}
            </button>
          )}
          {!isSystem && (() => {
            const isCompleted = review.status !== 'pending'
            const wasRequested =
              review.requested_directly || (review.requested_via_groups || []).length > 0
            // Pending reviews: show X for own self-submitted or for moderators
            const showForPending = !isCompleted && (canModerate || (isSelf && !wasRequested))
            // Completed reviews: show X for own or moderator
            const showForCompleted = isCompleted && (isSelf || canModerate)
            if (!showForPending && !showForCompleted) return null
            // Withdraw (reset to pending) only for own completed requested reviews without delete perm
            const withdrawOnly = isSelf && isCompleted && wasRequested && !canDeleteReview && !canModerate
            const actionLabel = withdrawOnly ? t('reviews.card.withdrawReview') : t('reviews.card.removeReview')
            return (
              <button
                type="button"
                className={styles.iconBtn}
                title={actionLabel}
                onClick={withdrawOnly ? onWithdraw : onRemove}
                aria-label={actionLabel}
              >
                ×
              </button>
            )
          })()}
        </span>
      </div>

      {review.status !== 'pending' ? (
        <div className={styles.reviewBody}>
          {review.comment || (
            <em style={{ color: '#9ca3af' }}>{t('reviews.card.noComment')}</em>
          )}
        </div>
      ) : (
        <div className={`${styles.reviewBody} ${styles.reviewBodyMuted}`}>
          {t('reviews.card.waitingFor', { name: (review.reviewer_username || '').split(' ')[0] })}
          {review.previous_status && (
            <div
              style={{
                marginTop: '0.5rem',
                fontStyle: 'normal',
                color: '#6b7280',
                fontSize: '0.85rem',
              }}
            >
              {t('reviews.card.previouslyVoted')}{' '}
              <StatusBadge status={review.previous_status as ReviewStatus} />
              {review.previous_comment && (
                <div className={styles.prevVoteBlock}>"{review.previous_comment}"</div>
              )}
            </div>
          )}
        </div>
      )}

      <div className={styles.reviewFootline}>
        {review.requested_directly && review.requested_by_username && (
          <span className={styles.requestedBy}>
            {t('reviews.card.requestedDirectlyBy')} <strong>{review.requested_by_username}</strong>
          </span>
        )}
        {(review.requested_via_groups || []).map((code) => {
          const label = groups.find((g) => g.code === code)?.label ?? code
          return (
            <span key={code} className={`${styles.requestedBy} ${styles.viaGroup}`}>
              {t('reviews.card.via')} <strong>{label}</strong>
            </span>
          )
        })}
        {isSystem && (
          <span className={styles.requestedBy}>
            {t('reviews.card.migratedFrom')} <strong>{t('reviews.card.moderationComment')}</strong>
          </span>
        )}
      </div>
    </div>
  )
}

// ── GroupReviewCard ───────────────────────────────────────────────────────────

interface GroupReviewCardProps {
  groupRequest: ProposalReviewOut
  allReviews: ProposalReviewOut[]
  currentUserId: string
  onWithdraw: () => void
}

function GroupReviewCard({
  groupRequest,
  allReviews,
  currentUserId,
  onWithdraw,
}: GroupReviewCardProps) {
  const { t } = useTranslation()
  const derived = deriveGroupStatus(groupRequest.group_code, allReviews)
  const worst = worstMemberVote(groupRequest.group_code, allReviews)
  const memberVotes = allReviews.filter(
    (r) =>
      r.kind === 'user' &&
      (r.requested_via_groups || []).includes(groupRequest.group_code),
  )

  const groupCardClass = [
    styles.reviewCard,
    styles.reviewCardGroup,
    derived === 'approved' ? styles.reviewCardGroupApproved : '',
    derived === 'rejected' ? styles.reviewCardGroupRejected : '',
    derived === 'revise' ? styles.reviewCardGroupRevise : '',
    derived === 'pending' ? styles.reviewCardGroupPending : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={groupCardClass}>
      <div className={styles.reviewHead}>
        <span className={styles.reviewer}>
          <span className={`${styles.avatar} ${styles.avatarGroup}`}>
            {initials(groupRequest.group_label || groupRequest.group_code)}
          </span>
          <span>{groupRequest.group_label || groupRequest.group_code}</span>
          <span className={styles.tagSoft}>{t('reviews.group.permissionGroup')}</span>
        </span>
        <StatusBadge status={derived} />
        <span className={styles.reviewMeta}>
          <span>{t('reviews.card.requested', { when: fmtRel(groupRequest.requested_at, t) })}</span>
          {derived !== 'approved' && (
            <button
              type="button"
              className={styles.iconBtn}
              title={t('reviews.group.withdrawGroupRequest')}
              onClick={onWithdraw}
              aria-label={t('reviews.group.withdrawGroupRequest')}
            >
              ×
            </button>
          )}
        </span>
      </div>

      <div className={styles.groupSummary}>
        <span>
          <strong>{memberVotes.filter((r) => r.status !== 'pending').length}/
          {groupRequest.group_member_count ?? memberVotes.length}</strong>{' '}
          {t('reviews.group.membersVotedSuffix')}
        </span>
        {derived === 'pending' && worst && (
          <span className={styles.groupTrending}>
            {t('reviews.group.trending')} <StatusBadge status={worst} />
          </span>
        )}
        {derived === 'approved' && (
          <span style={{ color: '#2e7d32', fontWeight: 600 }}>
            {t('reviews.group.oneApprovalEnough')}
          </span>
        )}
      </div>

      {memberVotes.length > 0 && (
        <div className={styles.memberChips}>
          {memberVotes.map((r) => {
            const isMe = r.reviewer_id === currentUserId
            const chipClass = [
              styles.memberChip,
              isMe ? styles.memberChipMe : '',
              r.status === 'approved' ? styles.memberChipApproved : '',
              r.status === 'rejected' ? styles.memberChipRejected : '',
              r.status === 'revise' ? styles.memberChipRevise : '',
            ]
              .filter(Boolean)
              .join(' ')
            return (
              <span
                key={r.reviewer_id}
                className={chipClass}
                title={
                  r.comment
                    ? `${r.reviewer_username}: ${r.comment}`
                    : `${r.reviewer_username} — ${r.status}`
                }
              >
                <span className={`${styles.avatar} ${styles.avatarSmall}`}>
                  {initials(r.reviewer_username || '?')}
                </span>
                <span>{(r.reviewer_username || '').split(' ')[0]}</span>
                {isMe && <span className={styles.selfTag}>{t('reviews.card.you')}</span>}
                <StatusBadge status={r.status as ReviewStatus} />
              </span>
            )
          })}
        </div>
      )}

      <div className={styles.reviewFootline}>
        {groupRequest.requested_by_username && (
          <span className={styles.requestedBy}>
            {t('reviews.group.requestedBy')} <strong>{groupRequest.requested_by_username}</strong>
          </span>
        )}
        <span className={styles.fieldHint}>
          {t('reviews.group.groupNote')}
        </span>
      </div>
    </div>
  )
}

// ── SelfReviewComposer ────────────────────────────────────────────────────────

interface SelfReviewComposerProps {
  currentUser: UserBasic
  existing: ProposalReviewOut | undefined
  onSubmit: (vote: { status: ReviewStatus; comment: string }) => void
  onCancel?: () => void
  onDismiss?: () => void
  dismissTitle?: string
}

function SelfReviewComposer({
  currentUser,
  existing,
  onSubmit,
  onCancel,
  onDismiss,
  dismissTitle,
}: SelfReviewComposerProps) {
  const { t } = useTranslation()
  const isRequested = existing && existing.status === 'pending'
  const isEditing = existing && existing.status !== 'pending'
  const seedComment = existing?.comment || existing?.previous_comment || ''
  const [comment, setComment] = useState(seedComment)
  const [open, setOpen] = useState(Boolean(existing))

  useEffect(() => {
    setComment(existing?.comment || existing?.previous_comment || '')
    setOpen(Boolean(existing))
  }, [existing?.id])

  if (!existing && !open) {
    return (
      <div className={styles.selfReviewForm}>
        <div className={styles.selfReviewHeader}>
          <span className={styles.reviewer}>
            <span className={styles.avatar}>{initials(currentUser.username)}</span>
            <span>{currentUser.username}</span>
            <span className={styles.selfTag}>{t('reviews.card.you')}</span>
          </span>
          <span style={{ marginLeft: 'auto', color: '#6b7280', fontSize: '0.85rem' }}>
            {t('reviews.composer.notAsked')}
          </span>
        </div>
        <div className={styles.selfReviewRow}>
          <button
            type="button"
            className={`${styles.btn} ${styles.btnPrimary}`}
            onClick={() => setOpen(true)}
          >
            {t('reviews.composer.addMyReview')}
          </button>
        </div>
      </div>
    )
  }

  const submit = (status: ReviewStatus) => {
    onSubmit({ status, comment: comment.trim() })
    setComment('')
    setOpen(false)
  }

  const headerLabel = isRequested
    ? t('reviews.composer.askedToReview')
    : isEditing
    ? t('reviews.composer.editingReview')
    : t('reviews.composer.newReview')

  const formClasses = [
    styles.selfReviewForm,
    styles.selfReviewFormEditing,
    isRequested ? styles.selfReviewFormRequested : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={formClasses}>
      <div className={styles.selfReviewHeader}>
        <span className={styles.reviewer}>
          <span className={styles.avatar}>{initials(currentUser.username)}</span>
          <span>{currentUser.username}</span>
          <span className={styles.selfTag}>{t('reviews.card.you')}</span>
        </span>
        <span className={styles.tagSoft}>{headerLabel}</span>
        {isRequested && existing.requested_by_username && (
          <span style={{ marginLeft: 'auto', color: '#6b7280', fontSize: '0.82rem' }}>
            {t('reviews.composer.requestedBy')} <strong>{existing.requested_by_username}</strong>{' '}
            {fmtRel(existing.requested_at, t)}
          </span>
        )}
        {onDismiss && (
          <button
            type="button"
            className={styles.iconBtn}
            title={dismissTitle ?? t('reviews.composer.removeReview')}
            onClick={onDismiss}
            aria-label={dismissTitle ?? t('reviews.composer.removeReview')}
            style={{ marginLeft: isRequested ? undefined : 'auto' }}
          >
            ×
          </button>
        )}
      </div>

      {isRequested && existing.previous_status && (
        <div style={{ fontSize: '0.85rem', color: '#6b7280' }}>
          {t('reviews.composer.resubmitted')}{' '}
          <StatusBadge status={existing.previous_status as ReviewStatus} />.
          {existing.previous_comment && (
            <div className={styles.prevVoteBlock}>"{existing.previous_comment}"</div>
          )}
        </div>
      )}

      <textarea
        className={styles.textarea}
        placeholder={
          isRequested
            ? t('reviews.composer.commentPlaceholderRequested')
            : t('reviews.composer.commentPlaceholderNew')
        }
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        maxLength={2000}
        rows={4}
      />
      <div className={styles.selfReviewRow} style={{ justifyContent: 'space-between' }}>
        <div className={styles.selfReviewRow}>
          <button
            type="button"
            className={`${styles.btn} ${styles.btnApprove}`}
            onClick={() => submit('approved')}
            disabled={!comment.trim()}
          >
            {t('reviews.composer.approve')}
          </button>
          <button
            type="button"
            className={`${styles.btn} ${styles.btnRevise}`}
            onClick={() => submit('revise')}
            disabled={!comment.trim()}
          >
            {t('reviews.composer.requestRevisions')}
          </button>
          <button
            type="button"
            className={`${styles.btn} ${styles.btnReject}`}
            onClick={() => submit('rejected')}
            disabled={!comment.trim()}
          >
            {t('reviews.composer.reject')}
          </button>
        </div>
        <button
          type="button"
          className={`${styles.btn} ${styles.btnGhost}`}
          onClick={() => {
            setOpen(false)
            setComment(existing?.comment || '')
            onCancel?.()
          }}
          style={{ visibility: isRequested ? 'hidden' : 'visible' }}
        >
          {t('reviews.composer.cancel')}
        </button>
      </div>
      <span className={styles.fieldHint}>
        {t('reviews.composer.commentRequired')}
      </span>
    </div>
  )
}

// ── ReviewsSection ────────────────────────────────────────────────────────────

interface ReviewsSectionProps {
  proposalId: string
  proposalStatus: string
  moderationComment?: string
  moderationCommentAt?: string
  canModerate: boolean
  canDeleteReview: boolean
  canCreateReview: boolean
}

export function ReviewsSection({
  proposalId,
  proposalStatus,
  moderationComment,
  canModerate,
  canDeleteReview,
  canCreateReview,
}: ReviewsSectionProps) {
  const { t } = useTranslation()
  const [currentUser, setCurrentUser] = useState<UserBasic | null>(null)
  const [groups, setGroups] = useState<LookupItem[]>([])
  const [reviews, setReviews] = useState<ProposalReviewOut[]>([])
  const [pendingViaGroups, setPendingViaGroups] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [editingOwn, setEditingOwn] = useState(false)
  const [pendingRequestee, setPendingRequestee] = useState<PickResult | null>(null)
  const [saving, setSaving] = useState(false)
  const prevStatusRef = useRef(proposalStatus)
  const moderationCommentSeededRef = useRef(false)

  // Load current user and groups once
  useEffect(() => {
    getCurrentUser().then((u) => setCurrentUser({ id: u.user_id, username: u.username }))
    fetchPermissionGroups().then(setGroups)
  }, [])

  // Load reviews from API
  useEffect(() => {
    if (!proposalId) return
    setLoading(true)
    fetchProposalReviews(proposalId)
      .then((data) => {
        setReviews(data.reviews)
        setPendingViaGroups(data.pending_via_groups)
      })
      .catch((err) => console.error('Failed to load reviews:', err))
      .finally(() => setLoading(false))
  }, [proposalId])

  // Migrate legacy moderation_comment as System review (once per proposal, if not already present)
  useEffect(() => {
    if (
      !moderationComment ||
      !proposalId ||
      moderationCommentSeededRef.current ||
      loading
    )
      return
    const alreadyMigrated = reviews.some((r) => r.reviewer_is_system)
    if (alreadyMigrated) {
      moderationCommentSeededRef.current = true
      return
    }
    moderationCommentSeededRef.current = true
    createProposalReview(proposalId, {
      kind: 'user',
      reviewer_is_system: true,
      comment: moderationComment,
      status: 'note',
      migrated: true,
    })
      .then((r) => setReviews((prev) => [r, ...prev]))
      .catch((err) => console.error('Failed to migrate moderation comment:', err))
  }, [moderationComment, proposalId, loading, reviews])

  // Reload reviews when the proposal is resubmitted (backend handles the reset atomically)
  useEffect(() => {
    const prev = prevStatusRef.current
    prevStatusRef.current = proposalStatus
    if (
      proposalStatus === 'submitted' &&
      (prev === 'revise' || prev === 'rejected') &&
      proposalId
    ) {
      fetchProposalReviews(proposalId)
        .then((data) => {
          setReviews(data.reviews)
          setPendingViaGroups(data.pending_via_groups)
        })
        .catch((err) => console.error('Failed to reload reviews after resubmission:', err))
    }
  }, [proposalStatus, proposalId])

  const currentUserId = currentUser?.id ?? ''

  // Ids already in the list (for picker exclusion)
  const excludeIds = reviews.map((r) =>
    r.kind === 'group' ? r.group_code : (r.reviewer_id ?? ''),
  )

  // Stats: count each direct ask once; group requests count once via derived status
  const stats = useMemo(() => {
    let approved = 0, revise = 0, rejected = 0, pending = 0
    for (const r of reviews) {
      if (r.kind === 'user' && r.status === 'note') continue
      if (
        r.kind === 'user' &&
        !r.requested_directly &&
        (r.requested_via_groups || []).length > 0
      )
        continue
      const s = r.kind === 'group' ? deriveGroupStatus(r.group_code, reviews) : r.status
      if (s === 'approved') approved++
      else if (s === 'revise') revise++
      else if (s === 'rejected') rejected++
      else pending++
    }
    return { approved, revise, rejected, pending, total: approved + revise + rejected + pending }
  }, [reviews])

  const requestReview = useCallback(async () => {
    if (!pendingRequestee || !proposalId) return
    setSaving(true)
    try {
      if (pendingRequestee.kind === 'group') {
        if (!confirmMailAction(t, 'review.request_group', { name: pendingRequestee.group.label })) {
          return
        }
        const r = await createProposalReview(proposalId, {
          kind: 'group',
          group_code: pendingRequestee.group.code,
        })
        setReviews((prev) => [...prev, r])
      } else {
        if (!confirmMailAction(t, 'review.request_user', { name: pendingRequestee.user.username })) {
          return
        }
        const r = await createProposalReview(proposalId, {
          kind: 'user',
          reviewer_id: pendingRequestee.user.id,
          requested_directly: true,
        })
        setReviews((prev) => [...prev, r])
      }
      setPendingRequestee(null)
    } catch (err) {
      console.error('Failed to request review:', err)
    } finally {
      setSaving(false)
    }
  }, [pendingRequestee, proposalId, t])

  const removeReview = useCallback(
    async (reviewId: string) => {
      if (!proposalId) return
      try {
        await deleteProposalReview(proposalId, reviewId)
        setReviews((prev) => prev.filter((r) => r.id !== reviewId))
      } catch (err) {
        console.error('Failed to remove review:', err)
      }
    },
    [proposalId],
  )

  const resetReviewToPending = useCallback(
    async (review: ProposalReviewOut) => {
      if (!proposalId) return
      try {
        const updated = await updateProposalReview(proposalId, review.id, 'pending', review.comment)
        setReviews((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
      } catch (err) {
        console.error('Failed to withdraw review:', err)
      }
    },
    [proposalId],
  )

  const submitOwnReview = useCallback(
    async ({ status, comment }: { status: ReviewStatus; comment: string }) => {
      if (!proposalId || !currentUser) return
      setSaving(true)
      try {
        const existing = reviews.find(
          (r) => r.kind === 'user' && r.reviewer_id === currentUser.id,
        )
        let updated: ProposalReviewOut
        if (existing) {
          updated = await updateProposalReview(proposalId, existing.id, status, comment)
          setReviews((prev) => prev.map((r) => (r.id === updated.id ? updated : r)))
        } else {
          updated = await createProposalReview(proposalId, {
            kind: 'user',
            status,
            comment,
            requested_directly: false,
            requested_via_groups: pendingViaGroups.length > 0 ? pendingViaGroups : undefined,
          })
          setReviews((prev) => [...prev, updated])
          setPendingViaGroups([])
        }
        setEditingOwn(false)
      } catch (err) {
        console.error('Failed to submit review:', err)
      } finally {
        setSaving(false)
      }
    },
    [proposalId, currentUser, reviews, pendingViaGroups],
  )

  const ownReview = reviews.find(
    (r): r is ProposalReviewOut =>
      r.kind === 'user' && r.reviewer_id === currentUserId,
  )

  const isLocked = proposalStatus === 'accepted' || proposalStatus === 'archived'

  const sortedReviews = useMemo(() => {
    const notes = reviews.filter((r) => r.kind === 'user' && r.status === 'note')
    const rest = reviews.filter((r) => !(r.kind === 'user' && r.status === 'note'))
    return [...notes, ...rest]
  }, [reviews])

  if (!currentUser || loading) return null

  return (
    <fieldset className={styles.reviewsFieldset}>
      <legend className={styles.reviewsLegend}>{t('reviews.legend')}</legend>

      <div className={styles.reviewsHeader}>
        <div className={styles.reviewsCount}>
          {stats.total === 0 ? (
            <span>{t('reviews.noReviewsYet')}</span>
          ) : (
            <>
              {stats.approved > 0 && (
                <span style={{ color: '#2e7d32' }}>{t('reviews.stats.approved', { count: stats.approved })}</span>
              )}
              {stats.revise > 0 && (
                <span style={{ color: '#8b6508' }}>{t('reviews.stats.revise', { count: stats.revise })}</span>
              )}
              {stats.rejected > 0 && (
                <span style={{ color: '#b71c1c' }}>{t('reviews.stats.rejected', { count: stats.rejected })}</span>
              )}
              {stats.pending > 0 && (
                <span style={{ color: '#b25e09' }}>{t('reviews.stats.pending', { count: stats.pending })}</span>
              )}
            </>
          )}
        </div>
      </div>

      {stats.total > 0 && (
        <div className={styles.reviewsProgress}>
          <div className={styles.progressBar} aria-label="Review progress">
            {stats.approved > 0 && (
              <div
                className={`${styles.progressSeg} ${styles.progressSegApproved}`}
                style={{ width: `${(stats.approved / stats.total) * 100}%` }}
              />
            )}
            {stats.revise > 0 && (
              <div
                className={`${styles.progressSeg} ${styles.progressSegRevise}`}
                style={{ width: `${(stats.revise / stats.total) * 100}%` }}
              />
            )}
            {stats.rejected > 0 && (
              <div
                className={`${styles.progressSeg} ${styles.progressSegRejected}`}
                style={{ width: `${(stats.rejected / stats.total) * 100}%` }}
              />
            )}
          </div>
        </div>
      )}

      <div className={styles.reviewList}>
        {reviews.length === 0 && (
          <div
            className={`${styles.reviewBody} ${styles.reviewBodyMuted}`}
            style={{ padding: '0.5rem 0' }}
          >
            {t('reviews.noReviewsMessage')}
          </div>
        )}

        {sortedReviews.map((r) => {
          if (editingOwn && r.kind === 'user' && r.reviewer_id === currentUserId)
            return null
          if (r.kind === 'group') {
            return (
              <GroupReviewCard
                key={r.id}
                groupRequest={r}
                allReviews={reviews}
                currentUserId={currentUserId}
                onWithdraw={() => void removeReview(r.id)}
              />
            )
          }
          return (
            <ReviewCard
              key={r.id}
              review={r}
              currentUserId={currentUserId}
              groups={groups}
              canModerate={canModerate}
              canDeleteReview={canDeleteReview}
              onRemove={() => void removeReview(r.id)}
              onWithdraw={() => void resetReviewToPending(r)}
              onEditOwn={() => setEditingOwn(true)}
            />
          )
        })}
      </div>

      {!isLocked && (ownReview?.status === 'pending' || editingOwn || (!ownReview && (canCreateReview || canModerate || pendingViaGroups.length > 0))) && (() => {
        const existing = editingOwn
          ? ownReview
          : ownReview?.status === 'pending'
          ? ownReview
          : undefined
        const wasRequested =
          existing && (existing.requested_directly || (existing.requested_via_groups || []).length > 0)
        const withdrawOnly = existing && wasRequested && !canDeleteReview && !canModerate
        const dismissAction = existing
          ? withdrawOnly
            ? () => void resetReviewToPending(existing)
            : () => void removeReview(existing.id)
          : undefined
        const dismissTitle = withdrawOnly ? t('reviews.card.withdrawReview') : t('reviews.card.removeReview')
        return (
          <SelfReviewComposer
            currentUser={currentUser}
            existing={existing}
            onSubmit={(v) => void submitOwnReview(v)}
            onCancel={() => setEditingOwn(false)}
            onDismiss={dismissAction}
            dismissTitle={dismissTitle}
          />
        )
      })()}

      {!isLocked && canModerate && (
        <div className={styles.requestRow}>
          <div className={styles.requestRowInner}>
            <UserPicker
              excludeIds={excludeIds}
              groups={groups}
              onPick={setPendingRequestee}
            />
            {pendingRequestee && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  flexWrap: 'wrap',
                }}
              >
                <span style={{ fontSize: '0.85rem', color: '#374151' }}>
                  {t('reviews.requestRow.requestFrom')}{' '}
                  <strong>
                    {pendingRequestee.kind === 'group'
                      ? pendingRequestee.group.label
                      : pendingRequestee.user.username}
                  </strong>
                </span>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  onClick={() => void requestReview()}
                  disabled={saving}
                >
                  {t('reviews.requestRow.requestButton')}
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnGhost}`}
                  onClick={() => setPendingRequestee(null)}
                >
                  {t('reviews.requestRow.cancel')}
                </button>
              </div>
            )}
            <span className={styles.requestHint}>
              {t('reviews.requestRow.hint')}
            </span>
          </div>
        </div>
      )}
    </fieldset>
  )
}
