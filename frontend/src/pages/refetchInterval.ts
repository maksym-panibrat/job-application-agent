/**
 * Dynamic refetch interval for the matches dashboard.
 *
 * Background scoring after a sync click runs ~30s for a 20-job batch
 * (1.5s throttle + LLM latency per job). The default 10s refetch + 3s
 * post-sync invalidation lost to that race — the user saw "Sync complete:
 * N new jobs" but matches stayed empty until they reloaded.
 *
 * Returns 5s during the "post-sync window" so freshly scored applications
 * surface promptly; returns 10s otherwise to keep the polling rate sane
 * during idle dashboard sessions. (Issue #52)
 */
export const POST_SYNC_INTERVAL_MS = 5000
export const IDLE_INTERVAL_MS = 10000
export const POST_SYNC_WINDOW_MS = 60_000

export function computeRefetchInterval(
  postSyncUntilMs: number | null,
  now: number = Date.now(),
): number {
  if (postSyncUntilMs !== null && now < postSyncUntilMs) {
    return POST_SYNC_INTERVAL_MS
  }
  return IDLE_INTERVAL_MS
}
