import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('workspace creation uses a separate OS step, keeps the same stacks, and submits the distro', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage();
    await page.addInitScript(() => {
      window.fetch = async () => new Response(JSON.stringify({ os: 'darwin', container_available: true,
        distributions: [{ id: 'alpine', name: 'Alpine Linux', detail: 'Accelerated desktop', tools: null },
          { id: 'ubuntu', name: 'Ubuntu 24.04 LTS', detail: 'All stacks and accelerated desktop', tools: ['git', 'python', 'desktop'] },
          { id: 'debian', name: 'Debian 13', detail: 'All stacks and accelerated desktop', tools: ['git', 'python', 'desktop'] }],
        tools: [{ id: 'git', name: 'Git', detail: 'Version control', category: 'Essentials' },
          { id: 'python', name: 'Python', detail: 'Python runtime', category: 'Languages' },
          { id: 'desktop', name: 'Desktop', detail: 'Graphical desktop', category: 'Desktop' }],
        stacks: [{ id: 'python', name: 'Python', description: 'Python stack', tools: ['python'] },
          { id: 'desktop', name: 'Desktop', description: 'Desktop stack', tools: ['desktop'] }],
      }), { headers: { 'Content-Type': 'application/json' } });
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html`);
    const selector = page.getByRole('radio', { name: /Alpine Linux/ });
    assert.equal(await selector.count(), 0);
    assert.equal(await page.getByRole('tab', { name: /Tools/ }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Next: OS' }).isDisabled(), true);
    assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    await page.getByLabel('Name', { exact: true }).fill('Ubuntu workspace');
    await page.getByPlaceholder('Choose a folder or enter a path').fill('/projects/test');
    await page.getByLabel('Name', { exact: true }).press('Enter');
    assert.equal(await selector.isChecked(), true);
    await page.getByRole('radio', { name: /Ubuntu 24/ }).check();
    await page.getByRole('button', { name: 'Next: Stacks' }).click();
    assert.equal(await page.getByRole('button', { name: /Desktop stack/ }).count(), 1);
    await page.getByRole('button', { name: 'Next: Tools' }).click();
    await page.getByRole('checkbox', { name: /Python/ }).check();
    await page.getByRole('checkbox', { name: /Graphical desktop/ }).check();
    await page.getByRole('button', { name: 'Next: Resources' }).click();
    assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).fill('0');
    assert.equal(await page.getByRole('button', { name: 'Create workspace' }).isDisabled(), true);
    assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).fill('2');
    await page.getByRole('button', { name: 'Create workspace' }).click();
    const value = await page.evaluate(() => window.savedWorkspace);
    assert.equal(value.distribution, 'ubuntu');
    assert.deepEqual(value.development_tools, ['git', 'python', 'desktop']);
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html?edit`);
    const toolsTab = page.getByRole('tab', { name: 'Tools', exact: true });
    assert.equal(await toolsTab.isEnabled(), true);
    await toolsTab.click();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    assert.equal((await page.evaluate(() => window.savedWorkspace)).name, 'Existing');
  } finally { await browser?.close(); await server.close(); }
});
