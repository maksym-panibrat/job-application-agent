/** Typed API client — thin wrappers over fetch() against /api routes. */

export interface Profile {
  id: string
  full_name: string | null
  first_name?: string | null
  last_name?: string | null
  email: string | null
  phone: string | null
  linkedin_url: string | null
  github_url: string | null
  portfolio_url: string | null
  base_resume_md: string | null
  target_roles: string[]
  target_locations: string[]
  remote_ok: boolean
  seniority: string | null
  search_keywords: string[]
  search_active: boolean
  search_expires_at: string | null
  subscription: SubscriptionInfo | null
  entitlements: EntitlementInfo
  limits: ProfileLimits
  target_companies?: { id: string; canonical_name: string }[]
  /** Write-side only; not surfaced from GET /api/profile. */
  target_company_ids?: string[]
  skills: Skill[]
  work_experiences: WorkExperience[]
}

interface SubscriptionInfo {
  tier: string
  status: 'active' | 'canceled' | 'expired' | 'refunded' | 'chargeback' | 'revoked'
  current_period_end: string
}

interface EntitlementInfo {
  paid_access: boolean
  search_auto_pause: boolean
}

interface ProfileLimits {
  followed_companies: number
}

interface Skill {
  id: string
  name: string
  category: string | null
  proficiency: string | null
  years: number | null
}

interface WorkExperience {
  id: string
  company: string
  title: string
  start_date: string
  end_date: string | null
  description_md: string | null
  technologies: string[]
}

export interface Job {
  id: string
  title: string
  company_name: string
  location: string | null
  workplace_type: string | null
  salary: string | null
  contract_type: string | null
  description?: string | null
  apply_url: string
  posted_at: string | null
}

export type ApplicationStatus = 'pending_review' | 'auto_rejected' | 'dismissed' | 'applied'
export type GenerationStatus = 'none' | 'pending' | 'generating' | 'ready' | 'failed'
export type DocumentType = 'tailored_resume' | 'cover_letter' | 'custom_answers'

export interface Application {
  id: string
  status: ApplicationStatus
  generation_status: GenerationStatus
  match_score: number | null
  match_summary: string | null
  match_rationale: string | null
  match_strengths: string[]
  match_gaps: string[]
  created_at: string
  applied_at: string | null
  job: Job | null
}

export interface ApplicationDetail extends Application {
  generation_attempts: number
  documents: Document[]
}

export interface Document {
  id: string
  doc_type: DocumentType
  content_md: string
  structured_content?: Record<string, string> | null
  has_edits: boolean
  generation_model: string | null
  created_at: string
}

export interface ApplicationSummary {
  pending_review: number
  auto_rejected: number
  dismissed: number
  applied: number
}

export interface AppStatus {
  budget_exhausted: boolean
  resumes_at: string | null
}

export interface SyncStatus {
  state: 'idle' | 'syncing' | 'matching'
  slugs_total: number
  slugs_pending: number
  matches_pending: number
  batch_matches_pending: number
  last_sync_requested_at: string | null
  last_sync_completed_at: string | null
  last_sync_summary: { matched_now?: number } | null
  invalid_slugs: string[]
}

export type FeedbackCategory = 'feature_request' | 'bug' | 'other'

export interface FeedbackDiagnostics {
  reported_at_client?: string
  path?: string
  page_title?: string
  user_agent?: string
  viewport?: { width: number; height: number }
  timezone?: string
  route_context?: Record<string, string>
}

export interface FeedbackRequest {
  category: FeedbackCategory
  message: string
  diagnostics: FeedbackDiagnostics
}

export interface FeedbackResponse {
  id: string
  created: boolean
  notification_status: 'pending' | 'not_configured' | 'sent' | 'failed'
}

function clearAuthOnUnauthorized(status: number) {
  if (status !== 401) return
  sessionStorage.removeItem('access_token')
  window.dispatchEvent(new CustomEvent('auth:token-expired'))
}

export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function parseApiError(response: Response): Promise<ApiError> {
  const text = await response.text()
  let detail = text
  try {
    const parsed = JSON.parse(text)
    if (parsed && typeof parsed.detail === 'string') detail = parsed.detail
  } catch {
    // body was not JSON; use raw text
  }
  return new ApiError(response.status, detail)
}

function authHeaders(contentType = 'application/json'): Record<string, string> {
  const token = sessionStorage.getItem('access_token')
  const headers: Record<string, string> = {}
  if (contentType) headers['Content-Type'] = contentType
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  })
  clearAuthOnUnauthorized(res.status)
  if (!res.ok) {
    throw await parseApiError(res)
  }
  return res.json()
}

