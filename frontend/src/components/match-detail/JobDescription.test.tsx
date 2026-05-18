import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobDescription } from './JobDescription'

describe('JobDescription', () => {
  it('renders the description block when content present', () => {
    render(<JobDescription content="Acme is hiring engineers." />)
    expect(screen.getByText('Acme is hiring engineers.')).toBeInTheDocument()
    expect(screen.getByText(/job description/i)).toBeInTheDocument()
  })

  it('returns null when content is null', () => {
    const { container } = render(<JobDescription content={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('returns null on empty / whitespace-only content', () => {
    const { container } = render(<JobDescription content="   " />)
    expect(container.firstChild).toBeNull()
  })

  it('formats markdown instead of printing raw markdown syntax', () => {
    render(<JobDescription content={'## Requirements\n\n- **Python**\n- React'} />)

    expect(screen.getByRole('heading', { level: 2, name: 'Requirements' })).toBeInTheDocument()
    expect(screen.getByText('Python')).toHaveClass('font-semibold')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.queryByText('## Requirements')).not.toBeInTheDocument()
  })
})
