import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

// The smoke runs against a stack that is already up (docker compose: Postgres plus the
// application image, which serves the console and the API from one origin). It starts
// nothing itself; see "End-to-end smoke" in console/README.md.
const baseURL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  testDir: import.meta.dirname,
  testMatch: '*.spec.ts',
  outputDir: path.join(import.meta.dirname, '..', 'test-results'),
  // One flow, in order. It consumes the tenant's one-time password, so a retry could never
  // sign in again: a failure is reported as it happened.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  forbidOnly: Boolean(process.env.CI),
  // The list for the terminal and the HTML report for what a failure leaves behind (CI
  // uploads it); it never opens a browser by itself.
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: path.join(import.meta.dirname, '..', 'playwright-report') }],
  ],
  use: {
    baseURL,
    // The console's default language; the spec reads its strings from the same catalogue.
    locale: 'es-MX',
    timezoneId: 'America/Mexico_City',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
