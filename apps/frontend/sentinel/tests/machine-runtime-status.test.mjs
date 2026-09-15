import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('machine runtime actions match state and preserve both Sentinel themes', { timeout: 60000 }, async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{ name: 'runtime-card-fixture', enforce: 'pre',
      resolveId(id, importer) {
        if (id === '\0runtime-card-fixture') return id;
        if (id === './env' && importer?.endsWith('/lib/api.ts')) return '\0runtime-card-env';
      },
      load(id) {
        if (id === '\0runtime-card-env') return 'export const API_BASE_URL = "/api/v1";';
        if (id === '\0runtime-card-fixture') return `
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {MemoryRouter} from 'react-router-dom';
          import {WorkspaceProvider} from '/src/lib/workspace-context.tsx';
          import {MachinesPanel} from '/src/components/runtime/MachinesPanel.tsx';
          import {useThemeStore} from '/src/store/theme-store.ts';
          import '/src/index.css';
          window.setTestTheme = theme => useThemeStore.getState().setTheme(theme);
          createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter, null,
            React.createElement(WorkspaceProvider, {instanceName:'test'}, React.createElement(MachinesPanel))));`;
      },
      configureServer(s) { s.middlewares.use(async (req, res, next) => {
        if (req.url !== '/runtime-preview') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(await s.transformIndexHtml(req.url, '<html class="dark"><body><div id="root" style="max-width:780px;margin:24px auto;padding:12px"></div><script type="module" src="/@id/__x00__runtime-card-fixture"></script></body></html>'));
      }); },
    }, react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error',
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 960, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const names = ['mac-primary', 'mac-new', 'mac-recovery', 'mac-current'];
    const calls = [];
    const installs = [];
    let verifyFails = false;
    let identityChanged = false;
    let requireNewApproval = false;
    let finishInstall;
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      calls.push({ method: request.method(), pathname });
      const id = pathname.split('/')[4];
      let payload;
      if (pathname.endsWith('/runtime/verify') && verifyFails) {
        await route.fulfill({ status: 502, json: { detail: 'Runtime verification unavailable' } });
        return;
      }
      if (pathname.endsWith('/runtime/verify')) payload = { checks: [{ name: 'Runtime files', status: 'passed', detail: 'Files match the installed release.' }, { name: 'Runtime service', status: 'passed', detail: 'Responded to a read-only status request.' }] };
      else if (pathname.endsWith('/runtime') && request.method() === 'POST') {
        installs.push(request.postDataJSON());
        if (requireNewApproval) {
          requireNewApproval = false;
          await route.fulfill({json: {approval_required: true, workspaces: ['second-workspace'], workspace_details: [{id: 'second-workspace', name: 'Newly started'}]}});
          return;
        }
        if (finishInstall) await new Promise(resolve => { finishInstall = resolve; });
        payload = installs.at(-1).approved_workspaces.length ? { installed: true } : { approval_required: true, workspaces: ['running-workspace-id'] };
      }
      else if (pathname.endsWith('/capabilities')) payload = { providers: [{ provider: 'ssh', has_lifecycle: false }], provider_options: [] };
      else if (pathname.endsWith('/machines')) payload = names.map((name, index) => ({ id: String(index), name, provider: 'ssh', auth_type: 'password', status: 'ready', username: 'tester', host: '192.0.2.10', port: 22 }));
      else payload = { installed: id !== '1', host_key_changed: identityChanged, identity_verified: id !== '1', workspaces: [{id: 'running-workspace-id', name: 'Sample Ubuntu', instance: 'test'}], installed_version: id === '3' ? 'new' : 'old', available_version: 'new', update_phase: id === '2' ? 'activating' : 'complete', host_key: 'test', fingerprint: 'SHA256:test', path: '~/.sentinel/runtime' };
      await route.fulfill({ json: payload });
    });
    await page.goto(server.resolvedUrls.local[0] + 'runtime-preview');
    try { await page.getByRole('button', { name: 'Update runtime', exact: true }).waitFor({ timeout: 10000 }); }
    catch (error) { throw new Error(`${error.message}\n${errors.join('\n')}\n${await page.locator('body').innerText()}`); }
    assert.equal(await page.getByRole('button', { name: 'Install runtime', exact: true }).count(), 1);
    assert.equal(await page.getByRole('button', { name: 'Recover runtime', exact: true }).count(), 1);
    assert.equal(await page.getByText('SSH verified', { exact: true }).count(), 4);
    const current = page.locator('.machine-card').filter({ hasText: 'mac-current' });
    assert.equal(await current.getByText('Up to date', { exact: true }).count(), 1);
    assert.equal(await current.getByRole('button', { name: 'Verify / repair…' }).count(), 1);
    for (const theme of ['dark', 'light']) {
      await page.evaluate(theme => window.setTestTheme(theme), theme);
      await page.waitForTimeout(250); // Allow existing theme/button transitions to finish.
      const color = await page.locator('.machine-card').first().evaluate(el => getComputedStyle(el).backgroundColor);
      assert.notEqual(color, 'rgba(0, 0, 0, 0)');
      if (process.env.SENTINEL_SCREENSHOT_DIR) await page.screenshot({ path: process.env.SENTINEL_SCREENSHOT_DIR + '/runtime-' + theme + '.png', fullPage: true });
    }
    await page.setViewportSize({ width: 360, height: 1000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await current.getByRole('button', { name: 'Verify / repair…' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', {name: 'Close', exact: true}).click();
    await dialog.waitFor({state: 'hidden'});
    await current.getByRole('button', {name: 'Verify / repair…'}).click();
    await dialog.getByRole('heading').click();
    assert.equal(await dialog.isVisible(), true, 'Inside clicks must not dismiss');
    await page.mouse.click(5, 5);
    await dialog.waitFor({state: 'hidden'});
    await current.getByRole('button', {name: 'Verify / repair…'}).click();
    await dialog.getByRole('heading', { name: 'Repair runtime' }).waitFor();
    await dialog.getByText('Sample Ubuntu', { exact: true }).waitFor();
    assert.equal(installs.length, 0);
    assert.equal(calls.filter(call => call.pathname.endsWith('/verify')).length, 0, 'Opening must not auto-verify');
    assert.equal(await dialog.getByRole('checkbox').count(), 0, 'Pinned identity needs no repeated trust prompt');
    assert.equal(await dialog.locator('summary').count(), 0);
    assert.equal(await dialog.getByRole('region', {name: 'Runtime details'}).isVisible(), true);
    for (const theme of ['dark', 'light']) {
      await page.evaluate(theme => window.setTestTheme(theme), theme);
      await page.setViewportSize({width: 960, height: 760});
      await page.waitForTimeout(250);
      const bounds = await dialog.boundingBox();
      assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 760);
      if (process.env.SENTINEL_SCREENSHOT_DIR) await page.screenshot({path: process.env.SENTINEL_SCREENSHOT_DIR + '/runtime-dialog-' + theme + '.png'});
    }
    await page.setViewportSize({width: 360, height: 640});
    const footer = await dialog.getByRole('button', {name: 'Repair & restart'}).boundingBox();
    assert.ok(footer.x >= 0 && footer.x + footer.width <= 360 && footer.y + footer.height <= 640);

    await dialog.getByRole('button', { name: 'Repair & restart', exact: true }).click();
    await dialog.getByRole('heading', { name: 'Runtime ready' }).waitFor();
    assert.deepEqual(installs, [{ approved: true, reinstall: true, host_key: 'test', approved_workspaces: ['running-workspace-id'] }]);
    await dialog.getByRole('button', { name: 'Done' }).click();
    verifyFails = true;
    await current.getByRole('button', { name: 'Verify / repair…' }).click();
    await dialog.getByRole('button', { name: 'Verify runtime', exact: true }).click();
    await dialog.getByRole('alert').filter({ hasText: 'Verification could not finish' }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: 'Repair & restart' }).isEnabled(), true, 'Failed verification must leave repair available');
    assert.equal(installs.length, 1);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await page.getByRole('button', { name: 'Install runtime', exact: true }).click();
    await dialog.getByRole('checkbox').waitFor();
    assert.equal(await dialog.getByRole('button', {name: 'Install & restart'}).isDisabled(), true);
    await dialog.getByRole('checkbox').check();
    assert.equal(await dialog.getByRole('button', {name: 'Install & restart'}).isEnabled(), true);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    identityChanged = true;
    await current.getByRole('button', {name: 'Verify / repair…'}).click();
    await dialog.getByRole('alert').filter({hasText: 'SSH identity changed'}).waitFor();
    assert.equal(await dialog.getByRole('button', {name: 'Repair & restart'}).isDisabled(), true);
    await dialog.getByRole('button', {name: 'Cancel'}).click();
    identityChanged = false;
    requireNewApproval = true;
    await current.getByRole('button', {name: 'Verify / repair…'}).click();
    await dialog.getByRole('button', {name: 'Repair & restart'}).click();
    await dialog.getByText('Newly started', {exact: true}).waitFor();
    assert.equal(installs.length, 2, 'Stale approval cannot auto-retry');
    finishInstall = true;
    await dialog.getByRole('button', {name: 'Repair & restart'}).click();
    await dialog.getByRole('button', {name: 'Repairing…'}).waitFor();
    assert.equal(await dialog.getByRole('button', {name: 'Cancel'}).isDisabled(), true);
    assert.equal(await dialog.getByRole('button', {name: 'Close', exact: true}).isDisabled(), true);
    await page.mouse.click(5, 5);
    assert.equal(await dialog.isVisible(), true, 'Outside clicks cannot dismiss an active install');
    await page.keyboard.press('Escape');
    assert.equal(await dialog.isVisible(), true);
    for (let i = 0; typeof finishInstall !== 'function' && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(typeof finishInstall, 'function');
    finishInstall();
    finishInstall = null;
    await dialog.getByRole('heading', {name: 'Runtime ready'}).waitFor();
    assert.deepEqual(installs[2].approved_workspaces, ['second-workspace']);
    await dialog.getByRole('button', {name: 'Done'}).click();
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
