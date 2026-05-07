import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@example.com' },
    token: 'fake',
    loading: false,
    signOut: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import { AppShell } from './AppShell'

function renderShell(pathname = '/') {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <AppShell>
        <p>page body</p>
      </AppShell>
    </MemoryRouter>
  )
}

describe('AppShell (desktop)', () => {
  it('renders children inside <main>', () => {
    renderShell()
    expect(screen.getByText('page body')).toBeInTheDocument()
  })

  it('renders the brand link → /', () => {
    renderShell('/anywhere')
    const brand = screen.getByText('Job Agent')
    expect(brand.closest('a')).toHaveAttribute('href', '/')
  })

  it('renders Settings, Coach, Sign-out controls (desktop bar)', () => {
    renderShell()
    expect(screen.getByRole('link', { name: /settings/i })).toHaveAttribute('href', '/settings')
    expect(screen.getByRole('button', { name: /coach/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })

  it('renders the hamburger button (visible on mobile; rendered at all widths)', () => {
    renderShell()
    expect(screen.getByRole('button', { name: /open menu/i })).toBeInTheDocument()
  })
})
