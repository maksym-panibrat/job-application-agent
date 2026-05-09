import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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

const MAX_DROPDOWN_ROWS = 8

export function FollowedCompaniesSection({ companies }: FollowedCompaniesSectionProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [optimistic, setOptimistic] = useState<Company[]>(companies)
  const [busy, setBusy] = useState(false)
  const [highlight, setHighlight] = useState<number>(-1)
  const [open, setOpen] = useState(false)
  const prevCompaniesRef = useRef(companies)

  // Sync optimistic with prop changes (parent refetched profile). The !busy
  // guard prevents a parent refetch from clobbering the freshly-set optimistic
  // value while a PATCH is still in flight.
  useEffect(() => {
    if (prevCompaniesRef.current !== companies && !busy) {
      setOptimistic(companies)
      prevCompaniesRef.current = companies
    }
  }, [companies, busy])

  const { data: catalog = [] } = useQuery({
    queryKey: ['companies', 'catalog'],
    queryFn: api.getCompanyCatalog,
    staleTime: Infinity,
  })

  const followedIds = useMemo(() => new Set(optimistic.map(c => c.id)), [optimistic])

  const matches = useMemo(() => {
    const q = draft.trim().toLowerCase()
    if (!q) return [] as Company[]
    return catalog
      .filter(c => !followedIds.has(c.id))
      .filter(c => c.canonical_name.toLowerCase().includes(q))
      .slice(0, MAX_DROPDOWN_ROWS)
  }, [draft, catalog, followedIds])

  // Reset highlight whenever the match set changes.
  useEffect(() => { setHighlight(-1) }, [matches])

  const patch = useMutation({
    mutationFn: (ids: string[]) => api.updateProfile({ target_company_ids: ids }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
  })

  async function commit(name: string) {
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
    setOpen(false)
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

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (matches.length === 0) return
      setHighlight(h => Math.min(h + 1, matches.length - 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (matches.length === 0) return
      setHighlight(h => Math.max(h - 1, 0))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      if (matches.length > 0 && highlight >= 0 && highlight < matches.length) {
        void commit(matches[highlight].canonical_name)
      } else {
        const trimmed = draft.trim()
        if (trimmed) void commit(trimmed)
      }
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
        <div className="relative">
          <input
            type="text"
            value={draft}
            onChange={e => { setDraft(e.target.value); setOpen(e.target.value.trim().length > 0) }}
            onFocus={() => setOpen(draft.trim().length > 0)}
            onBlur={() => setTimeout(() => setOpen(false), 100)}
            onKeyDown={onKeyDown}
            placeholder="Add a company you want to follow"
            disabled={busy}
            className="w-full bg-bg text-text border border-border rounded-md-token px-2 py-1.5 text-sm min-h-[36px] focus:outline-2 focus:outline-accent/40 focus:border-accent"
          />
          {open && (
            <div
              role="listbox"
              className="absolute left-0 right-0 mt-1 bg-surface border border-border rounded-md-token shadow-lg z-10"
            >
              {matches.length === 0 ? (
                <p className="px-2 py-1.5 text-xs text-subtle">No matches — press Enter to search the boards</p>
              ) : (
                matches.map((c, i) => (
                  <div
                    key={c.id}
                    role="option"
                    aria-selected={highlight === i}
                    aria-label={c.canonical_name}
                    onMouseDown={(e) => { e.preventDefault(); void commit(c.canonical_name) }}
                    onMouseEnter={() => setHighlight(i)}
                    className={`px-2 py-1.5 text-sm cursor-pointer ${highlight === i ? 'bg-surface-2' : ''}`}
                  >
                    {c.canonical_name}
                  </div>
                ))
              )}
            </div>
          )}
          {error && (
            <p role="alert" className="text-xs text-danger mt-1">{error}</p>
          )}
        </div>
      </div>
    </section>
  )
}
