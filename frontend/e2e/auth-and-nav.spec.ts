/**
 * E2E coverage for login + top-level navigation — the two pieces of core
 * functionality that silently broke on 2026-04-22:
 *
 *   - Landing.tsx's Sign-in-with-Google button used <a href> against an
 *     endpoint that returns JSON, so clicking it dead-ended the user on a
 *     raw JSON body.
 *   - GET /api/applications 500-ed (schema drift), taking the Matches page
 *     down with it.
 *
 * These tests exercise the real frontend against the real backend and would
 * have caught either regression. The webServer config runs with
 * AUTH_ENABLED=false, so the single-user bypass auto-authenticates requests;
 * we intercept /auth/google/authorize to simulate the OAuth flow without
 * needing real Google credentials.
 */

import { test, expect } from '@playwright/test'
import { loginAsTestUser } from './helpers'

test.describe('Landing page — Sign in with Google', () => {
  test('renders the Landing page with the Google sign-in button', async ({ page }) => {
    // Prevent AuthContext → /api/users/me from auto-resolving as the dev user
    // bouncing us past Landing if the app ever adds such a redirect.
    await page.route('**/api/users/me', (route) => route.fulfill({ status: 401, body: '{}' }))

    await page.goto('/')

    await expect(page.getByRole('heading', { name: /Job Search/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /Sign in with Google/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /Sign in with Google/i })).toBeEnabled()
  })

  test('clicking Sign in navigates to the authorization_url from the authorize endpoint', async ({ page }) => {
    // Dummy authorization_url — we only need to confirm the frontend follows
    // it; going to real accounts.google.com would break under network egress.
    const fakeAuthUrl = 'https://example.test/oauth-stub?state=abc'

    await page.route('**/auth/google/authorize', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ authorization_url: fakeAuthUrl }),
      })
    )
    // Stub the destination so Playwright doesn't hang on an unreachable host.
    await page.route(fakeAuthUrl, (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>stub</body></html>' })
    )
    await page.route('**/api/users/me', (route) => route.fulfill({ status: 401, body: '{}' }))

    await page.goto('/')
    await page.getByRole('button', { name: /Sign in with Google/i }).click()

    await page.waitForURL(fakeAuthUrl)
    expect(page.url()).toBe(fakeAuthUrl)
  })

  test('shows a user-visible error when the authorize endpoint fails', async ({ page }) => {
    await page.route('**/auth/google/authorize', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
    )
    await page.route('**/api/users/me', (route) => route.fulfill({ status: 401, body: '{}' }))

    await page.goto('/')
    await page.getByRole('button', { name: /Sign in with Google/i }).click()

    await expect(page.getByRole('alert')).toContainText(/Sign-in is unavailable/i)
    // Button must not leave the user in a permanently-disabled "Redirecting…"
    // state so they can retry.
    await expect(page.getByRole('button', { name: /Sign in with Google/i })).toBeEnabled()
  })
})

test.describe('Top-level navigation', () => {
  // These tests navigate to protected routes (/matches, /settings).
  // We authenticate as the deterministic e2e test user via loginAsTestUser so
  // the app renders the authenticated nav bar. The goal isn't to test auth —
  // it's to assert that each top-level route renders without a 500 or
  // unhandled error boundary.

  test.beforeEach(async ({ page }) => {
    await loginAsTestUser(page)
  })

  test('Feed page loads and lists 0+ applications without server error', async ({ page, request }) => {
    // Swallow the budget-status endpoint so the amber banner doesn't shift assertions.
    await page.route('**/api/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ budget_exhausted: false, resumes_at: null }),
      })
    )

    // Capture any 5xx so the test fails with a useful message instead of
    // just a flaky content-not-visible timeout.
    const failures: string[] = []
    page.on('response', (res) => {
      if (res.status() >= 500 && /\/api\//.test(res.url())) {
        failures.push(`${res.status()} ${res.url()}`)
      }
    })

    // Plan B: / is the new Feed (auth-gated). /matches is still aliased.
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // The new Feed has status chips ("Pending"). Sync now lives in the AppShell
    // header (relocated from the feed sticky region in #101), so it's available
    // on every page. Use first() because the mobile hamburger menu can also
    // contain a "Sync now" entry once opened.
    await expect(page.getByRole('button', { name: /pending/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /sync now/i }).first()).toBeVisible()
    expect(failures, `5xx responses hit: ${failures.join(', ')}`).toEqual([])
  })

  test('AppShell header surfaces Settings link, Chat button, Sign out', async ({ page }) => {
    await page.route('**/api/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ budget_exhausted: false, resumes_at: null }),
      })
    )

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    // The new Feed shows status chips, not a "Job Matches" heading.
    await expect(page.getByRole('button', { name: /pending/i })).toBeVisible()

    // Brand link returns to root.
    await expect(page.getByRole('link', { name: 'Job Search' })).toHaveAttribute('href', '/')

    // Settings → renders the structured Settings page (Plan C).
    await page.getByRole('link', { name: 'Settings' }).click()
    await expect(page).toHaveURL(/\/settings$/)
    await expect(page.getByRole('heading', { name: /^Settings$/i })).toBeVisible()

    // Chat button opens the drawer and sets the ?chat=1 query param.
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.getByRole('button', { name: 'Chat' }).click()
    await expect(page).toHaveURL(/[?&]chat=1/)
    await expect(page.getByRole('dialog', { name: 'Chat' })).toBeVisible()

    // Sign out is reachable as a button in the header.
    await expect(page.getByRole('button', { name: /Sign out/i })).toBeVisible()
  })
})
