import { useState } from 'react'
import { track } from '../lib/track'

export default function Landing() {
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  // fastapi-users' /auth/google/authorize returns {authorization_url: ...} as
  // JSON (not a 302 redirect), so a plain <a href> would render the raw JSON
  // body and dead-end the user. Fetch it, then navigate. The Set-Cookie
  // (CSRF) on that response is still persisted by the browser since this is
  // same-origin with Cloud Run.
  async function startGoogleLogin() {
    track('auth.signin_clicked', { method: 'google' })
    setError(null)
    setPending(true)
    try {
      const res = await fetch('/auth/google/authorize', { credentials: 'same-origin' })
      if (!res.ok) throw new Error(`authorize returned ${res.status}`)
      const data = await res.json()
      if (!data?.authorization_url) throw new Error('missing authorization_url')
      track('auth.signin_succeeded', { method: 'google' })
      window.location.href = data.authorization_url
    } catch (err) {
      track('auth.signin_failed', { method: 'google', reason: String(err) })
      setPending(false)
      setError('Sign-in is unavailable right now. Please try again in a moment.')
      console.error('Google OAuth start failed', err)
    }
  }

  // Dev-only escape hatch. Calls the deterministic test-user login endpoint
  // (mounted only when ENVIRONMENT is development or test — see app/main.py),
  // stores the JWT in sessionStorage, and full-page-navigates to /matches so
  // AuthProvider re-mounts and reads the freshly-set token.
  async function startDevLogin() {
    track('auth.signin_clicked', { method: 'dev' })
    setError(null)
    setPending(true)
    try {
      const res = await fetch('/api/test/login', { method: 'POST' })
      if (!res.ok) throw new Error(`test login returned ${res.status}`)
      const data = await res.json()
      if (!data?.access_token) throw new Error('missing access_token')
      sessionStorage.setItem('access_token', data.access_token)
      track('auth.signin_succeeded', { method: 'dev' })
      window.location.href = '/matches'
    } catch (err) {
      track('auth.signin_failed', { method: 'dev', reason: String(err) })
      setPending(false)
      setError('Dev login failed. Is the backend running with ENVIRONMENT=development?')
      console.error('Dev login failed', err)
    }
  }

  return (
    <div className="min-h-screen bg-bg flex flex-col items-center justify-center gap-8 px-4">
      <div className="text-center max-w-lg">
        <h1 className="text-3xl font-bold text-text mb-3 tracking-tight">Job Search</h1>
        <p className="text-muted">
          AI-powered job matching. Upload your resume, set your preferences, and get
          tailored applications generated automatically.
        </p>
      </div>
      <button
        type="button"
        onClick={startGoogleLogin}
        disabled={pending}
        className="inline-flex items-center gap-3 px-6 py-3 bg-surface border border-border-strong rounded-lg-token text-text font-medium hover:bg-surface-2 transition-colors disabled:opacity-60 disabled:cursor-not-allowed min-h-[48px]"
      >
        <svg className="w-5 h-5" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
        </svg>
        {pending ? 'Redirecting…' : 'Sign in with Google'}
      </button>
      {error && (
        <p role="alert" className="text-sm text-danger -mt-4">{error}</p>
      )}
      {/* `import.meta.env.DEV` is a Vite compile-time constant: in production
          builds it folds to `false` and the entire block is dead-code-eliminated.
          The /api/test/login endpoint is also gated server-side, so this is
          defense in depth, not the primary safety boundary. */}
      {import.meta.env.DEV && (
        <button
          type="button"
          onClick={startDevLogin}
          disabled={pending}
          className="text-xs text-subtle hover:text-text underline disabled:opacity-60 disabled:cursor-not-allowed"
        >
          Dev login (skip OAuth)
        </button>
      )}
      <a href="https://github.com/maksym-panibrat/job-application-agent" className="text-sm text-subtle hover:text-text">
        View on GitHub
      </a>
    </div>
  )
}
