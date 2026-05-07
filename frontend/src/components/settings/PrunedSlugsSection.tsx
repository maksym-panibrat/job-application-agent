import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

export function PrunedSlugsSection() {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const { data } = useQuery({
    queryKey: ['sync-status-for-pruned'],
    queryFn: api.getSyncStatus,
    refetchInterval: 30_000,
  })
  const invalid = (data?.invalid_slugs ?? []).filter((s) => !dismissed.has(s))
  if (invalid.length === 0) return null

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Auto-removed boards</h2>
      <div className="bg-warning/5 border border-warning/30 rounded-lg-token p-4">
        <p className="text-xs text-muted mb-3">
          We removed these boards because they returned 404 too many times. Add them back below if they come online again.
        </p>
        <div className="flex flex-wrap gap-2">
          {invalid.map((s) => (
            <span key={s} className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text">
              <code className="font-mono">{s}</code>
              <button
                type="button"
                aria-label={`Dismiss ${s}`}
                onClick={() => setDismissed((d) => new Set([...d, s]))}
                className="text-muted hover:text-text"
              >×</button>
            </span>
          ))}
        </div>
      </div>
    </section>
  )
}
