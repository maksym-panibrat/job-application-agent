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
  subscription: SubscriptionInfo
  limits: ProfileLimits
  target_companies?: { id: string; canonical_name: string }[]
  /** Write-side only; not surfaced from GET /api/profile. */
  target_company_ids?: string[]
  skills: Skill[]
  work_experiences: WorkExperience[]
}

export interface SubscriptionInfo {
  plan: string
  status: string
  paid_active: boolean
}

export interface ProfileLimits {
  followed_companies: number
}

export interface Skill {
  id: string
  name: string
  category: string | null
  proficiency: string | null
  years: number | null
}

export interface WorkExperience {
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

export interface Application {
  id: string
  status: string
  generation_status: string
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
  doc_type: string
  content_md: string
  structured_content?: Record<string, string> | null
  has_edits: boolean
  generation_model: string | null
  created_at: string
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

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem('access_token')
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(path, {
    headers: { ...headers, ...init?.headers },
    ...init,
  })
  clearAuthOnUnauthorized(res.status)
  if (!res.ok) {
    const text = await res.text()
    let detail: string | null = null
    try {
      const parsed = JSON.parse(text)
      if (parsed && typeof parsed.detail === 'string') {
        detail = parsed.detail
      }
    } catch {
      // body wasn't JSON; fall through to raw text
    }
    throw new Error(detail ? `${res.status}: ${detail}` : `${res.status}: ${text}`)
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
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const form = new FormData()
    form.append('file', file)
    const r = await fetch('/api/profile/upload', { method: 'POST', body: form, headers })
    clearAuthOnUnauthorized(r.status)
    if (!r.ok) {
      const text = await r.text()
      throw new Error(`${r.status}: ${text}`)
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
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
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
      const text = await resp.text()
      let detail: string | null = null
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed.detail === 'string') detail = parsed.detail
      } catch {
        // body wasn't JSON
      }
      throw new Error(detail ?? `${resp.status}: ${text}`)
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

  // Sync status (Task 19)
  getSyncStatus: () =>
    apiFetch<SyncStatus>('/api/sync/status'),

  // Feedback
  submitFeedback: (data: FeedbackRequest) =>
    apiFetch<FeedbackResponse>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Applications
  listApplications: (params?: { status?: string; min_score?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.min_score != null) q.set('min_score', String(params.min_score))
    if (params?.limit) q.set('limit', String(params.limit))
    return apiFetch<Application[]>(`/api/applications?${q}`)
  },
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
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res = await fetch(`/api/documents/${docId}/pdf`, { headers })
    clearAuthOnUnauthorized(res.status)
    if (!res.ok) {
      const text = await res.text()
      let detail: string | null = null
      try {
        const parsed = JSON.parse(text)
        if (parsed && typeof parsed.detail === 'string') detail = parsed.detail
      } catch {
        // body wasn't JSON
      }
      throw new Error(detail ?? `${res.status}: ${text}`)
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
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    return fetch('/api/chat/messages', {
      method: 'POST',
      headers,
      body: JSON.stringify({ message }),
    }).then(async (res) => {
      clearAuthOnUnauthorized(res.status)
      if (!res.ok) {
        const text = await res.text()
        const err = new Error(`${res.status}: ${text}`)
        if (onError) { onError(err); return }
        throw err
      }
      if (!res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let pendingEvent: string | null = null
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value)
        for (const line of text.split('\n')) {
          if (line.startsWith('event: ')) {
            pendingEvent = line.slice(7).trim()
            continue
          }
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            const eventType = pendingEvent
            pendingEvent = null
            if (data === '[DONE]') return
            try {
              const parsed = JSON.parse(data)
              if (eventType === 'meta' && onMeta) {
                onMeta(parsed)
              } else if (parsed.content) {
                onChunk(parsed.content)
              }
            } catch {
              const err = new Error(`stream parse error: ${data}`)
              if (onError) { onError(err); return }
            }
          }
        }
      }
    })
  },
}
