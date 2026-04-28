import {
  computeRefetchInterval,
  IDLE_INTERVAL_MS,
  POST_SYNC_INTERVAL_MS,
  POST_SYNC_WINDOW_MS,
} from './refetchInterval'

describe('computeRefetchInterval', () => {
  it('returns idle interval when no recent sync', () => {
    expect(computeRefetchInterval(null, 1_000_000)).toBe(IDLE_INTERVAL_MS)
  })

  it('returns aggressive interval inside the post-sync window', () => {
    const now = 1_000_000
    const until = now + POST_SYNC_WINDOW_MS - 1
    expect(computeRefetchInterval(until, now)).toBe(POST_SYNC_INTERVAL_MS)
  })

  it('returns idle interval after the post-sync window expires', () => {
    const now = 1_000_000
    const until = now - 1 // window already over
    expect(computeRefetchInterval(until, now)).toBe(IDLE_INTERVAL_MS)
  })

  it('idle and post-sync intervals differ', () => {
    expect(POST_SYNC_INTERVAL_MS).toBeLessThan(IDLE_INTERVAL_MS)
  })
})
