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

  it('preserves whitespace via whitespace-pre-wrap (no expander)', () => {
    render(<JobDescription content={'line one\n\nline two'} />)
    const pre = screen.getByText(/line one/, { exact: false })
    expect(pre.className).toMatch(/whitespace-pre-wrap/)
  })
})
