import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Drawer } from './Drawer'

describe('Drawer', () => {
  it('renders nothing when closed', () => {
    render(
      <Drawer open={false} onClose={() => {}} title="Test drawer">
        <p>chat</p>
      </Drawer>
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders dialog with title and close button when open', () => {
    render(
      <Drawer open onClose={() => {}} title="Test drawer">
        <p>chat content</p>
      </Drawer>
    )
    const dlg = screen.getByRole('dialog')
    expect(dlg).toHaveAttribute('aria-label', 'Test drawer')
    expect(screen.getByRole('button', { name: 'Close drawer' })).toBeInTheDocument()
    expect(screen.getByText('chat content')).toBeInTheDocument()
  })

  it('calls onClose on close button', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Drawer open onClose={onClose} title="x">
        <p>x</p>
      </Drawer>
    )
    await user.click(screen.getByRole('button', { name: 'Close drawer' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose on Escape', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Drawer open onClose={onClose} title="x">
        <p>x</p>
      </Drawer>
    )
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onClose on backdrop click', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(
      <Drawer open onClose={onClose} title="x">
        <p>x</p>
      </Drawer>
    )
    await user.click(screen.getByTestId('drawer-backdrop'))
    expect(onClose).toHaveBeenCalled()
  })
})
