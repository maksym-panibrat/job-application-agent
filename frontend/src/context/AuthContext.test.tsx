import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { AuthProvider, useAuth } from './AuthContext'

function AuthStatus() {
  const { user, loading } = useAuth()
  if (loading) return <div>loading</div>
  if (!user) return <div>no user</div>
  return <div>user:{user.email}</div>
}

function renderAuth() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <AuthStatus />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('starts in loading state then resolves', async () => {
    server.use(
      http.get('/api/users/me', () =>
        HttpResponse.json({ id: '1', email: 'test@test.com' })
      )
    )
    renderAuth()
    expect(screen.getByText('loading')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.queryByText('loading')).not.toBeInTheDocument()
    })
  })

  it('sets user when getMe succeeds (no token)', async () => {
    server.use(
      http.get('/api/users/me', () =>
        HttpResponse.json({ id: '1', email: 'test@test.com' })
      )
    )
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('user:test@test.com')).toBeInTheDocument()
    })
  })

  it('keeps user=null when getMe fails and no token in sessionStorage', async () => {
    server.use(http.get('/api/users/me', () => HttpResponse.error()))
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('no user')).toBeInTheDocument()
    })
  })

  it('calls getMe when token is present in sessionStorage', async () => {
    sessionStorage.setItem('access_token', 'test-token')
    let authHeader: string | null = null
    server.use(
      http.get('/api/users/me', ({ request }) => {
        authHeader = request.headers.get('Authorization')
        return HttpResponse.json({ id: '1', email: 'token-user@test.com' })
      })
    )
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('user:token-user@test.com')).toBeInTheDocument()
    })
    expect(authHeader).toBe('Bearer test-token')
  })

  it('clears token from sessionStorage when getMe fails with stored token', async () => {
    sessionStorage.setItem('access_token', 'bad-token')
    server.use(
      http.get('/api/users/me', () => new HttpResponse(null, { status: 401 }))
    )
    renderAuth()
    await waitFor(() => {
      expect(screen.getByText('no user')).toBeInTheDocument()
    })
    expect(sessionStorage.getItem('access_token')).toBeNull()
  })

  it('signOut clears user and token', async () => {
    // Replace window.location with a writable stub. Preserve the original
    // href so fetch can resolve relative URLs against a valid origin.
    const originalLocation = window.location
    const stub: { href: string } & Record<string, unknown> = {
      href: originalLocation.href,
      origin: originalLocation.origin,
      protocol: originalLocation.protocol,
      host: originalLocation.host,
      hostname: originalLocation.hostname,
      port: originalLocation.port,
      pathname: originalLocation.pathname,
      search: originalLocation.search,
      hash: originalLocation.hash,
      assign: () => {},
      replace: () => {},
      reload: () => {},
      toString: () => stub.href,
    }
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: stub,
    })

    try {
      server.use(
        http.get('/api/users/me', () =>
          HttpResponse.json({ id: '1', email: 'test@test.com' })
        )
      )

      function SignOutButton() {
        const { signOut, user } = useAuth()
        return (
          <div>
            {user ? <span>logged-in</span> : <span>logged-out</span>}
            <button onClick={signOut}>sign out</button>
          </div>
        )
      }

      sessionStorage.setItem('access_token', 'some-token')

      const user = userEvent.setup()
      render(
        <MemoryRouter>
          <AuthProvider>
            <SignOutButton />
          </AuthProvider>
        </MemoryRouter>
      )

      await waitFor(() => {
        expect(screen.getByText('logged-in')).toBeInTheDocument()
      })

      await user.click(screen.getByText('sign out'))
      expect(screen.getByText('logged-out')).toBeInTheDocument()
      expect(sessionStorage.getItem('access_token')).toBeNull()
      expect(stub.href).toBe('/')
    } finally {
      Object.defineProperty(window, 'location', {
        configurable: true,
        writable: true,
        value: originalLocation,
      })
    }
  })
})
