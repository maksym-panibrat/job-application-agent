import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'
import { useApplyAction } from './useApplyAction'

export interface StickyActionsProps {
  appId: string
  status: string
  applyUrl: string
}

export function StickyActions({ appId, status, applyUrl }: StickyActionsProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const { onOpen, isApplied } = useApplyAction({ appId, status, applyUrl })

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(appId, 'dismissed'),
    onSuccess: () => {
      show('Dismissed', 'info')
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  if (isApplied) {
    return (
      <div className="md:hidden fixed bottom-0 inset-x-0 bg-success/10 border-t border-success/30 px-4 py-3 flex items-center justify-between">
        <span className="text-sm text-success font-semibold">✓ Applied</span>
        <a
          href={applyUrl}
          onClick={(e) => { e.preventDefault(); onOpen() }}
          className="text-sm text-success underline"
        >
          Open posting again ↗
        </a>
      </div>
    )
  }

  return (
    <div className="md:hidden fixed bottom-0 inset-x-0 bg-surface border-t border-border p-3 flex gap-2 items-center"
         style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))' }}>
      <Button
        size="md" variant="ghost"
        pending={dismiss.isPending}
        onClick={() => { track('match.dismissed', { application_id: appId, source: 'detail_skip' }); dismiss.mutate() }}
      >
        ⏷ Skip
      </Button>
      <a
        href={applyUrl}
        onClick={(e) => { e.preventDefault(); onOpen() }}
        className="flex-1 inline-flex items-center justify-center bg-accent text-accent-fg font-semibold rounded-md-token px-4 py-2.5 min-h-[40px]"
      >
        Open posting ↗
      </a>
    </div>
  )
}
