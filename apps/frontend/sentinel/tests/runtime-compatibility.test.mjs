import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('desktop centers failures and only remote runtime upgrades offer an updater', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
    page.setDefaultTimeout(10_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.sentinelDesktop = { onSocketEvent: () => () => {}, socketOpen: async () => {}, socketClose: () => {}, socketSend: () => {} };
      window.runtimeCode = 'runtime_update_required';
      window.runtimeMessage = 'Update Sentinel Runtime on Work Mac.';
      window.requests = [];
      window.fetch = async (input, options) => {
        const url = String(input);
        window.requests.push({ url, method: options?.method ?? 'GET' });
        let data = {}, status = 200;
        if (url.includes('/runtime/live-view')) { status = 409; data = { error: { code: window.runtimeCode, message: window.runtimeMessage, details: { machine_id: 'worker' } } }; }
        else if (url.endsWith('/workspace')) data = { id: 'workspace', name: 'Project', machine_id: 'worker', desktop: 'xfce' };
        else if (url.endsWith('/machines')) data = [{ id: 'worker', name: 'Work Mac', provider: 'ssh' }];
        else if (url.endsWith('/runtime/plan')) data = { installed: true, identity_verified: true, installed_version: 'old', available_version: 'new', workspaces: [], host_key: 'test' };
        else if (url.includes('/messages')) data = { items: [], has_more: false };
        else if (url.includes('/sessions?')) data = { items: [{ id: 'session-a', title: 'Test', status: 'active' }] };
        else if (url.endsWith('/instances')) data = [{ name: 'test', status: 'running' }];
        else if (url.endsWith('/workspaces') || url.includes('/sub-agents')) data = [];
        else if (url.includes('/models')) data = { models: [] };
        else if (url.includes('/agent-modes')) data = { modes: [] };
        return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/desktop-pane.html`);
    await page.waitForFunction(() => window.openDesktop);
    await page.evaluate(() => window.openDesktop());
    const empty = page.locator('.workspace-desktop-empty').filter({ hasText: 'Workspace unavailable' });
    const update = empty.getByRole('button', { name: 'Update runtime', exact: true });
    await update.waitFor();
    assert.equal(await page.locator('.workspace-desktop-notice[role="alert"]').count(), 0);
    const panel = await page.locator('.workspace-desktop-content').boundingBox();
    const message = await empty.getByText('Update Sentinel Runtime on Work Mac.', { exact: true }).boundingBox();
    const button = await update.boundingBox();
    assert.ok(Math.abs((message.y + button.y + button.height) / 2 - (panel.y + panel.height / 2)) < panel.height * .18);
    assert.ok(Math.abs(button.x + button.width / 2 - (panel.x + panel.width / 2)) < 2);
    await page.screenshot({ path: '/tmp/sentinel-runtime-centered.png' });
    await update.click();
    const dialog = page.getByRole('dialog', { name: 'Update runtime', exact: true });
    await dialog.getByRole('button', { name: 'Update runtime', exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.requests.some(request => request.method === 'POST')), false, 'Opening the updater must not install anything');
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    await page.evaluate(() => { window.runtimeCode = 'app_update_required'; window.runtimeMessage = 'Restart Sentinel to load its bundled runtime.'; });
    await page.getByRole('button', { name: 'Refresh desktop status' }).click();
    await empty.getByText('Restart Sentinel to load its bundled runtime.', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Update runtime', exact: true }).count(), 0);
    await page.evaluate(() => { window.runtimeCode = 'internal_error'; window.runtimeMessage = 'Desktop renderer disconnected.'; });
    await page.getByRole('button', { name: 'Refresh desktop status' }).click();
    await page.locator('.workspace-desktop-empty').getByRole('alert').filter({ hasText: 'Desktop renderer disconnected.' }).waitFor();
    assert.equal(await page.locator('.workspace-desktop-notice[role="alert"]').count(), 0);
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});

for (const catalogFirst of [false, true]) test(`outdated worker preserves cached cards, catalog first=${catalogFirst}`, async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage();
    page.setDefaultTimeout(10_000);
    await page.addInitScript(first => {
      const gate = Promise.withResolvers();
      window.releaseLibrary = () => gate.resolve();
      window.setInterval = callback => { window.refreshLibrary = callback; return 1; };
      window.workerHealthy = false;
      const row = { id: 'project', name: 'Saved project', machine_id: 'worker', directory: '/project', development_tools: [], container_state: 'checking' };
      const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
      window.fetch = async input => {
        const url = String(input);
        if (url.endsWith('/machines')) return response([{ id: 'worker', name: 'Work Mac', provider: 'ssh' }]);
        if (url.includes('include_runtime=false')) {
          if (!first) await gate.promise;
          return response([row]);
        }
        if (url.includes('/discover/worker')) {
          if (first) await gate.promise;
          window.discoveryAttempted = true;
          return window.workerHealthy ? response([{ ...row, container_state: 'running' }])
            : response({ error: { code: 'runtime_update_required', message: 'Update the remote runtime.', details: { machine_id: 'worker' } } }, 409);
        }
        if (url.endsWith('/status')) return response({ container_state: 'checking' });
        return response({});
      };
    }, catalogFirst);
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-library.html`);
    const card = page.locator('article').filter({ hasText: 'Saved project' });
    if (catalogFirst) await card.waitFor();
    else await page.waitForFunction(() => window.discoveryAttempted);
    await page.evaluate(() => window.releaseLibrary());
    await card.getByRole('button', { name: 'Update runtime', exact: true }).waitFor();
    assert.equal(await page.locator('article').count(), 1);
    await page.evaluate(() => { window.workerHealthy = true; window.refreshLibrary(); });
    await card.getByText('Running', { exact: true }).waitFor();
    assert.equal(await card.getByRole('button', { name: 'Update runtime', exact: true }).count(), 0);
  } finally { await browser?.close(); await server.close(); }
});
