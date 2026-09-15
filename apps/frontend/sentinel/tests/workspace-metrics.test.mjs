import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('workspace metrics collect before hover, share history, and retain it across reopen', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const server = await createServer({ root, configFile: false, plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 420, height: 800 }, deviceScaleFactor: 2 });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.clock.install();
    await page.addInitScript(() => {
      window.metricRequests = 0;
      window.failMetrics = false;
      window.rebooted = false;
      window.sentinelDesktop = { onSocketEvent() { return () => {}; }, async socketOpen() {}, socketClose() {}, socketSend() {} };
      window.fetch = async input => {
        const path = String(input);
        let payload = {};
        if (path.endsWith('/workspace')) payload = { id: 'workspace-a', name: 'Sample Ubuntu', container_state: 'running' };
        if (path.endsWith('/metrics')) {
          const n = ++window.metricRequests;
          if (window.failMetrics) throw new Error('connection interrupted');
          payload = { state: 'running', uptime: window.rebooted ? 1 : 100 + n * 3, resources: { cpus: 6 }, cpu_percent: 8 + Math.abs(Math.sin(n / 3)) * 42,
            memory_total_bytes: 10 * 1024 ** 3, memory_used_bytes: (2.6 + n / 100) * 1024 ** 3,
            disk_total_bytes: 32 * 1024 ** 3, disk_used_bytes: 6.4 * 1024 ** 3,
            network_receive_bytes_per_second: Math.abs(Math.sin(n / 4)) * 70000,
            network_send_bytes_per_second: Math.abs(Math.cos(n / 3)) * 30000 };
        }
        return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-metrics.html`);
    await page.waitForFunction(() => window.metricRequests === 1);
    assert.equal(await page.locator('.workspace-runtime-stats').count(), 0);
    for (let n = 2; n <= 21; n++) {
      await page.clock.fastForward(3000);
      await page.waitForFunction(n => window.metricRequests === n, n);
    }
    await page.getByRole('button', { name: 'Toggle metrics', exact: true }).click();
    const cpu = page.locator('[data-metric="cpu"] .workspace-stat-trace');
    const path = await cpu.getAttribute('d');
    assert.equal((path.match(/L/g) || []).length, 20, 'History must already exist on first open');
    assert.equal(await page.evaluate(() => window.metricRequests), 21, 'Two headers share one collector; opening adds no request');
    const style = await cpu.evaluate(node => ({ fill: getComputedStyle(node).fill, stroke: getComputedStyle(node).stroke }));
    assert.equal(style.fill, 'none');
    assert.equal(style.stroke, 'rgb(99, 184, 239)');
    assert.equal(await page.locator('.workspace-stat-axis').first().evaluate(node => getComputedStyle(node).display), 'flex');
    await page.locator('.session-telemetry-panel').screenshot({ path: '/tmp/sentinel-workspace-metrics.png' });
    await page.getByRole('button', { name: 'Toggle metrics', exact: true }).click();
    await page.getByRole('button', { name: 'Toggle metrics', exact: true }).click();
    assert.equal(await cpu.getAttribute('d'), path, 'Popover reopen must preserve history');
    await page.getByRole('button', { name: 'Toggle header', exact: true }).click();
    await page.getByRole('button', { name: 'Toggle header', exact: true }).click();
    await cpu.waitFor();
    assert.equal(await cpu.getAttribute('d'), path, 'Returning to the workspace must preserve history');
    await page.evaluate(() => { window.failMetrics = true; });
    await page.clock.fastForward(3000);
    await page.getByText('Last known usage').waitFor();
    assert.ok(await cpu.getAttribute('d'), 'Failure must retain the last graph');
    await page.evaluate(() => { window.failMetrics = false; window.rebooted = true; });
    await page.clock.fastForward(15000);
    await page.getByText('Usage history', { exact: true }).waitFor();
    assert.equal(((await cpu.getAttribute('d')).match(/L/g) || []).length, 0, 'Guest reboot starts a new history');
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
