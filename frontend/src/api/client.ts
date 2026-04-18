/** Typed API client — thin wrappers over fetch() against /api routes. */

export interface Profile {
  id: string
  full_name: string | null
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
  ats_type: string | null
  supports_api_apply: boolean
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
  has_edits: boolean
  generation_model: string | null
  created_at: string
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
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
  uploadResume: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch('/api/profile/upload', { method: 'POST', body: form }).then((r) => r.json())
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
  reviewApplication: (id: string, status: 'approved' | 'dismissed' | 'applied') =>
    apiFetch<{ id: string; status: string }>(`/api/applications/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  updateDocument: (appId: string, docId: string, content: string) =>
    apiFetch<{ id: string; saved: boolean }>(
      `/api/applications/${appId}/documents/${docId}`,
      { method: 'PATCH', body: JSON.stringify({ user_edited_md: content }) }
    ),
  regenerate: (id: string) =>
    apiFetch<{ id: string; generation_status: string }>(`/api/applications/${id}/regenerate`, {
      method: 'POST',
    }),
  submitApplication: (id: string) =>
    apiFetch<{ success?: boolean; method: string; apply_url?: string; error?: string }>(
      `/api/applications/${id}/submit`,
      { method: 'POST' }
    ),
  downloadPdf: (docId: string) => `/api/documents/${docId}/pdf`,

  // Chat
  sendMessage: (message: string, onChunk: (text: string) => void): Promise<void> => {
    return fetch('/api/chat/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    }).then(async (res) => {
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
              // ignore parse errors
            }
          }
        }
      }
    })
  },
}
