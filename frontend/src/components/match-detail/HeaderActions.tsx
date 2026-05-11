import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'
import { HeaderApplyButton } from './HeaderApplyButton'

export interface HeaderActionsProps {
  appId: string
  status: string
  applyUrl: string
}

/**
 * Desktop-only counterpart to <StickyActions/> on mobile. Renders the same
 * status-dependent middle action (Dismiss / Unapply / Restore) inline next to
 * the Apply CTA in the page header. Mobile shows the bottom nav; desktop
 * shows these inline.
 */
export function HeaderActions({ appId, status, applyUrl }: HeaderActionsProps) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { show } = useToast()

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(appId, 'dismissed'),
    onSuccess: () => {
      show('Dismissed', 'info')
      qc.invalidateQueries({ queryKey: ['applications'] })
      qc.invalidateQueries({ queryKey: ['application', appId] })
      navigate(-1)
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  const moveBackToPending = useMutation({
    mutationFn: () => api.reviewApplication(appId, 'pending_review'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['application', appId] })
      qc.invalidateQueries({ queryKey: ['applications'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not update', 'error'),
  })

  let middle: React.ReactNode = null
  if (status === 'pending_review') {
    middle = (
      <Button
        size="sm"
        variant="ghost"
        pending={dismiss.isPending}
        onClick={() => {
          track('match.dismissed', { application_id: appId, source: 'detail_dismiss' })
          dismiss.mutate()
        }}
      >
        Dismiss
      </Button>
    )
  } else if (status === 'applied') {
    middle = (
      <Button
        size="sm"
        variant="ghost"
        pending={moveBackToPending.isPending}
        onClick={() => {
          track('match.unapplied', { application_id: appId })
          moveBackToPending.mutate()
        }}
      >
        Unapply
      </Button>
    )
  } else if (status === 'dismissed') {
    middle = (
      <Button
        size="sm"
        variant="ghost"
        pending={moveBackToPending.isPending}
        onClick={() => {
          track('match.undismissed', { application_id: appId, source: 'detail_restore' })
          moveBackToPending.mutate()
        }}
      >
        Restore
      </Button>
    )
  }

  return (
    <div className="hidden md:flex items-center gap-2">
      {middle}
      <HeaderApplyButton appId={appId} status={status} applyUrl={applyUrl} />
    </div>
  )
}
