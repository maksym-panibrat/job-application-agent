import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { IconButton } from '../components/ui/IconButton'
import { Close } from '../components/ui/icons'
import { MatchHero } from '../components/match-detail/MatchHero'
import { MatchAnalysis } from '../components/match-detail/MatchAnalysis'
import { JobDescription } from '../components/match-detail/JobDescription'
import { CoverLetterEditor } from '../components/match-detail/CoverLetterEditor'
import { StickyActions } from '../components/match-detail/StickyActions'
import { HeaderActions } from '../components/match-detail/HeaderActions'

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
    enabled: !!id,
  })

  if (isLoading || !app) {
    return <div className="flex items-center justify-center h-48 text-muted">Loading…</div>
  }
  if (!app.job) {
    return <div className="text-muted">Job data missing.</div>
  }

  const cover = app.documents?.find((d) => d.doc_type === 'cover_letter') ?? null

  return (
    <article className="pb-24 md:pb-6">
      <header className="sticky top-14 z-10 -mx-4 px-4 py-2 bg-bg/90 backdrop-blur border-b border-border flex items-center justify-between">
        <IconButton aria-label="Back" onClick={() => navigate(-1)}>
          <Close className="w-4 h-4" />
        </IconButton>
        <HeaderActions appId={app.id} status={app.status} applyUrl={app.job.apply_url} />
      </header>

      <div className="mt-4">
        <MatchHero job={app.job} />
        <MatchAnalysis
          score={app.match_score}
          summary={app.match_summary}
          strengths={app.match_strengths}
          gaps={app.match_gaps}
        />
        <JobDescription content={app.job.description} />
        <CoverLetterEditor appId={app.id} doc={cover} status={app.generation_status} />
      </div>

      <StickyActions
        appId={app.id}
        status={app.status}
        applyUrl={app.job.apply_url}
      />
    </article>
  )
}
