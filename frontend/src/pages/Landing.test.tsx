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
})
