import { useAuth } from '../../context/AuthContext'
import { Button } from '../ui/Button'

export function AccountSection() {
  const { user, signOut } = useAuth()
  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Account</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 flex items-center justify-between">
        <p className="text-sm text-text">{user?.email ?? '—'}</p>
        <Button size="sm" variant="ghost" onClick={() => signOut()}>Sign out</Button>
      </div>
    </section>
  )
}
