import { useEffect, useState } from 'react'
import { api, AppStatus } from '../api/client'

export default function BudgetBanner() {
  const [status, setStatus] = useState<AppStatus | null>(null)

  useEffect(() => {
    api.getStatus().then(setStatus).catch(() => {})
    const id = setInterval(() => api.getStatus().then(setStatus).catch(() => {}), 60_000)
    return () => clearInterval(id)
  }, [])

  if (!status?.budget_exhausted) return null

  const resumes = status.resumes_at
    ? new Date(status.resumes_at).toLocaleDateString(undefined, { month: 'long', day: 'numeric' })
    : 'next month'

  return (
    <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-center text-sm text-amber-800">
      AI features paused until {resumes} — job collection continues.
    </div>
  )
}
