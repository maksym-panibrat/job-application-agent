import { useMutation, useQueryClient } from '@tanstack/react-query'
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

export function MatchCard({ app }: { app: Application }) {
  const qc = useQueryClient()

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'dismissed'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['applications'] }),
  })

  const interest = useMutation({
    mutationFn: (value: 'interested' | 'not_interested' | null) =>
      api.setInterest(app.id, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['applications'] }),
  })

  const job = app.job
  if (!job) return null

  const toggleInterest = (value: 'interested' | 'not_interested') => {
    interest.mutate(app.user_interest === value ? null : value)
  }

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
          <div className="flex gap-1 mt-1">
            <button
              aria-label="Mark as interested"
              onClick={() => toggleInterest('interested')}
              disabled={interest.isPending}
              className={`text-base leading-none disabled:opacity-50 ${app.user_interest === 'interested' ? 'text-green-600' : 'text-gray-300 hover:text-gray-500'}`}
            >
              👍
            </button>
            <button
              aria-label="Mark as not interested"
              onClick={() => toggleInterest('not_interested')}
              disabled={interest.isPending}
              className={`text-base leading-none disabled:opacity-50 ${app.user_interest === 'not_interested' ? 'text-red-500' : 'text-gray-300 hover:text-gray-500'}`}
            >
              👎
            </button>
          </div>
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
