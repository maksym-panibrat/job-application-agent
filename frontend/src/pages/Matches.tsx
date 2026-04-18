import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, Application } from '../api/client'

function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  const color =
    pct >= 80 ? 'bg-green-100 text-green-800' :
    pct >= 65 ? 'bg-yellow-100 text-yellow-800' :
    'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
      {pct}% match
    </span>
  )
}

function GenerationBadge({ status }: { status: string }) {
  if (status === 'ready') return (
    <span className="text-xs text-green-600 font-medium">Documents ready</span>
  )
  if (status === 'generating' || status === 'pending') return (
    <span className="text-xs text-blue-500 animate-pulse">Preparing...</span>
  )
  if (status === 'failed') return (
    <span className="text-xs text-red-500">Generation failed</span>
  )
  return null
}

function MatchCard({ app }: { app: Application }) {
  const qc = useQueryClient()
  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'dismissed'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['applications'] }),
  })

  const job = app.job
  if (!job) return null

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-5 hover:border-gray-300 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <ScoreBadge score={app.match_score} />
            <GenerationBadge status={app.generation_status} />
          </div>
          <h3 className="mt-1.5 text-base font-semibold text-gray-900 truncate">{job.title}</h3>
          <p className="text-sm text-gray-600">{job.company_name}</p>
          {job.location && (
            <p className="text-xs text-gray-400 mt-0.5">
              {job.location}
              {job.workplace_type && ` · ${job.workplace_type}`}
            </p>
          )}
          {(job.salary || job.contract_type) && (
            <p className="text-xs text-gray-400 mt-0.5">
              {[job.salary, job.contract_type].filter(Boolean).join(' · ')}
            </p>
          )}
        </div>
        <div className="flex flex-col gap-2 shrink-0">
          <Link
            to={`/matches/${app.id}`}
            className="text-sm font-medium text-blue-600 hover:text-blue-700 whitespace-nowrap"
          >
            Review →
          </Link>
          <button
            onClick={() => dismiss.mutate()}
            className="text-xs text-gray-400 hover:text-gray-600 whitespace-nowrap"
          >
            Dismiss
          </button>
        </div>
      </div>

      {app.match_rationale && (
        <p className="mt-3 text-sm text-gray-600 line-clamp-2">{app.match_rationale}</p>
      )}

      {(app.match_strengths?.length > 0 || app.match_gaps?.length > 0) && (
        <div className="mt-3 flex gap-4 text-xs">
          {app.match_strengths?.length > 0 && (
            <div>
              <span className="text-green-600 font-medium">Strengths: </span>
              <span className="text-gray-600">{app.match_strengths.slice(0, 2).join(', ')}</span>
            </div>
          )}
          {app.match_gaps?.length > 0 && (
            <div>
              <span className="text-amber-600 font-medium">Gaps: </span>
              <span className="text-gray-600">{app.match_gaps.slice(0, 2).join(', ')}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Matches() {
  const qc = useQueryClient()
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

  if (isLoading) {
    return <div className="flex items-center justify-center h-48 text-gray-400">Loading...</div>
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

      {!apps?.length ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg">No matches yet</p>
          <p className="text-sm mt-1">Click "Sync jobs" to fetch and score job postings</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {apps.map((app) => <MatchCard key={app.id} app={app} />)}
        </div>
      )}
    </div>
  )
}
