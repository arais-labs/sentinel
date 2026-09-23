import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('a fresh client discovers worker workspaces and keeps them when disconnected', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      window.workerDisconnected = false;
      // Drive the existing refresh explicitly; no test sleeps or wall-clock polling.
      window.setInterval = callback => { window.refreshLibrary = callback; return 1; };
      const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
      window.fetch = async input => {
        const url = String(input);
        if (url.endsWith('/machines')) return response([{ id: 'worker', name: 'Work Mac', provider: 'ssh' }, { id: 'local', name: 'Local Mac', provider: 'local' }]);
        if (url.endsWith('/machines/capabilities')) return response({ providers: [] });
        if (url.endsWith('/workspaces?include_runtime=false')) return response([]);
        if (url.endsWith('/workspaces/discover/worker')) return window.workerDisconnected
          ? response({ detail: 'Worker disconnected' }, 503)
          : response([{ id: 'workspace', name: 'Discovered project', machine_id: 'worker', directory: '/remote/project',
            distribution: 'ubuntu', development_tools: ['git'], revision: 7, container_state: 'running' }]);
        throw new Error(`Unexpected request: ${url}`);
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-library.html`);
    const card = page.locator('article').filter({ hasText: 'Discovered project' });
    await card.getByText('Running', { exact: true }).waitFor();
    assert.equal(await card.getByText('/remote/project', { exact: true }).isVisible(), true);
    await page.getByRole('searchbox', { name: 'Search workspaces' }).fill('  REMOTE/PROJECT  ');
    await page.getByRole('combobox', { name: 'Filter by machine' }).selectOption('worker');
    assert.equal(await page.locator('article').count(), 1);
    await page.getByRole('combobox', { name: 'Filter by machine' }).selectOption('local');
    await page.getByText('No matching workspaces', { exact: true }).waitFor();
    assert.equal(await page.locator('article').count(), 0);
    await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
    assert.equal(await page.getByRole('searchbox', { name: 'Search workspaces' }).inputValue(), '');
    assert.equal(await page.getByRole('combobox', { name: 'Filter by machine' }).inputValue(), '');
    assert.equal(await page.locator('article').count(), 1);
    await page.evaluate(() => { window.workerDisconnected = true; window.refreshLibrary(); });
    await card.getByText('Worker disconnected', { exact: true }).waitFor();
    assert.equal(await page.locator('article').count(), 1);
    await page.evaluate(() => { window.workerDisconnected = false; window.refreshLibrary(); });
    await card.getByText('Running', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});

test('workspace cards stay usable while machines and individual runtime checks stall', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage();
    page.setDefaultTimeout(10_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const rows = ['Slow remote', 'Healthy'].map((name, id) => ({ id: String(id), name, machine_id: 'mac', directory: `/projects/${id}`, container_state: 'checking' }));
      let finishSlow, finishMachines;
      const slow = new Promise(resolve => { finishSlow = resolve; });
      const machines = new Promise(resolve => { finishMachines = resolve; });
      window.slowRequests = 0;
      window.failCatalog = false;
      window.remoteRecovered = false;
      window.releaseSlow = () => finishSlow();
      window.releaseMachines = () => finishMachines();
      const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
      window.fetch = async input => {
        const url = String(input);
        if (url.endsWith('/machines')) { await machines; return response([{ id: 'mac', name: 'Mac', provider: 'local' }]); }
        if (url.endsWith('/machines/capabilities')) return response({ providers: [] });
        if (url.includes('/instances/other/')) return response(url.includes('include_runtime=false')
          ? [{ ...rows[1], id: 'other', name: 'Other workspace' }] : { container_state: 'running' });
        if (url.endsWith('/workspaces?include_runtime=false')) return window.failCatalog
          ? response({ detail: 'Catalog temporarily unavailable' }, 503) : response(rows);
        if (url.endsWith('/workspaces/0/status')) {
          window.slowRequests++;
          await slow;
          return window.remoteRecovered ? response({ container_state: 'running' }) : response({ detail: 'Remote connection timed out' }, 502);
        }
        if (url.endsWith('/workspaces/1/status')) return response({ container_state: 'running' });
        throw new Error(`Unexpected request: ${url}`);
      };
    });
    const url = `${server.resolvedUrls.local[0]}tests/fixtures/workspace-library.html`;
    await page.goto(url);
    const slow = page.locator('article').filter({ hasText: 'Slow remote' });
    const healthy = page.locator('article').filter({ hasText: 'Healthy' });
    await slow.getByText('Checking connection…', { exact: true }).waitFor();
    await healthy.getByText('Running', { exact: true }).waitFor();
    assert.equal(await healthy.getByRole('button', { name: 'Stop', exact: true }).isEnabled(), true);
    assert.equal(await slow.getByRole('button', { name: 'Start', exact: true }).isEnabled(), false);
    assert.equal(await slow.getByRole('button', { name: 'Edit', exact: true }).isEnabled(), true);
    await page.getByRole('button', { name: 'machines', exact: true }).click();
    assert.equal(await page.getByRole('button', { name: 'machines', exact: true }).getAttribute('aria-pressed'), 'true');
    await page.getByRole('button', { name: 'workspaces', exact: true }).click();
    // Polling must not stack more requests onto an unresponsive runtime.
    await page.waitForTimeout(2800);
    assert.equal(await page.evaluate(() => window.slowRequests), 1);
    assert.equal(await page.locator('article').count(), 2);
    await page.evaluate(() => { window.releaseSlow(); window.releaseMachines(); });
    await slow.getByText('Remote connection timed out', { exact: true }).waitFor();
    await healthy.getByText('Mac', { exact: true }).waitFor();
    assert.equal(await healthy.getByText('Running', { exact: true }).isVisible(), true);
    await page.evaluate(() => { window.remoteRecovered = true; });
    await slow.getByText('Running', { exact: true }).waitFor();
    await page.evaluate(() => { window.failCatalog = true; });
    await page.getByRole('alert').filter({ hasText: 'Catalog temporarily unavailable' }).waitFor();
    assert.equal(await page.locator('article').count(), 2);
    await page.evaluate(() => { window.failCatalog = false; });
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await page.getByRole('alert').waitFor({ state: 'detached' });

    // A late response from an old instance cannot replace the current catalog.
    await page.goto(url);
    await page.getByText('Slow remote', { exact: true }).waitFor();
    await page.evaluate(() => window.selectLibraryInstance('other'));
    await page.getByText('Other workspace', { exact: true }).waitFor();
    await page.evaluate(() => { window.releaseSlow(); window.releaseMachines(); });
    await page.waitForTimeout(100);
    assert.equal(await page.locator('article').count(), 1);
    assert.equal(await page.getByText('Other workspace', { exact: true }).isVisible(), true);
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
