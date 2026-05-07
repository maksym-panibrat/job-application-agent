import { Job } from '../../api/client'

function relativePosted(iso: string | null): string | null {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  const d = Math.floor(ms / 86_400_000)
  if (d <= 0) return 'posted today'
  if (d === 1) return 'posted 1d ago'
  if (d < 30) return `posted ${d}d ago`
  return `posted ${new Date(iso).toLocaleDateString()}`
}

export function MatchHero({ job }: { job: Job }) {
  const meta = [job.location, job.workplace_type, job.salary, relativePosted(job.posted_at)]
    .filter(Boolean)
    .join(' · ')
  return (
    <header className="mb-6">
      <p className="text-sm text-muted">{job.company_name}</p>
      <h1 className="text-2xl font-bold tracking-tight text-text mt-0.5">{job.title}</h1>
      {meta && <p className="text-xs text-subtle font-mono mt-2">{meta}</p>}
    </header>
  )
}
