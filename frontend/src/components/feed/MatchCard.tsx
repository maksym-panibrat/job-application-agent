import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  SwipeableListItem,
  SwipeAction,
  TrailingActions,
} from 'react-swipeable-list'
import { api, Application } from '../../api/client'
import { track } from '../../lib/track'
import { Card } from '../ui/Card'
import { IconButton } from '../ui/IconButton'
import { ActionSheet, ActionSheetItem } from '../ui/ActionSheet'
import { Kebab } from '../ui/icons'
import { useToast } from '../ui/Toast'
import { ScoreBadge } from './ScoreBadge'
import { GenerationBadge } from './GenerationBadge'

function relativeAge(iso: string | null): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const d = Math.floor(ms / 86_400_000)
  if (d <= 0) return 'today'
  if (d === 1) return '1d ago'
  if (d < 30) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

export function MatchCard({ app }: { app: Application }) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [menuOpen, setMenuOpen] = useState(false)
  const isDismissed = app.status === 'dismissed'

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'dismissed'),
    onSuccess: () => {
      show(`Dismissed ${app.job?.title ?? 'match'}`, 'info')
      qc.invalidateQueries({ queryKey: ['applications'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  const restore = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'pending_review'),
    onSuccess: () => {
      show(`Restored ${app.job?.title ?? 'match'}`, 'info')
      qc.invalidateQueries({ queryKey: ['applications'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not restore', 'error'),
  })

  const job = app.job
  if (!job) return null

  const meta = [job.location, job.workplace_type, job.salary].filter(Boolean).join(' · ')
  const topStrength = app.match_strengths?.[0]
  const topGap = app.match_gaps?.[0]
  const age = relativeAge(job.posted_at) || relativeAge(app.created_at)

  const trailingActions = (
    <TrailingActions>
      <SwipeAction
        destructive
        onClick={() => {
          track('match.dismissed', { application_id: app.id, source: 'swipe', score: app.match_score })
          dismiss.mutate()
        }}
      >
        <div
          role="button"
          aria-label="Dismiss"
          className="flex items-center justify-center bg-danger text-white font-bold w-full h-full px-6"
        >
          Dismiss
        </div>
      </SwipeAction>
    </TrailingActions>
  )

  return (
    <SwipeableListItem trailingActions={isDismissed ? undefined : trailingActions} blockSwipe={isDismissed}>
      <div className="relative w-full">
        {/* Kebab in absolute corner — far from natural tap zone, doesn't interfere with the card link. */}
        <div className="absolute top-1 right-1 z-10">
          <IconButton
            aria-label="More actions"
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setMenuOpen(true) }}
          >
            <Kebab className="w-4 h-4" />
          </IconButton>
        </div>

        <Card as="rrlink" to={`/matches/${app.id}`} interactive onClick={() => track('match.card_opened', { application_id: app.id, score: app.match_score })} className="block pr-12 w-full">
          <div className="flex items-center gap-2 flex-wrap">
            <ScoreBadge score={app.match_score} />
            <GenerationBadge status={app.generation_status} />
            {age && <span className="ml-auto text-xs text-subtle font-mono">{age}</span>}
          </div>
          <h3 className="mt-2 text-base font-bold text-text tracking-tight truncate">{job.title}</h3>
          <p className="text-sm text-text">{job.company_name}</p>
          {meta && <p className="text-xs text-subtle font-mono mt-1">{meta}</p>}
          {(topStrength || topGap) && (
            <p className="text-xs text-muted mt-2 pt-2 border-t border-border">
              {topStrength && <><span className="text-success font-semibold">Strong:</span> {topStrength}</>}
              {topStrength && topGap && <span className="mx-1">·</span>}
              {topGap && <><span className="text-warning font-semibold">Gap:</span> {topGap}</>}
            </p>
          )}
        </Card>

        <ActionSheet open={menuOpen} onClose={() => setMenuOpen(false)} title="Match actions" heading={job.title}>
          <ActionSheetItem onClick={() => { setMenuOpen(false); show('Saved for later', 'info') }}>
            Save for later
          </ActionSheetItem>
          <ActionSheetItem onClick={() => {
            setMenuOpen(false)
            window.open(job.apply_url, '_blank', 'noopener')
          }}>
            Open original posting ↗
          </ActionSheetItem>
          {isDismissed ? (
            <ActionSheetItem onClick={() => {
              setMenuOpen(false)
              track('match.undismissed', { application_id: app.id, source: 'kebab' })
              restore.mutate()
            }}>
              Restore
            </ActionSheetItem>
          ) : (
            <ActionSheetItem intent="danger" onClick={() => {
              setMenuOpen(false)
              track('match.dismissed', { application_id: app.id, source: 'kebab', score: app.match_score })
              dismiss.mutate()
            }}>
              Dismiss
            </ActionSheetItem>
          )}
        </ActionSheet>
      </div>
    </SwipeableListItem>
  )
}
