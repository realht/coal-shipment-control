const path = require('path');
const { defineConfig, devices } = require('@playwright/test');

const root = path.resolve(__dirname, '..');
const port = process.env.E2E_PORT || '8765';
const localBaseURL = `http://127.0.0.1:${port}`;

module.exports = defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 7_500 },
  outputDir: './test-results',
  reporter: [
    ['list'],
    ['html', { outputFolder: './playwright-report', open: 'never' }],
    ['json', { outputFile: './artifacts/results.json' }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL || localBaseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  },
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: `python prepare.py && python ../app/manage.py runserver 127.0.0.1:${port} --noreload`,
    cwd: __dirname,
    env: {
      ...process.env,
      PYTHONPATH: `${path.join(root, 'app')}${path.delimiter}${root}`,
      DJANGO_SETTINGS_MODULE: 'e2e.settings',
    },
    url: `${localBaseURL}/healthz/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
  ],
});
