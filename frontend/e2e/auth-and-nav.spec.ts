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
