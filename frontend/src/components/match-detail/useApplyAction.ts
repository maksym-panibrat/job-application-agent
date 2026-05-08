import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface UseApplyActionArgs {
  appId: string
  status: string
  applyUrl: string
}

export function useApplyAction({ appId, status, applyUrl }: UseApplyActionArgs) {
  const qc = useQueryClient()
  const { show } = useToast()

  const markApplied = useMutation({
    mutationFn: () => api.markApplied(appId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', appId] }),
    onError: (e) => show((e as Error)?.message ?? "Couldn't mark as applied — try again", 'error'),
  })

  const isApplied = status === 'applied'

  function onOpen() {
    track('match.original_posting_opened', { application_id: appId })
    window.open(applyUrl, '_blank', 'noopener')
    if (status === 'pending_review') {
      track('match.applied', { application_id: appId })
      markApplied.mutate()
    }
  }

  return { onOpen, isApplied, markApplied }
}
