import { test, expect } from '@playwright/test'

test.describe('Onboarding flow', () => {
  test.beforeEach(async ({ page }) => {
    // Clear any previous profile state by visiting health first
    await page.goto('/profile')
    await page.waitForLoadState('networkidle')
  })

  test('profile page loads', async ({ page }) => {
    await page.goto('/profile')
    await expect(page.getByRole('heading', { name: 'Profile Setup' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible()
    await expect(page.getByPlaceholder('Type your preferences...')).toBeVisible()
  })

  test('resume upload populates profile card', async ({ page }) => {
    await page.goto('/profile')

    // Create a minimal .txt resume file
    const resumeContent = `Jane Smith
jane@example.com | +1-555-0100 | linkedin.com/in/janesmith | github.com/janesmith

EXPERIENCE
Senior Software Engineer - Acme Corp (2020-present)
- Led Python backend development
- Technologies: Python, FastAPI, PostgreSQL

SKILLS
Python (7 years), FastAPI (3 years), PostgreSQL (5 years)
`

    // Upload the resume file
    const fileInput = page.locator('input[type="file"]')
    await fileInput.setInputFiles({
      name: 'resume.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from(resumeContent),
    })

    // Wait for upload + LLM extraction + agent response
    await expect(page.getByText(/I've saved your profile|Is there anything else/i)).toBeVisible({
      timeout: 30_000,
    })

    // Expand the profile card (collapsed by default) then check for resume
    const profileCardToggle = page.getByText(/Current profile/i)
    if (await profileCardToggle.isVisible({ timeout: 5_000 })) {
      await profileCardToggle.click()
      await expect(page.getByText('Uploaded', { exact: true })).toBeVisible({ timeout: 10_000 })
    }
  })

  test('sending a chat message triggers agent response', async ({ page }) => {
    await page.goto('/profile')

    const input = page.getByPlaceholder('Type your preferences...')
    await input.fill('I am a senior Python engineer looking for roles in San Francisco')
    await input.press('Enter')

    // Wait for assistant response to appear in the chat
    await expect(
      page.locator('.bg-gray-100').filter({ hasText: /saved|profile|San Francisco|Engineer/i })
    ).toBeVisible({ timeout: 30_000 })
  })
})