export const api = {
  // Profile
  getProfile: () => apiFetch<Profile>('/api/profile'),
  updateProfile: (data: Partial<Profile>) =>
    apiFetch<{ id: string; updated: boolean }>('/api/profile', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  uploadResume: async (file: File): Promise<{ id: string; base_resume_md: string | null; extraction_status: string; message: string }> => {
    const headers = authHeaders('')
    const form = new FormData()
    form.append('file', file)
    const r = await fetch('/api/profile/upload', { method: 'POST', body: form, headers })
    clearAuthOnUnauthorized(r.status)
    if (!r.ok) {
      throw await parseApiError(r)
    }
    return r.json()
  },
  toggleSearch: (active: boolean) =>
    apiFetch<{ search_active: boolean; search_expires_at: string | null }>(
      '/api/profile/search',
      { method: 'PATCH', body: JSON.stringify({ search_active: active }) }
    ),

  // Companies
  resolveCompany: async (
    name: string,
  ): Promise<{ id: string; canonical_name: string; providers: string[] }> => {
    const headers = authHeaders()
    const resp = await fetch('/api/companies/resolve', {
      method: 'POST',
      headers,
      body: JSON.stringify({ name }),
    })
    clearAuthOnUnauthorized(resp.status)
    if (resp.status === 404) {
      throw new Error("Couldn't find that company on any of our supported boards.")
    }
    if (resp.status === 503) {
      throw new Error("Couldn't reach our boards right now, try again.")
    }
    if (!resp.ok) {
      throw await parseApiError(resp)
    }
    const body = await resp.json()
    return body.company
  },
  getCompanyCatalog: () =>
    apiFetch<{ id: string; canonical_name: string }[]>('/api/companies/catalog'),

  // Jobs
  triggerSync: () =>
    apiFetch<{
      status: string
      queued_slugs: string[]
      matched_now: number
    }>('/api/jobs/sync', { method: 'POST' }),

  // Sync status
  getSyncStatus: () =>
    apiFetch<SyncStatus>('/api/sync/status'),

  // Feedback
  submitFeedback: (data: FeedbackRequest) =>
    apiFetch<FeedbackResponse>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Applications
  listApplications: (params?: { status?: ApplicationStatus; min_score?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.min_score != null) q.set('min_score', String(params.min_score))
    if (params?.limit) q.set('limit', String(params.limit))
    return apiFetch<Application[]>(`/api/applications?${q}`)
  },
  getApplicationSummary: () => apiFetch<ApplicationSummary>('/api/applications/summary'),
  getApplication: (id: string) => apiFetch<ApplicationDetail>(`/api/applications/${id}`),
  reviewApplication: (id: string, status: 'dismissed' | 'applied' | 'pending_review') =>
    apiFetch<{ id: string; status: string }>(`/api/applications/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  updateDocument: (appId: string, docId: string, data: { user_edited_md?: string; structured_content?: Record<string, string> }) =>
    apiFetch<{ id: string; saved: boolean }>(
      `/api/applications/${appId}/documents/${docId}`,
      { method: 'PATCH', body: JSON.stringify(data) }
    ),
  generateCoverLetter: (id: string) =>
    apiFetch<{
      status: 'pending'
      job_id: number | null
    }>(`/api/applications/${id}/cover-letter`, { method: 'POST' }),
  markApplied: (id: string) =>
    apiFetch<{ id: string; status: string; applied_at: string | null }>(
      `/api/applications/${id}/mark-applied`,
      { method: 'POST' }
    ),
  /** URL of the document's PDF endpoint. NOT directly usable as an `<a href>`
   *  in production — the endpoint requires a JWT in the Authorization header,
   *  which browser navigation does not send. Use `downloadPdfBlob()` and the
   *  blob-download dance instead. Kept for callers that just need the URL. */
  downloadPdf: (docId: string) => `/api/documents/${docId}/pdf`,
  /** Fetch the PDF as a blob using the auth header from sessionStorage.
   *  Throws on non-2xx (so the caller can surface an error toast). */
  downloadPdfBlob: async (docId: string): Promise<Blob> => {
    const headers = authHeaders('')
    const res = await fetch(`/api/documents/${docId}/pdf`, { headers })
    clearAuthOnUnauthorized(res.status)
    if (!res.ok) {
      throw await parseApiError(res)
    }
    return res.blob()
  },

  // Status & auth
  getStatus: () => apiFetch<AppStatus>('/api/status'),
  getMe: () => apiFetch<{ id: string; email: string }>('/api/users/me'),

  // Chat
  sendMessage: (
    message: string,
    onChunk: (text: string) => void,
    onError?: (err: Error) => void,
    onMeta?: (meta: Record<string, unknown>) => void,
  ): Promise<void> => {
    const headers = authHeaders()
    return fetch('/api/chat/messages', {
      method: 'POST',
      headers,
      body: JSON.stringify({ message }),
    }).then(async (res) => {
      clearAuthOnUnauthorized(res.status)
      if (!res.ok) {
        const err = await parseApiError(res)
        if (onError) { onError(err); return }
        throw err
      }
      if (!res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      const handleEvent = (rawEvent: string): boolean => {
        let eventType = 'message'
        const dataLines: string[] = []
        for (const line of rawEvent.split('\n')) {
          if (line.startsWith('event: ')) eventType = line.slice(7).trim()
          if (line.startsWith('data: ')) dataLines.push(line.slice(6))
        }
        if (!dataLines.length) return false
        const data = dataLines.join('\n')
        if (data === '[DONE]') return true
        try {
          const parsed = JSON.parse(data)
          if (eventType === 'meta' && onMeta) {
            onMeta(parsed)
          } else if (eventType === 'error') {
            const err = new Error(parsed.detail || parsed.error || 'stream error')
            if (onError) { onError(err); return true }
            throw err
          } else if (parsed.content) {
            onChunk(parsed.content)
          }
        } catch (err) {
          const error = err instanceof Error ? err : new Error(`stream parse error: ${data}`)
          if (onError) { onError(error); return true }
          throw error
        }
        return false
      }
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split(/\r?\n\r?\n/)
        buffer = events.pop() ?? ''
        for (const event of events) {
          if (handleEvent(event)) return
        }
      }
      buffer += decoder.decode()
      if (buffer.trim() && handleEvent(buffer)) return
    })
  },
}
