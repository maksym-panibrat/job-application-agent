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

test.describe('Landing page — Sign in with Google', () => {
  test('renders the Landing page with the Google sign-in button', async ({ page }) => {
    // Prevent AuthContext → /api/users/me from auto-resolving as the dev user
    // bouncing us past Landing if the app ever adds such a redirect.
    await page.route('**/api/users/me', (route) => route.fulfill({ status: 401, body: '{}' }))

    await page.goto('/')

    await expect(page.getByRole('heading', { name: /Job Application Agent/i })).toBeVisible()
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
  // These run in AUTH_ENABLED=false mode (per playwright.config.ts), so the
  // backend auto-provisions SINGLE_USER_ID and the nav bar renders. The goal
  // isn't to test auth — it's to assert that each top-level route renders
  // without a 500 or unhandled error boundary.

  test('Matches page loads and lists 0+ applications without server error', async ({ page, request }) => {
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

    await page.goto('/matches')
    await page.waitForLoadState('networkidle')

    await expect(page.getByRole('heading', { name: /Job Matches/i })).toBeVisible()
    await expect(page.getByRole('button', { name: /Sync jobs/i })).toBeVisible()
    expect(failures, `5xx responses hit: ${failures.join(', ')}`).toEqual([])
  })

  test('nav bar routes between Matches / History / Profile', async ({ page }) => {
    await page.route('**/api/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ budget_exhausted: false, resumes_at: null }),
      })
    )

    await page.goto('/matches')
    await page.waitForLoadState('networkidle')
    await expect(page.getByRole('heading', { name: /Job Matches/i })).toBeVisible()

    await page.getByRole('link', { name: 'History' }).click()
    await expect(page).toHaveURL(/\/applied$/)
    await expect(page.getByRole('heading', { name: /History/i })).toBeVisible()

    await page.getByRole('link', { name: 'Profile' }).click()
    await expect(page).toHaveURL(/\/profile$/)
    await expect(page.getByRole('heading', { name: /Profile Setup/i })).toBeVisible()

    await page.getByRole('link', { name: 'Matches' }).click()
    await expect(page).toHaveURL(/\/matches$/)
    await expect(page.getByRole('heading', { name: /Job Matches/i })).toBeVisible()
  })

  test('History page loads without crashing the app', async ({ page }) => {
    await page.route('**/api/status', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ budget_exhausted: false, resumes_at: null }),
      })
    )

    const failures: string[] = []
    page.on('response', (res) => {
      if (res.status() >= 500 && /\/api\//.test(res.url())) {
        failures.push(`${res.status()} ${res.url()}`)
      }
    })

    await page.goto('/applied')
    await page.waitForLoadState('networkidle')

    await expect(page.getByRole('heading', { name: /History/i })).toBeVisible()
    expect(failures, `5xx responses hit: ${failures.join(', ')}`).toEqual([])
  })
})
