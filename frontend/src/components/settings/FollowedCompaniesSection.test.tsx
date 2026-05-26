import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../ui/Toast'
import { FollowedCompaniesSection } from './FollowedCompaniesSection'
import { api } from '../../api/client'
import { track } from '../../lib/track'

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

vi.mock('../../lib/track', () => ({
  track: vi.fn(),
}))

const defaultCatalog = [
  { id: 'cat-1', canonical_name: 'Anthropic' },
  { id: 'cat-2', canonical_name: 'Linear' },
  { id: 'cat-3', canonical_name: 'Stripe' },
]

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
  ;(api.getCompanyCatalog as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(defaultCatalog)
  ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockReset()
  ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockReset()
})

describe('FollowedCompaniesSection', () => {
  it('shows existing companies as chips', () => {
    render(withCtx(
      <FollowedCompaniesSection companies={[{ id: 'a', canonical_name: 'Linear' }]} limit={5} />
    ))
    expect(screen.getByText('Linear')).toBeInTheDocument()
  })

  it('shows the current followed company count against the limit', () => {
    render(withCtx(
      <FollowedCompaniesSection companies={[{ id: 'a', canonical_name: 'Linear' }]} limit={5} />
    ))

    expect(screen.getByText('1 / 5 followed')).toBeInTheDocument()
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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))

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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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
      ]} limit={5} />
    ))

    fireEvent.click(screen.getByLabelText(/Remove Linear/i))
    await waitFor(() =>
      expect(api.updateProfile).toHaveBeenCalledWith({ target_company_ids: ['b'] })
    )
  })

  it('opens the typeahead dropdown when the user types', async () => {
    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
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
      ]} limit={5} />
    ))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'a')

    // 'Linear' matches 'a'; 'Anthropic' does too but is filtered out.
    expect(await screen.findByText('Linear')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Anthropic' })).not.toBeInTheDocument()
  })

  it('Esc closes the dropdown without selecting', async () => {
    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'lin')

    await screen.findByText('Linear')
    await userEvent.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('option', { name: 'Linear' })).not.toBeInTheDocument())
    // Draft text stays in the input.
    expect(input).toHaveValue('lin')
  })

  it('does not show "No matches" while the catalog is loading', async () => {
    // Deferred mock — the promise never resolves during this test, so
    // useQuery stays in the isPending state.
    ;(api.getCompanyCatalog as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise(() => {})
    )

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))
    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'lin')

    // The listbox should be open (user has typed) but no match copy should
    // render because the catalog query is still pending.
    expect(screen.queryByText(/No matches/i)).not.toBeInTheDocument()
  })

  it('falls through to typed-name resolve when catalog fetch fails', async () => {
    ;(api.getCompanyCatalog as unknown as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('catalog server down')
    )
    ;(api.resolveCompany as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'fallback-id',
      canonical_name: 'typed-name',
      providers: ['greenhouse'],
    })
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'p',
      updated: true,
    })

    render(withCtx(<FollowedCompaniesSection companies={[]} limit={5} />))

    // Wait for the catalog query to settle into the error state, which
    // is when the once-per-mount telemetry fires.
    await waitFor(() =>
      expect(track).toHaveBeenCalledWith('settings.catalog_failed', expect.anything())
    )

    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'typed-name{Enter}')

    await waitFor(() =>
      expect(api.resolveCompany).toHaveBeenCalledWith('typed-name')
    )
  })

  it('does not resolve or save when adding at the followed-company limit', async () => {
    render(withCtx(
      <FollowedCompaniesSection companies={[
        { id: 'a', canonical_name: 'Linear' },
      ]} limit={1} />
    ))

    const input = screen.getByPlaceholderText(/Add a company/i)
    await userEvent.type(input, 'Stripe{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent(/limit/i)
    expect(api.resolveCompany).not.toHaveBeenCalled()
    expect(api.updateProfile).not.toHaveBeenCalled()
  })

  it('still removes companies while at the followed-company limit', async () => {
    ;(api.updateProfile as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 'p',
      updated: true,
    })

    render(withCtx(
      <FollowedCompaniesSection companies={[
        { id: 'a', canonical_name: 'Linear' },
      ]} limit={1} />
    ))

    fireEvent.click(screen.getByLabelText(/Remove Linear/i))

    await waitFor(() =>
      expect(api.updateProfile).toHaveBeenCalledWith({ target_company_ids: [] })
    )
    expect(api.resolveCompany).not.toHaveBeenCalled()
  })
})
