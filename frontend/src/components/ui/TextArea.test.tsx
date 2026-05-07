import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TextArea } from './TextArea'

describe('TextArea', () => {
  it('renders with label', () => {
    render(<TextArea label="Notes" />)
    expect(screen.getByLabelText('Notes')).toBeInTheDocument()
  })

  it('reflects typed value', async () => {
    const user = userEvent.setup()
    render(<TextArea label="x" />)
    const ta = screen.getByLabelText('x')
    await user.type(ta, 'hi')
    expect(ta).toHaveValue('hi')
  })

  it('shows error and sets aria-invalid', () => {
    render(<TextArea label="x" error="too long" />)
    expect(screen.getByLabelText('x')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('too long')).toBeInTheDocument()
  })

  it('respects passed rows when set', () => {
    render(<TextArea label="x" rows={6} />)
    expect(screen.getByLabelText('x')).toHaveAttribute('rows', '6')
  })
})
