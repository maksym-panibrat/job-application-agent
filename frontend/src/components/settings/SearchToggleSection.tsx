import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface SearchToggleSectionProps {
  active: boolean
  expiresAt: string | null
  paidActive?: boolean
}

function daysUntil(iso: string | null): number | null {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return null
  return Math.ceil(ms / 86_400_000)
}

export function SearchToggleSection({ active, expiresAt, paidActive = false }: SearchToggleSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const toggle = useMutation({
    mutationFn: (next: boolean) => api.toggleSearch(next),
    onSuccess: () => {
      track('settings.search_toggled', { to: active ? 'paused' : 'active' })
      qc.invalidateQueries({ queryKey: ['profile'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not update search', 'error'),
  })
  const days = daysUntil(expiresAt)

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Search</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-text">
            {active ? 'Search active' : 'Search paused'}
          </p>
          {active && !paidActive && days != null && (
            <p className="text-xs text-muted mt-0.5">Auto-pause in {days} day{days === 1 ? '' : 's'}</p>
          )}
        </div>
        <Button
          size="sm"
          variant={active ? 'secondary' : 'primary'}
          pending={toggle.isPending}
          onClick={() => toggle.mutate(!active)}
        >
          {active ? 'Pause' : 'Resume'}
        </Button>
      </div>
    </section>
  )
}
