import { ScoreBadge } from '../feed/ScoreBadge'

export interface MatchAnalysisProps {
  score: number | null
  summary: string | null
  strengths: string[]
  gaps: string[]
}

export function MatchAnalysis({ score, summary, strengths, gaps }: MatchAnalysisProps) {
  if (score == null) return null
  const hasLists = strengths.length > 0 || gaps.length > 0
  return (
    <section className="mb-6 bg-surface-2 border border-border border-l-4 border-l-accent rounded-lg-token p-4">
      <div className="flex items-center gap-2 mb-2">
        <ScoreBadge score={score} />
      </div>
      {summary && <p className="text-sm text-muted leading-relaxed">{summary}</p>}
      {hasLists && (
        <>
          <hr className="my-3 border-border" />
          <div className="grid md:grid-cols-2 gap-4">
            {strengths.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-success mb-1">Strengths</p>
                <ul className="text-sm text-muted space-y-0.5">
                  {strengths.map((s, i) => <li key={i}><span aria-hidden>— </span><span>{s}</span></li>)}
                </ul>
              </div>
            )}
            {gaps.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-warning mb-1">Gaps</p>
                <ul className="text-sm text-muted space-y-0.5">
                  {gaps.map((g, i) => <li key={i}><span aria-hidden>— </span><span>{g}</span></li>)}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}
