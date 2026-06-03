import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { track } from '../lib/track'
import { useStatusFilter } from '../lib/useStatusFilter'
import { StatusChips, StatusCounts } from '../components/feed/StatusChips'
import { SwipeableList, Type } from 'react-swipeable-list'
import { MatchCard } from '../components/feed/MatchCard'
import { ProfileCompletenessCard } from '../components/feed/ProfileCompletenessCard'
import { SkeletonCard } from '../components/ui/Skeleton'
import { EmptyState } from '../components/ui/EmptyState'

const SERVER_STATUS_BY_FILTER = {
  pending: 'pending_review',
  applied: 'applied',
  dismissed: 'dismissed',
} as const

export default function Matches() {
  const { status } = useStatusFilter()

  const { data: profile } = useQuery({ queryKey: ['profile'], queryFn: api.getProfile })

  const apps = useQuery({
    queryKey: ['applications', status],
    queryFn: () => api.listApplications({ status: SERVER_STATUS_BY_FILTER[status] }),
    refetchInterval: 30_000,
  })

  const summary = useQuery({
    queryKey: ['applications', 'summary'],
    queryFn: api.getApplicationSummary,
    refetchInterval: 30_000,
  })

  const counts: StatusCounts = {
    pending: summary.data?.pending_review ?? 0,
    applied: summary.data?.applied ?? 0,
    dismissed: summary.data?.dismissed ?? 0,
  }

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
          <SwipeableList
            type={Type.IOS}
            fullSwipe
            threshold={0.4}
            className="space-y-2"
            style={{ height: 'auto', overflowY: 'visible' }}
          >
            {apps.data.map((app) => <MatchCard key={app.id} app={app} />)}
          </SwipeableList>
        )}
      </div>
    </div>
  )
}
