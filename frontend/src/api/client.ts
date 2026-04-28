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
  target_company_slugs?: { greenhouse?: string[]; lever?: string[]; ashby?: string[] }
  skills: Skill[]
  work_experiences: WorkExperience[]
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
  description_md: string | null
  apply_url: string
  posted_at: string | null
}

export interface Application {
  id: string
  status: string
  generation_status: string
  match_score: number | null
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

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem('access_token')
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(path, {
    headers: { ...headers, ...init?.headers },
    ...init,
  })
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
    throw new Error(detail ?? `${res.status}: ${text}`)
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

  // Jobs
  triggerSync: () =>
    apiFetch<{ status: string; new_jobs: number; updated_jobs: number; stale_jobs: number }>(
      '/api/jobs/sync',
      { method: 'POST' }
    ),

  // Applications
  listApplications: (params?: { status?: string; min_score?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.min_score != null) q.set('min_score', String(params.min_score))
    if (params?.limit) q.set('limit', String(params.limit))
    return apiFetch<Application[]>(`/api/applications?${q}`)
  },
  getApplication: (id: string) => apiFetch<ApplicationDetail>(`/api/applications/${id}`),
  reviewApplication: (id: string, status: 'dismissed' | 'applied') =>
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
      id: string
      doc_type: string
      content_md: string
      generation_model: string | null
      created_at: string
    }>(`/api/applications/${id}/cover-letter`, { method: 'POST' }),
  markApplied: (id: string) =>
    apiFetch<{ id: string; status: string; applied_at: string | null }>(
      `/api/applications/${id}/mark-applied`,
      { method: 'POST' }
    ),
  downloadPdf: (docId: string) => `/api/documents/${docId}/pdf`,

  // Status & auth
  getStatus: () => apiFetch<AppStatus>('/api/status'),
  getMe: () => apiFetch<{ id: string; email: string }>('/api/users/me'),

  // Chat
  sendMessage: (message: string, onChunk: (text: string) => void, onError?: (err: Error) => void): Promise<void> => {
    const token = sessionStorage.getItem('access_token')
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    return fetch('/api/chat/messages', {
      method: 'POST',
      headers,
      body: JSON.stringify({ message }),
    }).then(async (res) => {
      if (!res.ok) {
        const text = await res.text()
        const err = new Error(`${res.status}: ${text}`)
        if (onError) { onError(err); return }
        throw err
      }
      if (!res.body) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const text = decoder.decode(value)
        for (const line of text.split('\n')) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6)
            if (data === '[DONE]') return
            try {
              const parsed = JSON.parse(data)
              if (parsed.content) onChunk(parsed.content)
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
