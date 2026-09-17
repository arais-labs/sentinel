import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('layout retains panels, positions, sizes and focus across refresh and browser restart', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const profile = await mkdtemp(path.join(tmpdir(), 'sentinel-layout-'));
  const server = await createServer({ root, configFile: false, plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    const url = `${server.resolvedUrls.local[0]}tests/fixtures/workspace-layout.html`;
    const launch = () => chromium.launchPersistentContext(profile, { headless: true, viewport: { width: 1200, height: 800 }, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    browser = await launch();
    let page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(url);
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    const saved = await page.evaluate(() => window.layoutTest.arrange());
    assert.equal(Object.keys(saved.panels).length, 3);
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.workspace')).state.sessionLayouts[JSON.stringify(['test', 'session-a'])]), saved);
    assert.deepEqual(await page.evaluate(() => window.layoutTest.remount()), saved);
    await page.reload();
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.deepEqual(await page.evaluate(() => window.layoutTest.snapshot()), saved);
    await browser.close();
    browser = await launch();
    page = await browser.newPage();
    await page.goto(url);
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.deepEqual(await page.evaluate(() => window.layoutTest.snapshot()), saved);

    // Focus hides the live header, but pagehide must save the ordinary layout.
    const focused = await page.evaluate(() => window.layoutTest.focusAndSave());
    assert.ok(focused.grid.maximizedNode);
    assert.match(JSON.stringify(focused), /"hideHeader":true/);
    const persisted = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.workspace')).state.sessionLayouts[JSON.stringify(['test', 'session-a'])]);
    assert.equal(persisted.grid.maximizedNode, undefined);
    assert.doesNotMatch(JSON.stringify(persisted), /"hideHeader"/);
    assert.deepEqual(persisted.panels, saved.panels);
    assert.equal(await page.evaluate(() => window.layoutTest.headersVisible()), false, 'saving must not exit live focus');
    await page.reload();
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.equal(await page.evaluate(() => window.layoutTest.headersVisible()), true);
    assert.deepEqual(await page.evaluate(() => window.layoutTest.snapshot()), persisted);

    // Old installations may already have persisted these temporary flags.
    await page.addInitScript(() => {
      const legacy = sessionStorage.getItem('legacy-focus-layout');
      if (!legacy) return;
      sessionStorage.removeItem('legacy-focus-layout');
      const stored = JSON.parse(localStorage.getItem('sentinel.workspace'));
      stored.state.sessionLayouts[JSON.stringify(['test', 'session-a'])] = JSON.parse(legacy);
      localStorage.setItem('sentinel.workspace', JSON.stringify(stored));
    });
    await page.evaluate(layout => sessionStorage.setItem('legacy-focus-layout', JSON.stringify(layout)), focused);
    await page.reload();
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.equal(await page.evaluate(() => window.layoutTest.headersVisible()), true);
    assert.deepEqual(await page.evaluate(() => window.layoutTest.snapshot()), persisted);
    assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.workspace')).state.sessionLayouts[JSON.stringify(['test', 'session-a'])]), persisted);

    // Undo restores focus separately; its layout must start with visible headers.
    await page.evaluate(() => window.layoutTest.focusAndSave());
    const undone = await page.evaluate(() => window.layoutTest.undoFromFocus());
    assert.ok(undone.focused);
    assert.equal(undone.layout.grid.maximizedNode, undefined);
    assert.doesNotMatch(JSON.stringify(undone.layout), /"hideHeader"/);
    assert.deepEqual(undone.layout.panels, saved.panels);
    const orphaned = await page.evaluate(() => window.layoutTest.orphanedLayout());
    await page.evaluate(layout => sessionStorage.setItem('legacy-focus-layout', JSON.stringify(layout)), orphaned);
    await page.reload();
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.ok((await page.evaluate(() => window.layoutTest.groupSizes())).every(size => size > 0), 'Restoring a saved empty group must reclaim its space');
    assert.deepEqual((await page.evaluate(() => window.layoutTest.snapshot())).panels, orphaned.panels, 'Recovery must preserve all real panels');
    await page.evaluate(() => window.layoutTest.addEmptyGroup());
    await page.waitForFunction(() => window.layoutTest.groupSizes().every(size => size > 0));
    const normalized = await page.evaluate(() => window.layoutTest.snapshot());
    await page.reload();
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.deepEqual(await page.evaluate(() => window.layoutTest.snapshot()), normalized);
    // Existing Voice panels disappear without disturbing the remaining layout.
    const legacyVoice = structuredClone(normalized);
    const oldVoiceId = Object.keys(legacyVoice.panels)[0];
    legacyVoice.panels[oldVoiceId].params = {...legacyVoice.panels[oldVoiceId].params, tabId:'voice'};
    await page.evaluate(layout => sessionStorage.setItem('legacy-focus-layout', JSON.stringify(layout)), legacyVoice);
    await page.reload();
    await page.waitForFunction(() => window.layoutTest);
    const migrated = await page.evaluate(() => window.layoutTest.snapshot());
    assert.equal(migrated.panels[oldVoiceId], undefined);
    for (const [id, panel] of Object.entries(normalized.panels)) {
      if (id !== oldVoiceId) assert.deepEqual(migrated.panels[id], panel);
    }
    await page.evaluate(() => window.layoutTest.closeAll());
    await page.reload();
    await page.waitForFunction(() => window.layoutTest).catch(error => { throw new Error(errors.join('\n') || error.message); });
    assert.equal(Object.keys((await page.evaluate(() => window.layoutTest.snapshot())).panels).length, 0);
  } finally {
    await browser?.close();
    await server.close();
    await rm(profile, { recursive: true, force: true });
  }
});
