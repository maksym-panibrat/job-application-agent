import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TextField } from './TextField'

describe('TextField', () => {
  it('renders with label and associates it via htmlFor/id', () => {
    render(<TextField label="Email" />)
    const input = screen.getByLabelText('Email')
    expect(input).toBeInTheDocument()
    expect(input.tagName).toBe('INPUT')
  })

  it('reflects the typed value', async () => {
    const user = userEvent.setup()
    render(<TextField label="Name" />)
    const input = screen.getByLabelText('Name')
    await user.type(input, 'Maks')
    expect(input).toHaveValue('Maks')
  })

  it('marks aria-invalid when error is provided', () => {
    render(<TextField label="X" error="bad" />)
    expect(screen.getByLabelText('X')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('bad')).toBeInTheDocument()
  })

  it('does not set aria-invalid when error is absent', () => {
    render(<TextField label="X" />)
    expect(screen.getByLabelText('X')).not.toHaveAttribute('aria-invalid')
  })
})
