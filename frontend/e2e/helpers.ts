import type { Page } from '@playwright/test'

/**
 * Authenticate as the deterministic e2e test user.
 *
 * Calls POST /api/test/login (mounted only in dev/test) to get a JWT,
 * then injects it into sessionStorage via addInitScript so the React
 * app's AuthContext picks it up on every page load in this context.
 *
 * Call once in `test.beforeEach` before any page.goto for protected routes.
 */
export async function loginAsTestUser(
  page: Page
): Promise<{ token: string; userId: string; email: string }> {
  const res = await page.request.post('/api/test/login')
  if (!res.ok()) {
    throw new Error(
      `loginAsTestUser failed: POST /api/test/login → ${res.status()} ${await res.text()}`
    )
  }
  const body = (await res.json()) as { access_token: string; user_id: string; email: string }
  const token = body.access_token

  // Inject the token into sessionStorage on every navigation in this page's context.
  // addInitScript runs BEFORE any page script, so AuthContext sees the token on first
  // useEffect.
  await page.addInitScript(
    (t: string) => {
      sessionStorage.setItem('access_token', t)
    },
    token,
  )

  return { token, userId: body.user_id, email: body.email }
}
