import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { MatchCard } from '../components/MatchCard'
import { computeRefetchInterval, POST_SYNC_WINDOW_MS } from './refetchInterval'

function SkeletonCard() {
  return (
    <div className="bg-white rounded-lg shadow p-4 animate-pulse">
      <div className="h-4 bg-gray-200 rounded w-3/4 mb-2" />
      <div className="h-3 bg-gray-200 rounded w-1/2 mb-4" />
      <div className="h-3 bg-gray-200 rounded w-full mb-2" />
      <div className="h-3 bg-gray-200 rounded w-full" />
    </div>
  )
}

export default function Matches() {
  const qc = useQueryClient()
  const [postSyncUntilMs, setPostSyncUntilMs] = useState<number | null>(null)

  const { data: apps, isLoading } = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.listApplications({ status: 'pending_review' }),
    refetchInterval: () => computeRefetchInterval(postSyncUntilMs),
  })

  const sync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: () => {
      // Background scoring runs ~30s for a 20-job batch — poll aggressively
      // for a minute so freshly scored matches surface without a manual reload.
      setPostSyncUntilMs(Date.now() + POST_SYNC_WINDOW_MS)
      setTimeout(() => qc.invalidateQueries({ queryKey: ['applications'] }), 3000)
    },
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Job Matches</h1>
          <p className="text-sm text-gray-500 mt-0.5">{apps?.length ?? 0} pending review</p>
        </div>
        <button
          onClick={() => sync.mutate()}
          disabled={sync.isPending}
          className="px-3 py-1.5 text-sm font-medium bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {sync.isPending ? 'Syncing...' : 'Sync jobs'}
        </button>
      </div>

      {sync.data && (
        <div className="mb-4 p-3 bg-green-50 text-green-700 text-sm rounded-md">
          Sync complete: {sync.data.new_jobs} new jobs, {sync.data.updated_jobs} updated
        </div>
      )}

      {sync.isError && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 text-sm rounded-md">
          {(sync.error as Error)?.message ?? 'Sync failed.'}
        </div>
      )}

      {isLoading ? (
        <div className="grid gap-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : !apps?.length ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-sm mt-1">No matches yet. Click 'Sync jobs' to fetch new listings.</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {apps.map((app) => <MatchCard key={app.id} app={app} />)}
        </div>
      )}
    </div>
  )
}
