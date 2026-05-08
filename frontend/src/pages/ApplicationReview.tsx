import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { track } from '../lib/track'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { IconButton } from '../components/ui/IconButton'
import { ActionSheet, ActionSheetItem } from '../components/ui/ActionSheet'
import { Kebab, Close } from '../components/ui/icons'
import { useToast } from '../components/ui/Toast'
import { MatchHero } from '../components/match-detail/MatchHero'
import { MatchAnalysis } from '../components/match-detail/MatchAnalysis'
import { JobDescription } from '../components/match-detail/JobDescription'
import { CoverLetterEditor } from '../components/match-detail/CoverLetterEditor'
import { StickyActions } from '../components/match-detail/StickyActions'
import { HeaderApplyButton } from '../components/match-detail/HeaderApplyButton'

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { show } = useToast()
  const [menuOpen, setMenuOpen] = useState(false)

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
    enabled: !!id,
  })

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'dismissed'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['applications'] })
      navigate(-1)
      show('Dismissed', 'info')
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  const moveBackToPending = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'pending_review'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not move back to pending', 'error'),
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
        <div className="flex items-center gap-2">
          <HeaderApplyButton appId={app.id} status={app.status} applyUrl={app.job.apply_url} />
          <IconButton aria-label="More actions" onClick={() => setMenuOpen(true)}>
            <Kebab className="w-4 h-4" />
          </IconButton>
        </div>
      </header>

      <div className="mt-4">
        <MatchHero job={app.job} />
        <MatchAnalysis
          score={app.match_score}
          summary={app.match_summary}
          strengths={app.match_strengths}
          gaps={app.match_gaps}
        />
        <JobDescription content={app.job.description_clean ?? app.job.description_md} />
        <CoverLetterEditor appId={app.id} doc={cover} status={app.generation_status} />
      </div>

      <StickyActions
        appId={app.id}
        status={app.status}
        applyUrl={app.job.apply_url}
      />

      <ActionSheet open={menuOpen} onClose={() => setMenuOpen(false)} title="Match actions">
        <ActionSheetItem onClick={() => { setMenuOpen(false); window.open(app.job!.apply_url, '_blank', 'noopener') }}>
          Open original posting ↗
        </ActionSheetItem>
        {app.status === 'applied' && (
          <ActionSheetItem onClick={() => {
            setMenuOpen(false)
            track('match.unapplied', { application_id: id })
            moveBackToPending.mutate()
          }}>
            Move back to pending
          </ActionSheetItem>
        )}
        {app.status !== 'dismissed' && (
          <ActionSheetItem intent="danger" onClick={() => { setMenuOpen(false); dismiss.mutate() }}>
            Dismiss
          </ActionSheetItem>
        )}
      </ActionSheet>
    </article>
  )
}
