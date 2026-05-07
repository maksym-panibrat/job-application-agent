import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ToastProvider } from './ui/Toast'

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@example.com' },
    token: 'fake',
    loading: false,
    signOut: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('../lib/track', () => ({
  track: vi.fn(),
}))

import { AppShell } from './AppShell'
import { CoachDrawer } from './coach/CoachDrawer'

function renderShell(pathname = '/') {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <AppShell>
        <p>page body</p>
      </AppShell>
    </MemoryRouter>
  )
}

function renderShellWithCoach(pathname = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[pathname]}>
          <AppShell>
            <p>page body</p>
          </AppShell>
          <CoachDrawer />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
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

  it('opens the Coach drawer when Coach is clicked', async () => {
    const user = userEvent.setup()
    renderShellWithCoach('/')

    await user.click(screen.getByRole('button', { name: /coach/i }))

    expect(screen.getByRole('dialog', { name: 'Coach' })).toBeInTheDocument()
  })
})
