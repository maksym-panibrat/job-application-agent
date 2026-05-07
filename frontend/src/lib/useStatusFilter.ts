import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { track } from './track'

export type StatusFilter = 'pending' | 'applied' | 'dismissed'

const VALID: readonly StatusFilter[] = ['pending', 'applied', 'dismissed'] as const

function parse(raw: string | null): StatusFilter {
  if (raw && (VALID as readonly string[]).includes(raw)) return raw as StatusFilter
  return 'pending'
}

export interface UseStatusFilterResult {
  status: StatusFilter
  setStatus: (next: StatusFilter) => void
}

/** URL-driven status filter chip state (reads/writes ?status=).
 *  - "pending" is the default and is omitted from the URL for clean links.
 *  - Unknown ?status values are coerced to "pending". */
export function useStatusFilter(): UseStatusFilterResult {
  const [params, setParams] = useSearchParams()
  const status = parse(params.get('status'))

  const setStatus = useCallback((next: StatusFilter) => {
    if (next !== status) track('feed.status_filter_changed', { from: status, to: next })
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev)
        if (next === 'pending') out.delete('status')
        else out.set('status', next)
        return out
      },
      { replace: true },
    )
  }, [setParams, status])

  return { status, setStatus }
}
