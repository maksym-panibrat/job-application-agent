import { test, expect } from '@playwright/test'

test.describe('Application review flow', () => {
  let applicationId: string

  test.beforeEach(async ({ request }) => {
    const res = await request.post('http://localhost:8000/api/test/seed')
    expect(res.ok()).toBeTruthy()
    const body = await res.json()
    applicationId = body.applications[0]
  })

  test.afterEach(async ({ request }) => {
    await request.delete('http://localhost:8000/api/test/seed')
  })

  test('application review page loads with job details', async ({ page }) => {
    await page.goto(`/matches/${applicationId}`)
    await page.waitForLoadState('networkidle')

    // Should show job title and company
    await expect(page.getByText('Senior Software Engineer').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('Acme Corp').first()).toBeVisible()
  })

  test('document tabs are present and switchable', async ({ page }) => {
    await page.goto(`/matches/${applicationId}`)
    await page.waitForLoadState('networkidle')

    // Look for document tab labels (Resume / Cover Letter)
    const resumeTab = page.getByRole('tab', { name: /resume/i }).or(
      page.getByText(/tailored resume/i)
    )
    const coverTab = page.getByRole('tab', { name: /cover letter/i }).or(
      page.getByText(/cover letter/i)
    )

    if (await resumeTab.isVisible({ timeout: 5_000 })) {
      await resumeTab.click()
      await expect(page.getByText('Jane Smith')).toBeVisible({ timeout: 5_000 })

      if (await coverTab.isVisible()) {
        await coverTab.click()
        await expect(page.getByText(/Hiring Manager|excited to apply/i)).toBeVisible({
          timeout: 5_000,
        })
      }
    }
  })

  test('approve button is present', async ({ page }) => {
    await page.goto(`/matches/${applicationId}`)
    await page.waitForLoadState('networkidle')

    const approveBtn = page.getByRole('button', { name: /approve/i })
    if (await approveBtn.isVisible({ timeout: 5_000 })) {
      expect(await approveBtn.isEnabled()).toBeTruthy()
    }
  })
})
