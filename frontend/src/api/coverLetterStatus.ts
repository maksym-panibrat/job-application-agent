import { apiFetch } from './client'

export interface CoverLetterStatus {
  status: 'pending' | 'generating' | 'ready' | 'failed'
  attempts: number
  queued_at?: string
  completed_at?: string
  error?: string
}

const POLL_INTERVAL_MS = 3000
const HARD_TIMEOUT_MS = 90_000

function abortError(): DOMException {
  return new DOMException('Polling aborted', 'AbortError')
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const id = window.setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => {
      window.clearTimeout(id)
      reject(abortError())
    }, { once: true })
  })
}

export async function pollUntilTerminal(
  applicationId: string,
  onUpdate: (status: CoverLetterStatus) => void,
  options: { signal?: AbortSignal } = {},
): Promise<CoverLetterStatus> {
  const deadline = Date.now() + HARD_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (options.signal?.aborted) throw abortError()
    const status = await apiFetch<CoverLetterStatus>(
      `/api/applications/${applicationId}/cover-letter/status`,
      { signal: options.signal },
    )
    onUpdate(status)
    if (status.status === 'ready' || status.status === 'failed') {
      return status
    }
    await sleep(POLL_INTERVAL_MS, options.signal)
  }
  throw new Error('Polling timed out')
}
