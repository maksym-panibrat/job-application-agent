/**
 * Screenshot capture for the Layer 2 typeahead dropdown.
 *
 * Not part of the regression suite — purely PR artifact production. Run with:
 *   npx playwright test e2e/typeahead-dropdown.screenshot.spec.ts --project=chromium
 *
 * Outputs to docs/superpowers/screenshots/2026-05-08-curated-company-catalog/.
 */

import { test, expect } from '@playwright/test'
import { loginAsTestUser } from './helpers'

const SHOT_DIR = '../docs/superpowers/screenshots/2026-05-08-curated-company-catalog'

const PROFILE_BASE = {
  id: '00000000-0000-0000-0000-000000000001',
  full_name: 'Screenshot User',
  email: 'shot@example.com',
  phone: null,
  linkedin_url: null,
  github_url: null,
  portfolio_url: null,
  base_resume_md: null,
  target_roles: [],
  target_locations: [],
  remote_ok: true,
  seniority: null,
  search_keywords: [],
  search_active: true,
  search_expires_at: null,
  subscription: { plan: 'free', status: 'active', paid_active: false },
  limits: { followed_companies: 5 },
  target_companies: [],
  skills: [],
  work_experiences: [],
}

const CATALOG = [
  { id: 'cat-1', canonical_name: 'Anthropic' },
  { id: 'cat-2', canonical_name: 'Asana' },
  { id: 'cat-3', canonical_name: 'Brex' },
  { id: 'cat-4', canonical_name: 'Cloudflare' },
  { id: 'cat-5', canonical_name: 'Datadog' },
  { id: 'cat-6', canonical_name: 'DoorDash' },
  { id: 'cat-7', canonical_name: 'Linear' },
  { id: 'cat-8', canonical_name: 'Notion' },
  { id: 'cat-9', canonical_name: 'OpenAI' },
  { id: 'cat-10', canonical_name: 'Stripe' },
  { id: 'cat-11', canonical_name: 'Vercel' },
]

test.describe('Layer 2 typeahead screenshots', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsTestUser(page)
    await page.route('**/api/profile', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(PROFILE_BASE),
        })
      }
      return route.fallback()
    })
    await page.route('**/api/companies/catalog', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(CATALOG),
      })
    )
  })

  test('01 dropdown open with substring matches', async ({ page }) => {
    await page.goto('/settings')
    const section = page.getByRole('heading', { name: /Followed companies/i }).locator('..')
    await expect(section).toBeVisible()

    const input = section.getByPlaceholder(/Add a company/i)
    await input.fill('an')

    await expect(section.getByRole('option', { name: 'Anthropic' })).toBeVisible()
    await page.screenshot({ fullPage: false, path: `${SHOT_DIR}/01-dropdown-open.png` })
  })

  test('02 no-match state', async ({ page }) => {
    await page.goto('/settings')
    const section = page.getByRole('heading', { name: /Followed companies/i }).locator('..')
    await expect(section).toBeVisible()

    const input = section.getByPlaceholder(/Add a company/i)
    await input.fill('zzz-no-such')

    await expect(section.getByText(/press Enter to search the boards/i)).toBeVisible()
    await page.screenshot({ fullPage: false, path: `${SHOT_DIR}/02-no-match.png` })
  })

  test('03 chip added after typeahead selection', async ({ page }) => {
    // Override the beforeEach profile mock to seed a chip directly. The
    // typeahead-click path is exercised by the unit tests; this screenshot
    // shows the resulting state once a catalog selection has been added,
    // without fighting Playwright route ordering for PATCH mocks.
    await page.unroute('**/api/profile')
    await page.route('**/api/profile', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            ...PROFILE_BASE,
            target_companies: [{ id: 'cat-7', canonical_name: 'Linear' }],
          }),
        })
      }
      return route.fallback()
    })

    await page.goto('/settings')
    const section = page.getByRole('heading', { name: /Followed companies/i }).locator('..')
    await expect(section).toBeVisible()
    await expect(section.getByText('Linear')).toBeVisible()
    await page.screenshot({ fullPage: false, path: `${SHOT_DIR}/03-chip-added.png` })
  })
})
