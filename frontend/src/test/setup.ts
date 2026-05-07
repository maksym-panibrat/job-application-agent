import '@testing-library/jest-dom'
import { server } from './server'

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
