import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Reset module-level state between tests so the queue/sessionId don't leak.
function resetModule() {
  vi.resetModules()
  sessionStorage.clear()
}

describe('track()', () => {
  let originalFetch: typeof fetch
  // Hold a reference to the current test's _reset so afterEach can drain the
  // stale queue and remove its pagehide listener before the next module load.
  let currentReset: (() => void) | null = null

  beforeEach(() => {
    currentReset = null
    resetModule()
    vi.useFakeTimers()
    originalFetch = globalThis.fetch
  })
  afterEach(async () => {
    // Drain any buffered events from this test's module instance and remove its
    // pagehide listener so it doesn't bleed into subsequent tests.
    if (currentReset) currentReset()
    vi.useRealTimers()
    globalThis.fetch = originalFetch
  })

  it('does not fetch synchronously — flushes after the timer', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track, _reset } = await import('./track')
    currentReset = _reset
    track('feed.viewed', { status_filter: 'pending' })
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('flushes the queue after 5 seconds', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track, _reset } = await import('./track')
    currentReset = _reset
    track('feed.viewed')
    track('match.card_opened', { application_id: 'a1' })
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const init = fetchSpy.mock.calls[0][1] as RequestInit
    const body = JSON.parse(init.body as string)
    expect(body.events).toHaveLength(2)
    expect(body.events[0].name).toBe('feed.viewed')
    expect(body.events[1].name).toBe('match.card_opened')
    expect(typeof body.session_id).toBe('string')
  })

  it('flushes on pagehide', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track, _reset } = await import('./track')
    currentReset = _reset
    track('app.error_boundary_hit')
    window.dispatchEvent(new Event('pagehide'))
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('caps each batch at 50 events', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track, _reset } = await import('./track')
    currentReset = _reset
    for (let i = 0; i < 60; i++) track(`evt_${i}`)
    await vi.advanceTimersByTimeAsync(5_000)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string)
    expect(body.events).toHaveLength(50)
  })

  it('swallows fetch errors silently', async () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error('network'))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track, _reset } = await import('./track')
    currentReset = _reset
    track('feed.viewed')
    await vi.advanceTimersByTimeAsync(5_000)
  })

  it('persists session_id across calls', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    globalThis.fetch = fetchSpy as typeof fetch
    const { track, _reset } = await import('./track')
    currentReset = _reset
    track('a')
    await vi.advanceTimersByTimeAsync(5_000)
    track('b')
    await vi.advanceTimersByTimeAsync(5_000)
    const sid1 = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string).session_id
    const sid2 = JSON.parse((fetchSpy.mock.calls[1][1] as RequestInit).body as string).session_id
    expect(sid1).toBe(sid2)
  })
})
