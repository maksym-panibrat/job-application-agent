import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import RequireAuth from './RequireAuth'

vi.mock('../context/AuthContext', () => ({
  useAuth: vi.fn(),
}))

import { useAuth } from '../context/AuthContext'

describe('RequireAuth', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders children when user is authenticated', () => {
    vi.mocked(useAuth).mockReturnValue({
      user: { id: '1', email: 'user@test.com' },
      token: 'tok',
      loading: false,
      signOut: vi.fn(),
    })

    render(
      <MemoryRouter>
        <RequireAuth><div>Protected Content</div></RequireAuth>
      </MemoryRouter>
    )

    expect(screen.getByText('Protected Content')).toBeInTheDocument()
  })

  it('shows loading state while auth resolves', () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      loading: true,
      signOut: vi.fn(),
    })

    render(
      <MemoryRouter>
        <RequireAuth><div>Protected Content</div></RequireAuth>
      </MemoryRouter>
    )

    expect(screen.getByText('Loading...')).toBeInTheDocument()
    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument()
  })

  it('redirects to / when user is null and loading is false', () => {
    vi.mocked(useAuth).mockReturnValue({
      user: null,
      token: null,
      loading: false,
      signOut: vi.fn(),
    })

    render(
      <MemoryRouter initialEntries={['/matches']}>
        <RequireAuth><div>Protected Content</div></RequireAuth>
      </MemoryRouter>
    )

    expect(screen.queryByText('Protected Content')).not.toBeInTheDocument()
  })
})
