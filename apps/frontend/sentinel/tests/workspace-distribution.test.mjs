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
    const { workspaceSaveError } = await server.ssrLoadModule('/src/components/runtime/workspaceValidation.ts');
    const { ApiError } = await server.ssrLoadModule('/src/lib/api.ts');
    assert.equal(workspaceSaveError(new ApiError('Request validation failed', 422, 'validation_error', [
      { loc: ['body', 'directory'], msg: 'Value error, Choose an absolute project directory, not the filesystem root', input: '/' },
    ])), 'Project folder: Choose an absolute project directory, not the filesystem root');
    assert.equal(workspaceSaveError(new ApiError('Machine unavailable', 422)), 'Machine unavailable');
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage();
    await page.addInitScript(() => {
      window.fetch = async input => {
        if (String(input).includes('/directories?')) {
          const path = new URL(String(input), window.location.origin).searchParams.get('path') || '/';
          return new Response(JSON.stringify({ path, parent: '/', directories: path === '/' ? ['projects'] : [] }), { headers: { 'Content-Type': 'application/json' } });
        }
        return new Response(JSON.stringify({ os: 'darwin', container_available: true,
        distributions: [{ id: 'alpine', name: 'Alpine Linux', detail: 'Accelerated desktop', tools: null },
          { id: 'ubuntu', name: 'Ubuntu 24.04 LTS', detail: 'All stacks and accelerated desktop', tools: ['git', 'python', 'desktop'] },
          { id: 'debian', name: 'Debian 13', detail: 'All stacks and accelerated desktop', tools: ['git', 'python', 'desktop'] }],
        tools: [{ id: 'git', name: 'Git', detail: 'Version control', category: 'Essentials' },
          { id: 'python', name: 'Python', detail: 'Python runtime', category: 'Languages' },
          { id: 'desktop', name: 'Desktop', detail: 'Graphical desktop', category: 'Desktop' }],
        stacks: [{ id: 'python', name: 'Python', description: 'Python stack', tools: ['python'] },
          { id: 'desktop', name: 'Desktop', description: 'Desktop stack', tools: ['desktop'] }],
      }), { headers: { 'Content-Type': 'application/json' } });
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html`);
    const selector = page.getByRole('radio', { name: /Alpine Linux/ });
    assert.equal(await selector.count(), 0);
    assert.equal(await page.getByRole('tab', { name: /Tools/ }).isDisabled(), true);
    assert.equal(await page.getByRole('button', { name: 'Next: OS' }).isDisabled(), true);
    assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    await page.getByLabel('Name', { exact: true }).fill('Ubuntu workspace');
    const folder = page.getByPlaceholder('Choose a folder or enter a path');
    for (const invalid of ['/', '///', '/./', '~/projects', 'projects', '/projects/../other']) {
      await folder.fill(invalid);
      assert.equal(await folder.getAttribute('aria-invalid'), 'true');
      assert.equal(await page.getByRole('button', { name: 'Next: OS' }).isDisabled(), true);
      await page.getByLabel('Name', { exact: true }).press('Enter');
      assert.equal(await page.getByRole('tab', { name: /Location/ }).getAttribute('aria-selected'), 'true');
      assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    }
    await folder.fill('/');
    await page.getByRole('alert').filter({ hasText: 'not the filesystem root' }).waitFor();
    await page.getByRole('button', { name: 'Browse', exact: true }).click();
    const picker = page.getByRole('dialog', { name: 'Choose project folder' });
    await picker.getByRole('alert').filter({ hasText: 'not the filesystem root' }).waitFor();
    assert.equal(await picker.getByRole('button', { name: 'Use this folder' }).isDisabled(), true);
    await picker.getByRole('button', { name: 'projects', exact: true }).click();
    await picker.getByRole('button', { name: 'Use this folder' }).click();
    assert.equal(await folder.inputValue(), '/projects');
    assert.equal(await folder.getAttribute('aria-invalid'), 'false');
    await folder.fill('/projects/test');
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
