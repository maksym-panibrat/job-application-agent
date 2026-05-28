import '@testing-library/jest-dom'
import React from 'react'
import { vi } from 'vitest'
import { server } from './server'

vi.mock('react-swipeable-list', () => ({
  SwipeableList: ({ children, className, style }: {
    children: React.ReactNode
    className?: string
    style?: React.CSSProperties
  }) => React.createElement('div', { className, style }, children),
  SwipeableListItem: ({ children, trailingActions }: {
    children: React.ReactNode
    trailingActions?: React.ReactNode
  }) => React.createElement('div', null, trailingActions, children),
  SwipeAction: ({ children, onClick }: {
    children: React.ReactNode
    onClick?: () => void
  }) => React.createElement('div', { onClick }, children),
  TrailingActions: ({ children }: { children: React.ReactNode }) =>
    React.createElement('div', null, children),
  Type: { IOS: 'ios' },
}))

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

window.HTMLElement.prototype.scrollIntoView = () => {}

// jsdom does not implement these — primitives need them mocked at the
// global level so component tests don't have to redo this per file.
class _MockObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}

// @ts-expect-error — assigning to the global typed as undefined-able
window.IntersectionObserver = _MockObserver
window.ResizeObserver = _MockObserver

if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as MediaQueryList)
}
