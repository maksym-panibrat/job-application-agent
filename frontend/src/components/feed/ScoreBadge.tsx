import { Badge, BadgeIntent } from '../ui/Badge'

function intentForScore(pct: number): BadgeIntent {
  if (pct >= 80) return 'success'
  if (pct >= 65) return 'warning'
  return 'muted'
}

export function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  return <Badge intent={intentForScore(pct)}>{pct}% match</Badge>
}
