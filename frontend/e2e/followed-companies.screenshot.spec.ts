/**
 * Screenshot capture for the new FollowedCompaniesSection.
 *
 * Not part of the regression suite — purely PR artifact production. Run with:
 *   npx playwright test e2e/followed-companies.screenshot.spec.ts --project=chromium
 *
 * Outputs to tmp/screenshots/ at the worktree root.
 */

import { test, expect } from '@playwright/test'
import { loginAsTestUser } from './helpers'

const SHOT_DIR = '../tmp/screenshots'

test.describe('FollowedCompaniesSection screenshots', () => {
  test.beforeEach(async ({ page }) => {
    await loginAsTestUser(page)
  })

  test('empty state', async ({ page }) => {
    await page.route('**/api/profile', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: '00000000-0000-0000-0000-000000000001',
            full_name: 'Screenshot User',
            email: 'shot@example.com',
            phone: null,
            linkedin_url: null,
            github_url: null,
            portfolio_url: null,
            base_resume_md: null,
            target_roles: ['Senior Frontend Engineer'],
            target_locations: ['Remote — US'],
            remote_ok: true,
            seniority: 'senior',
            search_keywords: ['typescript', 'react'],
            search_active: true,
            search_expires_at: null,
            subscription: { plan: 'free', status: 'active', paid_active: false },
            limits: { followed_companies: 5 },
            target_companies: [],
            skills: [],
            work_experiences: [],
          }),
        })
      }
      return route.fallback()
    })

    await page.goto('/settings')
    const section = page.getByRole('heading', { name: /Followed companies/i }).locator('..')
    await expect(section).toBeVisible()
    await section.screenshot({ path: `${SHOT_DIR}/followed-companies-empty.png` })
  })

  test('with chips', async ({ page }) => {
    await page.route('**/api/profile', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: '00000000-0000-0000-0000-000000000001',
            full_name: 'Screenshot User',
            email: 'shot@example.com',
            phone: null,
            linkedin_url: null,
            github_url: null,
            portfolio_url: null,
            base_resume_md: null,
            target_roles: ['Senior Frontend Engineer'],
            target_locations: ['Remote — US'],
            remote_ok: true,
            seniority: 'senior',
            search_keywords: ['typescript', 'react'],
            search_active: true,
            search_expires_at: null,
            subscription: { plan: 'free', status: 'active', paid_active: false },
            limits: { followed_companies: 5 },
            target_companies: [
              { id: 'a', canonical_name: 'Stripe' },
              { id: 'b', canonical_name: 'Linear' },
              { id: 'c', canonical_name: 'Anthropic' },
              { id: 'd', canonical_name: 'Vercel' },
            ],
            skills: [],
            work_experiences: [],
          }),
        })
      }
      return route.fallback()
    })

    await page.goto('/settings')
    const section = page.getByRole('heading', { name: /Followed companies/i }).locator('..')
    await expect(section).toBeVisible()
    await expect(section.getByText('Stripe')).toBeVisible()
    await section.screenshot({ path: `${SHOT_DIR}/followed-companies-with-chips.png` })
  })

  test('inline 404 error', async ({ page }) => {
    await page.route('**/api/profile', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
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
          }),
        })
      }
      return route.fallback()
    })
    await page.route('**/api/companies/resolve', (route) =>
      route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'company not found on any supported board' }) })
    )

    await page.goto('/settings')
    const section = page.getByRole('heading', { name: /Followed companies/i }).locator('..')
    await expect(section).toBeVisible()

    const input = section.getByPlaceholder(/Add a company/i)
    await input.fill('totally-fake-co')
    await input.press('Enter')

    await expect(section.getByRole('alert')).toContainText(/Couldn't find that company/i)
    await section.screenshot({ path: `${SHOT_DIR}/followed-companies-404-error.png` })
  })
})
