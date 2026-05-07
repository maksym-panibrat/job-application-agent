import { useMemo, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, Application } from '../api/client'
import { track } from '../lib/track'
import { useStatusFilter } from '../lib/useStatusFilter'
import { StatusChips, StatusCounts } from '../components/feed/StatusChips'
import { MatchCard } from '../components/feed/MatchCard'
import { ProfileCompletenessCard } from '../components/feed/ProfileCompletenessCard'
import { SkeletonCard } from '../components/ui/Skeleton'
import { EmptyState } from '../components/ui/EmptyState'

const SERVER_STATUS_BY_FILTER = {
  pending: 'pending_review',
  applied: 'applied',
  dismissed: 'dismissed',
} as const

function deriveCounts(byStatus: Partial<Record<'pending' | 'applied' | 'dismissed', Application[]>>): StatusCounts {
  return {
    pending:   byStatus.pending?.length ?? 0,
    applied:   byStatus.applied?.length ?? 0,
    dismissed: byStatus.dismissed?.length ?? 0,
  }
}

export default function Matches() {
  const { status } = useStatusFilter()

  const { data: profile } = useQuery({ queryKey: ['profile'], queryFn: api.getProfile })

  const apps = useQuery({
    queryKey: ['applications', status],
    queryFn: () => api.listApplications({ status: SERVER_STATUS_BY_FILTER[status] }),
    refetchInterval: 30_000,
  })

  const pendingQ   = useQuery({ queryKey: ['applications', 'pending'],   queryFn: () => api.listApplications({ status: 'pending_review' }), enabled: status !== 'pending'   })
  const appliedQ   = useQuery({ queryKey: ['applications', 'applied'],   queryFn: () => api.listApplications({ status: 'applied' }),        enabled: status !== 'applied'   })
  const dismissedQ = useQuery({ queryKey: ['applications', 'dismissed'], queryFn: () => api.listApplications({ status: 'dismissed' }),     enabled: status !== 'dismissed' })

  const counts = useMemo(() => deriveCounts({
    pending:   status === 'pending'   ? apps.data : pendingQ.data,
    applied:   status === 'applied'   ? apps.data : appliedQ.data,
    dismissed: status === 'dismissed' ? apps.data : dismissedQ.data,
  }), [status, apps.data, pendingQ.data, appliedQ.data, dismissedQ.data])

  useEffect(() => {
    if (apps.isLoading) return
    track('feed.viewed', {
      status_filter: status,
      count_pending: counts.pending,
      count_applied: counts.applied,
      count_dismissed: counts.dismissed,
    })
  }, [status, apps.isLoading, counts.pending, counts.applied, counts.dismissed])

  useEffect(() => {
    if (!apps.isLoading && (apps.data?.length ?? 0) === 0) {
      track('feed.empty_state_shown', { reason: status === 'pending' ? 'no_matches' : `no_${status}` })
    }
  }, [apps.isLoading, apps.data?.length, status])

  return (
    <div>
      {profile && <ProfileCompletenessCard profile={profile} />}

      <div className="sticky top-14 z-10 -mx-4 px-4 py-3 bg-bg/90 backdrop-blur border-b border-border">
        <StatusChips counts={counts} />
      </div>

      <div className="mt-4">
        {apps.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} data-testid="skel-card"><SkeletonCard /></div>
            ))}
          </div>
        ) : !apps.data?.length ? (
          <EmptyState
            title={status === 'pending' ? 'Caught up' : `No ${status} matches`}
            description={status === 'pending'
              ? 'We’ll surface new matches as boards refresh. Tap the refresh icon in the header to fetch now.'
              : `Nothing in your ${status} list yet.`}
          />
        ) : (
          <div className="space-y-2">
            {apps.data.map((app) => <MatchCard key={app.id} app={app} />)}
          </div>
        )}
      </div>
    </div>
  )
}
