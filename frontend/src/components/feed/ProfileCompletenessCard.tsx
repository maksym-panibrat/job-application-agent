import { useMemo, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Profile } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

interface CheckItem {
  id: string
  label: string
  done: boolean
  promptSlug: string
}

function checks(profile: Profile): CheckItem[] {
  return [
    { id: 'resume',    label: 'Resume',         done: !!profile.base_resume_md,        promptSlug: 'set_resume' },
    { id: 'roles',     label: 'Target roles',   done: profile.target_roles.length > 0, promptSlug: 'set_roles' },
    { id: 'locations', label: 'Locations',      done: profile.target_locations.length > 0 || !!profile.remote_ok, promptSlug: 'set_locations' },
    { id: 'companies', label: 'Followed companies', done: (profile.target_companies?.length ?? 0) > 0, promptSlug: 'set_companies' },
  ]
}

export function ProfileCompletenessCard({ profile }: { profile: Profile }) {
  const qc = useQueryClient()
  const { show } = useToast()

  const items = useMemo(() => checks(profile), [profile])
  const allDone = items.every((c) => c.done)
  const paused = profile.search_active === false

  useEffect(() => {
    const checksDone = items.filter(c => c.done).length
    track('profile.completeness_viewed', {
      checks_done: checksDone, checks_total: items.length, paused,
    })
  }, [items, paused])

  const toggle = useMutation({
    mutationFn: (active: boolean) => api.toggleSearch(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not update search', 'error'),
  })

  // Healthy + active → render nothing
  if (allDone && !paused) return null

  // Paused state
  if (paused) {
    return (
      <div className="mb-4 p-4 bg-warning/5 border border-warning/30 rounded-lg-token">
        <p className="text-sm font-semibold text-text mb-1">Search is paused</p>
        <p className="text-xs text-muted mb-3">We won't surface new matches while paused.</p>
        <Button size="sm" pending={toggle.isPending} onClick={() => toggle.mutate(true)}>
          Resume search
        </Button>
      </div>
    )
  }

  // Setup state
  return (
    <div className="mb-4 p-4 bg-surface border border-border rounded-lg-token">
      <p className="text-sm font-semibold text-text mb-3">Set up your search</p>
      <ul className="space-y-2 text-sm">
        {items.map((c) => (
          <li key={c.id} className="flex items-center justify-between">
            <span className={c.done ? 'text-muted line-through' : 'text-text'}>
              <span className={`inline-block w-4 mr-2 ${c.done ? 'text-success' : 'text-subtle'}`}>
                {c.done ? '✓' : '○'}
              </span>
              {c.label}
            </span>
            {!c.done && (
              <Link
                to={`/?chat=1&prompt=${c.promptSlug}`}
                className="text-xs text-accent font-semibold px-2 py-1 rounded-md-token hover:bg-accent/10"
              >
                Open chat →
              </Link>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-subtle mt-3 pt-3 border-t border-border">
        {allDone
          ? 'Profile ready — your search will start automatically.'
          : 'Search will start automatically when these are set.'}
      </p>
    </div>
  )
}
