import { defineConfig } from '@playwright/test'

const databaseUrl =
  process.env.DATABASE_URL ||
  'postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // serial — tests share a single backend + DB
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      // FastAPI backend
      command: 'cd .. && uv run uvicorn app.main:app --port 8000',
      port: 8000,
      timeout: 30_000,
      reuseExistingServer: !process.env.CI,
      env: {
        DATABASE_URL: databaseUrl,
        ANTHROPIC_API_KEY: 'test-key',
        ENVIRONMENT: 'test',
        GOOGLE_API_KEY: 'fake-test-key',
      },
    },
    {
      // Vite dev server (proxies /api → :8000)
      command: 'npm run dev',
      port: 5173,
      timeout: 15_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
})
