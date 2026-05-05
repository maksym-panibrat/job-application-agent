import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Landing from './Landing'

describe('Landing', () => {
  let originalLocation: Location

  beforeEach(() => {
    originalLocation = window.location
    // jsdom's Location is non-configurable; replace for assignment assertions.
    delete (window as unknown as { location?: Location }).location
    ;(window as unknown as { location: { href: string } }).location = { href: '' } as Location
  })

  afterEach(() => {
    ;(window as unknown as { location: Location }).location = originalLocation
    vi.restoreAllMocks()
  })

  it('fetches /auth/google/authorize and navigates to authorization_url on click', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?x=1' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<Landing />)
    await userEvent.click(screen.getByRole('button', { name: /Sign in with Google/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/auth/google/authorize', { credentials: 'same-origin' })
    })
    await waitFor(() => {
      expect(window.location.href).toBe('https://accounts.google.com/o/oauth2/v2/auth?x=1')
    })
  })

  it('shows a user-facing error if the authorize endpoint fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    render(<Landing />)
    await userEvent.click(screen.getByRole('button', { name: /Sign in with Google/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/Sign-in is unavailable/i)
    })
    expect(window.location.href).toBe('')
  })

  it('shows a user-facing error if the response lacks authorization_url', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }))
    vi.spyOn(console, 'error').mockImplementation(() => {})

    render(<Landing />)
    await userEvent.click(screen.getByRole('button', { name: /Sign in with Google/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/Sign-in is unavailable/i)
    })
    expect(window.location.href).toBe('')
  })

  // Dev-only "skip OAuth" button — gated on `import.meta.env.DEV` so production
  // builds tree-shake it away. Defense in depth: even if the button somehow
  // reached prod, /api/test/login is only mounted when ENVIRONMENT is
  // development or test (app/main.py), so it would 404. Vitest runs in DEV
  // mode by default, so the button is rendered for these tests.

  it('renders a dev-only "Skip OAuth" button in development builds', () => {
    render(<Landing />)
    expect(
      screen.getByRole('button', { name: /Dev login \(skip OAuth\)/i })
    ).toBeInTheDocument()
  })

  it('dev login posts to /api/test/login, stores token, then navigates to /matches', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ access_token: 'dev-jwt-xyz', user_id: 'u-1', email: 'e2e@local' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    sessionStorage.clear()

    render(<Landing />)
    await userEvent.click(screen.getByRole('button', { name: /Dev login \(skip OAuth\)/i }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/api/test/login', { method: 'POST' })
    })
    await waitFor(() => {
      expect(sessionStorage.getItem('access_token')).toBe('dev-jwt-xyz')
    })
    await waitFor(() => {
      expect(window.location.href).toBe('/matches')
    })
    sessionStorage.clear()
  })

  it('shows a user-facing error if dev login fails (e.g. backend not in dev mode)', async () => {
    // Production-style backend: /api/test/login isn't mounted → 404.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) })
    )
    vi.spyOn(console, 'error').mockImplementation(() => {})

    render(<Landing />)
    await userEvent.click(screen.getByRole('button', { name: /Dev login \(skip OAuth\)/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/Dev login failed/i)
    })
    expect(sessionStorage.getItem('access_token')).toBeNull()
    expect(window.location.href).toBe('')
  })
})
