import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, SyncStatus } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

const POLL_MS = 3_000

function liveLabel(s: SyncStatus): string {
  if (s.state === 'syncing') {
    const done = s.slugs_total - s.slugs_pending
    return `Searching ${done} of ${s.slugs_total} boards…`
  }
  if (s.state === 'matching') {
    return `Scoring ${s.matches_pending} job${s.matches_pending === 1 ? '' : 's'}…`
  }
  return 'Sync now'
}

export function SyncRow() {
  const qc = useQueryClient()
  const { show } = useToast()
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const prevState = useRef<SyncStatus['state'] | null>(null)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const body = await api.getSyncStatus()
        if (cancelled) return
        setStatus(body)
        if (prevState.current && prevState.current !== 'idle' && body.state === 'idle') {
          qc.invalidateQueries({ queryKey: ['applications'] })
        }
        prevState.current = body.state
      } catch {
        // Silent — the button just stays "Sync now" without live state.
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [qc])

  const sync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: (data) => {
      show(`Searching now — ${data.matched_now ?? 0} from cache.`, 'success')
      setTimeout(() => qc.invalidateQueries({ queryKey: ['applications'] }), 1500)
    },
    onError: (err) => {
      show((err as Error)?.message ?? 'Sync failed — try again', 'error')
    },
  })

  const isLive = status?.state && status.state !== 'idle'
  const label = status ? liveLabel(status) : 'Sync now'
  const lastSyncCopy = status?.last_sync_completed_at
    ? 'Last synced a few minutes ago · we re-check every few hours'
    : ''

  return (
    <div className="flex items-center justify-between gap-3 mb-2">
      <Button
        variant={isLive ? 'ghost' : 'secondary'}
        pending={sync.isPending}
        disabled={!!isLive}
        onClick={() => sync.mutate()}
        size="sm"
      >
        {sync.isPending ? 'Syncing…' : label}
      </Button>
      {!isLive && lastSyncCopy && (
        <span className="text-xs text-subtle">{lastSyncCopy}</span>
      )}
    </div>
  )
}
