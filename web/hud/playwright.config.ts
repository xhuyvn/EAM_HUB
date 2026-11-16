import { defineConfig, devices } from '@playwright/test'

const DEFAULT_TEST_DB_URL =
  'postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test'
const TEST_DATABASE_URL = process.env.TEST_DATABASE_URL || DEFAULT_TEST_DB_URL
const E2E_TOKEN = process.env.E2E_TOKEN || 'e2e-hud-owner-token'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'uv run python ../../tests/hud/e2e_fixture_server.py',
      url: 'http://127.0.0.1:8000/healthz',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        TEST_DATABASE_URL,
        E2E_TOKEN,
      },
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: 'http://127.0.0.1:5173/',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
