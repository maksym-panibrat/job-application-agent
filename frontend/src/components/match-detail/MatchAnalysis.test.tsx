import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MatchAnalysis } from './MatchAnalysis'

describe('MatchAnalysis', () => {
  it('renders score, summary, and full strengths/gaps lists', () => {
    render(<MatchAnalysis
      score={0.87} summary="Strong fit on Go"
      strengths={['Go', 'Postgres', 'Distributed systems']}
      gaps={['No public ML', 'No public k8s']}
    />)
    expect(screen.getByText('87% match')).toBeInTheDocument()
    expect(screen.getByText('Strong fit on Go')).toBeInTheDocument()
    expect(screen.getByText('Go')).toBeInTheDocument()
    expect(screen.getByText('Postgres')).toBeInTheDocument()
    expect(screen.getByText('Distributed systems')).toBeInTheDocument()
    expect(screen.getByText('No public ML')).toBeInTheDocument()
    expect(screen.getByText('No public k8s')).toBeInTheDocument()
  })

  it('returns null when score is null', () => {
    const { container } = render(<MatchAnalysis score={null} summary={null} strengths={[]} gaps={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders just score + summary when strengths and gaps are empty', () => {
    render(<MatchAnalysis score={0.8} summary="ok" strengths={[]} gaps={[]} />)
    expect(screen.getByText('80% match')).toBeInTheDocument()
    expect(screen.queryByText(/strengths/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/gaps/i)).not.toBeInTheDocument()
  })
})
