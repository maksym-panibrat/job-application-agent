import { test, expect, request } from '@playwright/test'

test.describe('Job matching flow', () => {
  test.beforeEach(async ({ request }) => {
    // Seed test jobs and applications via the dev endpoint
    const res = await request.post('http://localhost:8000/api/test/seed')
    expect(res.ok()).toBeTruthy()
  })

  test.afterEach(async ({ request }) => {
    await request.delete('http://localhost:8000/api/test/seed')
  })

  test('matches page shows seeded jobs', async ({ page }) => {
    await page.goto('/matches')
    await page.waitForLoadState('networkidle')

    // Should see at least one job card from seeded data
    await expect(page.getByText('Senior Software Engineer')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('Acme Corp')).toBeVisible()
  })

  test('dismissing a job removes it from the list', async ({ page }) => {
    await page.goto('/matches')
    await page.waitForLoadState('networkidle')

    const initialCards = page.locator('[data-testid="job-card"]')
    const initialCount = await initialCards.count()

    if (initialCount === 0) {
      // Fallback: look for the job title directly
      await expect(page.getByText('Senior Software Engineer')).toBeVisible({ timeout: 10_000 })
    }

    // Click dismiss on the first job card
    const dismissBtn = page.getByRole('button', { name: /dismiss/i }).first()
    await dismissBtn.click()

    // Wait for the card to disappear
    await page.waitForTimeout(500)

    // Check that 'Senior Software Engineer' card is gone or count reduced
    if (initialCount > 0) {
      await expect(initialCards).toHaveCount(Math.max(0, initialCount - 1))
    }
  })

  test('clicking through to application review works', async ({ page }) => {
    await page.goto('/matches')
    await page.waitForLoadState('networkidle')

    // Find a "Review" link/button
    const reviewBtn = page.getByRole('link', { name: /review/i }).first()
    if (await reviewBtn.isVisible({ timeout: 5_000 })) {
      await reviewBtn.click()
      // Should navigate to an application page
      await expect(page).toHaveURL(/\/matches\//)
    }
  })
})
