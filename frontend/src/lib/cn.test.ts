import { describe, it, expect } from 'vitest'
import { cn } from './cn'

describe('cn', () => {
  it('joins truthy strings with single spaces', () => {
    expect(cn('a', 'b', 'c')).toBe('a b c')
  })

  it('drops falsy values (false, null, undefined, "")', () => {
    expect(cn('a', false, null, undefined, '', 'b')).toBe('a b')
  })

  it('returns empty string when nothing truthy', () => {
    expect(cn(false, null, undefined)).toBe('')
  })

  it('handles a single argument', () => {
    expect(cn('only')).toBe('only')
  })
})
