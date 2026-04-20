import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { MatchCard } from '../components/MatchCard'

type Filter = 'all' | 'interested' | 'undecided'

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
  const [filter, setFilter] = useState<Filter>('all')

  const { data: apps, isLoading } = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.listApplications({ status: 'pending_review' }),
    refetchInterval: 10000,
  })

  const sync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ['applications'] }), 3000)
    },
  })

  const filtered = (() => {
    if (!apps) return []
    if (filter === 'interested') return apps.filter((a) => a.user_interest === 'interested')
    if (filter === 'undecided') return apps.filter((a) => a.user_interest === null)
    return apps
  })()

  const tabClass = (tab: Filter) =>
    filter === tab
      ? 'px-4 py-1.5 text-sm font-medium rounded-md bg-white shadow text-gray-800'
      : 'px-4 py-1.5 text-sm font-medium rounded-md text-gray-500 hover:text-gray-700'

  const emptyMessage = () => {
    if (filter === 'interested')
      return 'No matches marked as interested yet. Use the thumbs-up button on any match to save it here.'
    if (filter === 'undecided')
      return 'All matches have been reviewed. Check the Interested tab.'
    return 'No matches yet. Click \'Sync jobs\' to fetch new listings.'
  }

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

      <div className="mb-4 flex gap-1 bg-gray-100 p-1 rounded-lg w-fit">
        <button className={tabClass('all')} onClick={() => setFilter('all')}>All</button>
        <button className={tabClass('interested')} onClick={() => setFilter('interested')}>Interested</button>
        <button className={tabClass('undecided')} onClick={() => setFilter('undecided')}>Undecided</button>
      </div>

      {isLoading ? (
        <div className="grid gap-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : !filtered.length ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-sm mt-1">{emptyMessage()}</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {filtered.map((app) => <MatchCard key={app.id} app={app} />)}
        </div>
      )}
    </div>
  )
}
