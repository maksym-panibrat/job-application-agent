import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'

export interface TargetSlugsSectionProps {
  slugs: { greenhouse?: string[]; lever?: string[]; ashby?: string[] }
}

const PROVIDERS: Array<{ key: 'greenhouse' | 'lever' | 'ashby'; label: string }> = [
  { key: 'greenhouse', label: 'Greenhouse' },
  { key: 'lever',      label: 'Lever' },
  { key: 'ashby',      label: 'Ashby' },
]

export function TargetSlugsSection({ slugs }: TargetSlugsSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  const patch = useMutation({
    mutationFn: (next: TargetSlugsSectionProps['slugs']) =>
      api.updateProfile({ target_company_slugs: next }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not update slugs', 'error'),
  })

  function add(key: 'greenhouse' | 'lever' | 'ashby') {
    const draft = (drafts[key] ?? '').trim().toLowerCase()
    if (!draft) return
    const existing = slugs[key] ?? []
    if (existing.includes(draft)) return
    patch.mutate({ ...slugs, [key]: [...existing, draft] })
    setDrafts((d) => ({ ...d, [key]: '' }))
  }

  function remove(key: 'greenhouse' | 'lever' | 'ashby', s: string) {
    const existing = slugs[key] ?? []
    patch.mutate({ ...slugs, [key]: existing.filter((x) => x !== s) })
  }

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Target boards</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-4">
        {PROVIDERS.map(({ key, label }) => (
          <div key={key}>
            <p className="text-sm font-semibold text-text mb-2">{label}</p>
            <div className="flex flex-wrap gap-2 mb-2">
              {(slugs[key] ?? []).map((s) => (
                <span key={s} className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text">
                  {s}
                  <button
                    type="button"
                    aria-label={`Remove ${s}`}
                    onClick={() => remove(key, s)}
                    className="text-muted hover:text-danger"
                  >×</button>
                </span>
              ))}
              {(slugs[key] ?? []).length === 0 && (
                <p className="text-xs text-subtle">No {label.toLowerCase()} boards yet.</p>
              )}
            </div>
            <input
              type="text"
              value={drafts[key] ?? ''}
              onChange={(e) => setDrafts((d) => ({ ...d, [key]: e.target.value }))}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(key) } }}
              placeholder={`Add ${label} slug…`}
              className="w-full bg-bg text-text border border-border rounded-md-token px-2 py-1.5 text-sm min-h-[36px] focus:outline-2 focus:outline-accent/40 focus:border-accent"
            />
          </div>
        ))}
      </div>
    </section>
  )
}
