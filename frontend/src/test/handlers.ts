import { http, HttpResponse } from 'msw'

export const handlers = [
  http.get('/api/users/me', () =>
    HttpResponse.json({ id: '00000000-0000-0000-0000-000000000001', email: 'test@test.com' })
  ),
  http.get('/api/status', () =>
    HttpResponse.json({ budget_exhausted: false, resumes_at: null })
  ),
  http.get('/api/applications', () => HttpResponse.json([])),
  http.get('/api/profile', () => HttpResponse.json(null)),
]
