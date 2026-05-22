import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function BudgetBanner() {
  const { data: status } = useQuery({
    queryKey: ['app-status'],
    queryFn: api.getStatus,
    staleTime: 10 * 60_000,
    refetchInterval: false,
  })

  if (!status?.budget_exhausted) return null

  const resumes = status.resumes_at
    ? new Date(status.resumes_at).toLocaleDateString(undefined, { month: 'long', day: 'numeric' })
    : 'next month'

  return (
    <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-center text-sm text-amber-800">
      AI features paused until {resumes} - job collection continues.
    </div>
  )
}
