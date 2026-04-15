import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'

export default function Applied() {
  const { data: applied, isLoading: loadingApplied } = useQuery({
    queryKey: ['applications', 'applied'],
    queryFn: () => api.listApplications({ status: 'applied' }),
  })
  const { data: dismissed, isLoading: loadingDismissed } = useQuery({
    queryKey: ['applications', 'dismissed'],
    queryFn: () => api.listApplications({ status: 'dismissed' }),
  })

  if (loadingApplied || loadingDismissed) {
    return <div className="flex items-center justify-center h-48 text-gray-400">Loading...</div>
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-gray-900 mb-6">History</h1>

      <section className="mb-8">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
          Applied ({applied?.length ?? 0})
        </h2>
        {!applied?.length ? (
          <p className="text-sm text-gray-400">No applications submitted yet.</p>
        ) : (
          <div className="space-y-2">
            {applied.map((app) => (
              <Link
                key={app.id}
                to={`/matches/${app.id}`}
                className="block p-3 bg-white rounded-md border border-gray-200 hover:border-gray-300 transition-colors"
              >
                <p className="font-medium text-gray-900 text-sm">{app.job?.title}</p>
                <p className="text-xs text-gray-500">{app.job?.company_name}</p>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
          Dismissed ({dismissed?.length ?? 0})
        </h2>
        {!dismissed?.length ? (
          <p className="text-sm text-gray-400">No dismissed jobs.</p>
        ) : (
          <div className="space-y-2">
            {dismissed.map((app) => (
              <div key={app.id} className="p-3 bg-gray-50 rounded-md border border-gray-100">
                <p className="font-medium text-gray-600 text-sm">{app.job?.title}</p>
                <p className="text-xs text-gray-400">{app.job?.company_name}</p>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
