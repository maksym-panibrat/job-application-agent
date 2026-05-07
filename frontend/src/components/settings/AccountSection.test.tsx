import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AccountSection } from './AccountSection'

vi.mock('../../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@x.com' },
    token: 'fake', loading: false, signOut: vi.fn(),
  }),
}))

describe('AccountSection', () => {
  it('renders the user email', () => {
    render(<AccountSection />)
    expect(screen.getByText('maks@x.com')).toBeInTheDocument()
  })

  it('renders a Sign out button', () => {
    render(<AccountSection />)
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })
})
