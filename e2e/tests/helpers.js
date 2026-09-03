const fs = require('fs');
const path = require('path');
const { expect } = require('@playwright/test');

const AXE_SOURCE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

async function login(page, username = 'e2e-admin', password = 'Acceptance-Admin-2026') {
  await page.goto('/accounts/login/');
  await page.locator('[name=username]').fill(username);
  await page.locator('[name=password]').fill(password);
  await page.getByRole('button', { name: 'Войти' }).click();
  await expect(page).not.toHaveURL(/\/accounts\/login/);
}

async function expectAccessible(page) {
  await page.addScriptTag({ content: AXE_SOURCE });
  const results = await page.evaluate(async () => axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] },
    resultTypes: ['violations'],
  }));
  const blocking = results.violations.filter(({ impact }) => impact === 'serious' || impact === 'critical');
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
}

function installFailureGuards(page) {
  const failures = [];
  const applicationOrigin = new URL(process.env.E2E_BASE_URL || `http://127.0.0.1:${process.env.E2E_PORT || '8765'}`).origin;
  const isApplicationRequest = rawUrl => {
    try { return new URL(rawUrl).origin === applicationOrigin; } catch { return false; }
  };
  page.on('pageerror', error => failures.push(`pageerror: ${error.message}`));
  page.on('console', message => {
    const expectedPermissionDenial = message.text() === 'Failed to load resource: the server responded with a status of 403 (Forbidden)';
    if (message.type() === 'error' && !expectedPermissionDenial) failures.push(`console: ${message.text()}`);
  });
  page.on('response', response => {
    if (isApplicationRequest(response.url()) && response.status() >= 500) {
      failures.push(`HTTP ${response.status()}: ${response.url()}`);
    }
  });
  page.on('requestfailed', request => {
    const expectedDownloadAbort = /\/documents\/\d+\/serve\/$/.test(new URL(request.url()).pathname)
      && request.failure()?.errorText === 'net::ERR_ABORTED';
    if (isApplicationRequest(request.url()) && !expectedDownloadAbort) {
      failures.push(`requestfailed: ${request.url()} ${request.failure()?.errorText || ''}`);
    }
  });
  return () => expect(failures, failures.join('\n')).toEqual([]);
}

module.exports = { expectAccessible, installFailureGuards, login, fixture: name => path.join(__dirname, '..', 'fixtures', name) };
