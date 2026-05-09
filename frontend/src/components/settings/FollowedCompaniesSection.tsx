import { useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface Company {
  id: string
  canonical_name: string
}

export interface FollowedCompaniesSectionProps {
  companies: Company[]
}

export function FollowedCompaniesSection({ companies }: FollowedCompaniesSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [optimistic, setOptimistic] = useState<Company[]>(companies)
  const [busy, setBusy] = useState(false)
  const lastCompaniesRef = useRef<Company[]>(companies)

  // Sync from parent only when the prop reference actually changes
  // (e.g. profile refetch after a successful PATCH). Without the ref guard,
  // any unrelated re-render would clobber a freshly-set optimistic value.
  if (lastCompaniesRef.current !== companies) {
    lastCompaniesRef.current = companies
    setOptimistic(companies)
  }

  const patch = useMutation({
    mutationFn: (ids: string[]) => api.updateProfile({ target_company_ids: ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
  })

  async function add() {
    const name = draft.trim()
    if (!name) return
    setError(null)
    setBusy(true)
    let resolved: { id: string; canonical_name: string } | null = null
    try {
      resolved = await api.resolveCompany(name)
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
      return
    }
    const next = [...optimistic, { id: resolved.id, canonical_name: resolved.canonical_name }]
    setOptimistic(next)
    setDraft('')
    track('settings.company_added', { company_id: resolved.id, canonical_name: resolved.canonical_name })
    try {
      await patch.mutateAsync(next.map(c => c.id))
    } catch (e) {
      setOptimistic(optimistic)
      show((e as Error)?.message ?? 'Could not save', 'error')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    const company = optimistic.find(c => c.id === id)
    const next = optimistic.filter(c => c.id !== id)
    setOptimistic(next)
    track('settings.company_removed', { company_id: id, canonical_name: company?.canonical_name })
    try {
      await patch.mutateAsync(next.map(c => c.id))
    } catch (e) {
      setOptimistic(optimistic)
      show((e as Error)?.message ?? 'Could not save', 'error')
    }
  }

  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Followed companies</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-3">
        <p className="text-sm text-subtle">We'll match you to roles posted by these companies.</p>
        <div className="flex flex-wrap gap-2">
          {optimistic.map(c => (
            <span
              key={c.id}
              className="inline-flex items-center gap-1 px-2 py-1 bg-surface-2 border border-border rounded-pill text-xs text-text"
            >
              {c.canonical_name}
              <button
                type="button"
                aria-label={`Remove ${c.canonical_name}`}
                onClick={() => remove(c.id)}
                className="text-muted hover:text-danger"
              >×</button>
            </span>
          ))}
          {optimistic.length === 0 && (
            <p className="text-xs text-subtle">No companies followed yet.</p>
          )}
        </div>
        <div>
          <input
            type="text"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void add() } }}
            placeholder="Add a company you want to follow"
            disabled={busy}
            className="w-full bg-bg text-text border border-border rounded-md-token px-2 py-1.5 text-sm min-h-[36px] focus:outline-2 focus:outline-accent/40 focus:border-accent"
          />
          {error && (
            <p role="alert" className="text-xs text-danger mt-1">{error}</p>
          )}
        </div>
      </div>
    </section>
  )
}
