import { test, expect } from '@playwright/test'
import { loginAsTestUser } from './helpers'

test.describe('Application review flow', () => {
  let applicationId: string
  let authToken: string

  test.beforeEach(async ({ page }) => {
    const { token } = await loginAsTestUser(page)
    authToken = token

    const res = await page.request.post('/api/test/seed', {
      headers: { Authorization: `Bearer ${authToken}` },
    })
    expect(res.ok()).toBeTruthy()
    const body = await res.json()
    applicationId = body.applications[0]
  })

  test.afterEach(async ({ page }) => {
    await page.request.delete('/api/test/seed', {
      headers: { Authorization: `Bearer ${authToken}` },
    })
  })

  test('application review page loads with job details', async ({ page }) => {
    await page.goto(`/matches/${applicationId}`)
    await page.waitForLoadState('networkidle')

    await expect(page.getByText('Senior Software Engineer').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('Acme Corp').first()).toBeVisible()
  })

  test('generate cover letter button is shown when no doc exists', async ({ page }) => {
    await page.goto(`/matches/${applicationId}`)
    await page.waitForLoadState('networkidle')

    const generateBtn = page.getByRole('button', { name: /generate cover letter/i })
    await expect(generateBtn).toBeVisible({ timeout: 5_000 })
    expect(await generateBtn.isEnabled()).toBeTruthy()
  })

  test('mark as applied + open application controls are present', async ({ page }) => {
    await page.goto(`/matches/${applicationId}`)
    await page.waitForLoadState('networkidle')

    await expect(page.getByRole('link', { name: /open application/i })).toBeVisible({ timeout: 5_000 })
    await expect(page.getByRole('button', { name: /mark as applied/i })).toBeVisible()
  })
})
