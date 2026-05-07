import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ActionSheet } from './ActionSheet'

describe('ActionSheet', () => {
  it('renders nothing when closed', () => {
    render(
      <ActionSheet open={false} onClose={() => {}} title="Choose">
        <button>Item</button>
      </ActionSheet>
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders dialog with title and content when open', () => {
    render(
      <ActionSheet open onClose={() => {}} title="Choose">
        <button>Item A</button>
      </ActionSheet>
    )
    const dlg = screen.getByRole('dialog')
    expect(dlg).toBeInTheDocument()
    expect(dlg).toHaveAttribute('aria-label', 'Choose')
    expect(screen.getByText('Item A')).toBeInTheDocument()
  })

  it('calls onClose when Escape is pressed', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ActionSheet open onClose={onClose} title="x">
        <button>Item</button>
      </ActionSheet>
    )
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose when backdrop is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ActionSheet open onClose={onClose} title="x">
        <button>Item</button>
      </ActionSheet>
    )
    await user.click(screen.getByTestId('actionsheet-backdrop'))
    expect(onClose).toHaveBeenCalled()
  })

  it('does NOT call onClose when sheet content is clicked', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <ActionSheet open onClose={onClose} title="x">
        <button>Item</button>
      </ActionSheet>
    )
    await user.click(screen.getByText('Item'))
    expect(onClose).not.toHaveBeenCalled()
  })
})
