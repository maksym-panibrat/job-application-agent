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

export async function pollUntilTerminal(
  applicationId: string,
  onUpdate: (status: CoverLetterStatus) => void,
): Promise<CoverLetterStatus> {
  const deadline = Date.now() + HARD_TIMEOUT_MS
  while (Date.now() < deadline) {
    const status = await apiFetch<CoverLetterStatus>(
      `/api/applications/${applicationId}/cover-letter/status`,
    )
    onUpdate(status)
    if (status.status === 'ready' || status.status === 'failed') {
      return status
    }
    await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS))
  }
  throw new Error('Polling timed out')
}
