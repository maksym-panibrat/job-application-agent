/** In-app event tracking — see spec section 7.
 *
 *  Public API: `track(name, properties?)`. Calls are buffered and flushed
 *  every 5s and on `pagehide`. The server requires an authenticated profile,
 *  so calls made before a JWT exists are skipped client-side. Failures are
 *  swallowed so analytics never break the app. */

interface EventIn {
  name: string
  properties?: Record<string, unknown>
  path?: string
}

const SESSION_KEY = 'ja_session_id'
const FLUSH_MS = 5_000
const MAX_BATCH = 50
const PAGEHIDE_LISTENER_KEY = '__jobApplicationAgentTrackPagehideListener'

const queue: EventIn[] = []
let flushTimer: number | null = null

declare global {
  interface Window {
    [PAGEHIDE_LISTENER_KEY]?: () => void
  }
}

function getSessionId(): string {
  let s = sessionStorage.getItem(SESSION_KEY)
  if (!s) {
    s = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36)
    sessionStorage.setItem(SESSION_KEY, s)
  }
  return s
}

async function flush(): Promise<void> {
  flushTimer = null
  if (queue.length === 0) return
  const token = sessionStorage.getItem('access_token')
  if (!token) {
    queue.length = 0
    return
  }
  const batch = queue.splice(0, MAX_BATCH)
  if (queue.length > 0 && flushTimer == null) {
    flushTimer = window.setTimeout(flush, FLUSH_MS)
  }
  try {
    await fetch('/api/events', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ session_id: getSessionId(), events: batch }),
      keepalive: true,
    })
  } catch {
    // Swallow — analytics MUST NOT break the app.
  }
}

export function track(name: string, properties?: Record<string, unknown>): void {
  if (!sessionStorage.getItem('access_token')) return
  queue.push({
    name,
    properties,
    path: typeof window !== 'undefined' ? window.location.pathname + window.location.search : undefined,
  })
  if (flushTimer == null && typeof window !== 'undefined') {
    flushTimer = window.setTimeout(flush, FLUSH_MS)
  }
}

function onPagehide(): void { void flush() }

if (typeof window !== 'undefined') {
  const previous = window[PAGEHIDE_LISTENER_KEY]
  if (previous) {
    window.removeEventListener('pagehide', previous)
  }
  window.addEventListener('pagehide', onPagehide)
  window[PAGEHIDE_LISTENER_KEY] = onPagehide
}
