const { defineConfig, devices } = require('@playwright/test');
const fs = require('node:fs');
const python = process.env.DINING_TEST_PYTHON || (fs.existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');
if (!/^[\w/.: -]+$/.test(python)) throw new Error('Use a plain Python executable path');
const port = Number(process.env.DINING_E2E_PORT || 7879);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid test port');
module.exports = defineConfig({
  testDir: './tests/browser',
  testMatch: '*.spec.cjs',
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 12000 },
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    timezoneId: 'Asia/Kuala_Lumpur',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium-desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'chromium-narrow', use: { ...devices['Pixel 7'], viewport: { width: 320, height: 740 } } },
    { name: 'webkit-mobile', use: { ...devices['iPhone 13'] } },
  ],
  webServer: {
    command: `"${python}" tests/browser/server.py`,
    url: `http://127.0.0.1:${port}/e2e-ready`,
    reuseExistingServer: false,
    timeout: 45000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10000 },
    env: { PYTHONDONTWRITEBYTECODE: '1' },
  },
});
