import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../ui/Toast'
import { FollowedCompaniesSection } from './FollowedCompaniesSection'
import { api } from '../../api/client'

vi.mock('../../api/client', () => ({
  api: {
    resolveCompany: vi.fn(),
    updateProfile: vi.fn(),
    getCompanyCatalog: vi.fn().mockResolvedValue([
      { id: 'cat-1', canonical_name: 'Anthropic' },
      { id: 'cat-2', canonical_name: 'Linear' },
      { id: 'cat-3', canonical_name: 'Stripe' },
    ]),
  },
}))

function withCtx(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('FollowedCompaniesSection', () => {
  it('shows existing companies as chips', () => {
    render(withCtx(
      <FollowedCompaniesSection companies={[{ id: 'a', canonical_name: 'Linear' }]} />
    ))
    expect(screen.getByText('Linear')).toBeInTheDocument()
  })

  it('resolves a typed company on Enter and adds a chip', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'b',
      canonical_name: 'Stripe',
      providers: ['greenhouse'],
    })
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'p',
      updated: true,
    })

    render(withCtx(<FollowedCompaniesSection companies={[]} />))

    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'Stripe{Enter}')

    await waitFor(() => expect(screen.getByText('Stripe')).toBeInTheDocument())
    expect(api.resolveCompany).toHaveBeenCalledWith('Stripe')
    expect(api.updateProfile).toHaveBeenCalledWith({ target_company_ids: ['b'] })
  })

  it('shows inline error on 404', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Couldn't find that company on any of our supported boards.")
    )

    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    await userEvent.type(
      screen.getByPlaceholderText(/Add a company/i),
      'nope-co{Enter}',
    )

    expect(await screen.findByText(/Couldn't find that company/i)).toBeInTheDocument()
  })

  it('shows inline error on 503', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Couldn't reach our boards right now, try again.")
    )

    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    await userEvent.type(
      screen.getByPlaceholderText(/Add a company/i),
      'Stripe{Enter}',
    )

    expect(await screen.findByText(/Couldn't reach our boards/i)).toBeInTheDocument()
  })

  it('rolls back chip on PATCH failure', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'c',
      canonical_name: 'Linear',
      providers: ['ashby'],
    })
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('boom')
    )

    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    await userEvent.type(
      screen.getByPlaceholderText(/Add a company/i),
      'Linear{Enter}',
    )

    await waitFor(() =>
      expect(screen.queryByText('Linear')).not.toBeInTheDocument()
    )
  })

  it('removes a chip and PATCHes without that id', async () => {
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'p',
      updated: true,
    })
    render(withCtx(
      <FollowedCompaniesSection companies={[
        { id: 'a', canonical_name: 'Linear' },
        { id: 'b', canonical_name: 'Stripe' },
      ]} />
    ))

    fireEvent.click(screen.getByLabelText(/Remove Linear/i))
    await waitFor(() =>
      expect(api.updateProfile).toHaveBeenCalledWith({ target_company_ids: ['b'] })
    )
  })

  it('opens the typeahead dropdown when the user types', async () => {
    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    const input = screen.getByPlaceholderText(/Add a company/i)

    await userEvent.type(input, 'lin')

    expect(await screen.findByText('Linear')).toBeInTheDocument()
    // Anthropic and Stripe don't match "lin" — should NOT appear in dropdown.
    expect(screen.queryByText('Anthropic')).not.toBeInTheDocument()
    expect(screen.queryByText('Stripe')).not.toBeInTheDocument()
  })

  it('selecting a dropdown row via Enter resolves and adds the chip', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'cat-3',
      canonical_name: 'Stripe',
      providers: ['greenhouse'],
    })
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'p', updated: true })

    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'str')

    // First (and only) match should be highlighted on first ↓; Enter selects.
    await screen.findByText('Stripe')
    await userEvent.keyboard('{ArrowDown}')
    await userEvent.keyboard('{Enter}')

    await waitFor(() => expect(screen.getAllByText('Stripe')).toHaveLength(1))
    expect(api.resolveCompany).toHaveBeenCalledWith('Stripe')
  })

  it('clicking a dropdown row resolves and adds the chip', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'cat-2',
      canonical_name: 'Linear',
      providers: ['ashby'],
    })
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 'p', updated: true })

    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'lin')

    const row = await screen.findByRole('option', { name: 'Linear' })
    await userEvent.click(row)

    expect(api.resolveCompany).toHaveBeenCalledWith('Linear')
  })

  it('Enter with no matches falls through to the existing resolve flow', async () => {
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Couldn't find that company on any of our supported boards.")
    )

    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'totally-fake-co{Enter}')

    // The dropdown shows the no-match copy.
    expect(await screen.findByText(/No matches/i)).toBeInTheDocument()
    // resolveCompany was called with the literal draft (not a catalog name).
    expect(api.resolveCompany).toHaveBeenCalledWith('totally-fake-co')
  })

  it('already-followed companies are filtered out of the dropdown', async () => {
    render(withCtx(
      <FollowedCompaniesSection companies={[
        { id: 'cat-1', canonical_name: 'Anthropic' },
      ]} />
    ))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'a')

    // 'Linear' matches 'a'; 'Anthropic' does too but is filtered out.
    expect(await screen.findByText('Linear')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Anthropic' })).not.toBeInTheDocument()
  })

  it('Esc closes the dropdown without selecting', async () => {
    render(withCtx(<FollowedCompaniesSection companies={[]} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'lin')

    await screen.findByText('Linear')
    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('option', { name: 'Linear' })).not.toBeInTheDocument())
    // Draft text stays in the input.
    expect(input).toHaveValue('lin')
  })
})
