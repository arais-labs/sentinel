import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

async function waitForPreview(page, name) {
  const figure = page.getByRole('figure', { name: `${name} preview`, exact: true });
  await figure.waitFor();
  await page.waitForFunction(label => {
    const figure = document.querySelector(`figure[aria-label="${label} preview"]`);
    const tooltip = figure?.closest('[role="tooltip"]');
    if (!tooltip || !figure) return false;
    for (let element = figure; element; element = element.parentElement) {
      const style = getComputedStyle(element);
      if (style.visibility !== 'visible' || Number(style.opacity) !== 1) return false;
      if (element === tooltip) break;
    }
    const bounds = figure.getBoundingClientRect();
    return bounds.width > 0 && bounds.height > 0;
  }, name);
  return figure;
}

test('workspace creation separates OS, desktop and tools and submits each choice', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    const { workspaceSaveError } = await server.ssrLoadModule('/src/components/runtime/workspaceValidation.ts');
    const { ApiError } = await server.ssrLoadModule('/src/lib/api.ts');
    const { workspaceDesktops, workspaceDesktopChoices } = await server.ssrLoadModule('/src/components/runtime/workspaceDesktops.ts');
    assert.deepEqual(workspaceDesktopChoices, ['none', 'xfce', 'lxqt', 'gnome', 'plasma']);
    assert.equal(workspaceDesktopChoices.includes('weston'), false, 'Internal diagnostic compositor is not a workspace choice');
    for (const id of workspaceDesktopChoices) {
      assert.ok(workspaceDesktops[id].name && workspaceDesktops[id].summary);
      if (id !== 'none') {
        assert.ok(workspaceDesktops[id].capture?.src, 'Selectable desktops need actual captured previews');
        assert.ok(workspaceDesktops[id].capture?.distribution, 'Each capture identifies its actual distribution');
      }
    }
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
          { id: 'ubuntu', name: 'Ubuntu 26.04 LTS', detail: 'All stacks and accelerated desktop', tools: ['git', 'python'] },
          { id: 'debian', name: 'Debian 13', detail: 'All stacks and accelerated desktop', tools: ['git', 'python'] }],
        tools: [{ id: 'git', name: 'Git', detail: 'Version control', category: 'Essentials' },
          { id: 'python', name: 'Python', detail: 'Python runtime', category: 'Languages' }],
        stacks: [{ id: 'python', name: 'Python', description: 'Python stack', tools: ['python'] }],
      }), { headers: { 'Content-Type': 'application/json' } });
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html`);
    await page.getByRole('dialog').waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), true);
    assert.notEqual(await page.getByRole('dialog').evaluate(element => getComputedStyle(element).backgroundColor), 'rgba(0, 0, 0, 0)', 'Fixture must use production theme styles');
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
    assert.equal(await picker.getByRole('button', { name: 'Choose Root', exact: true }).isDisabled(), true);
    await picker.getByRole('button', { name: 'projects', exact: true }).click();
    await picker.getByRole('button', { name: 'Choose projects', exact: true }).click();
    assert.equal(await folder.inputValue(), '/projects');
    assert.equal(await folder.getAttribute('aria-invalid'), 'false');
    await folder.fill('/projects/test');
    await page.getByLabel('Name', { exact: true }).press('Enter');
    assert.equal(await selector.isChecked(), true);
    await page.locator('label').filter({ has: page.getByRole('radio', { name: /Ubuntu 26/ }) }).click();
    await page.getByRole('button', { name: 'Next: Desktop' }).click();
    assert.equal(await page.getByRole('radio', { name: /None/ }).isChecked(), true);
    const options = page.locator('.workspace-desktop-options');
    for (const id of [...workspaceDesktopChoices.filter(id => id !== 'none'), 'none']) {
      const { name: label, capture } = workspaceDesktops[id];
      const preview = id === 'none' ? 'No desktop' : label;
      await options.locator('label').filter({ has: page.getByRole('radio', { name: new RegExp(label) }) }).hover();
      const figure = await waitForPreview(page, preview);
      if (capture) {
        const image = figure.getByRole('img');
        await image.waitFor();
        await image.evaluate(image => image.decode());
        assert.deepEqual(await image.evaluate(image => [image.naturalWidth, image.naturalHeight]), [1280, 800], 'Preview must load a real desktop capture');
        assert.ok((await image.getAttribute('alt')).endsWith(`on ${capture.distribution}`));
        assert.ok((await figure.locator('figcaption').textContent()).includes(`Shown on ${capture.distribution};`));
      } else {
        assert.equal(await figure.getByRole('img').count(), 0, 'Do not invent previews before real desktop qualification');
      }
      const bounds = await figure.boundingBox();
      const modal = await page.getByRole('dialog').boundingBox();
      assert.ok(bounds && modal && bounds.y >= modal.y && bounds.y + bounds.height <= modal.y + modal.height, 'Hover preview must fit without scrolling');
      assert.equal(await page.getByRole('radio', { name: /None/ }).isChecked(), true, 'Previewing must not select a desktop');
    }
    await page.getByRole('button', { name: 'Next: Browser' }).hover();
    await page.getByRole('figure', { name: 'No desktop preview', exact: true }).waitFor({ state: 'hidden' });
    await page.keyboard.press('Tab');
    await page.getByRole('radio', { name: /XFCE · X11/ }).focus();
    await waitForPreview(page, 'XFCE · X11');
    assert.equal(await page.getByRole('radio', { name: /None/ }).isChecked(), true);
    await page.keyboard.press('Escape');
    await page.getByRole('figure', { name: 'XFCE · X11 preview', exact: true }).waitFor({ state: 'hidden' });
    assert.equal(await page.getByRole('radio', { name: /LXQt · Wayland/ }).isEnabled(), true);
    await options.locator('label').filter({ has: page.getByRole('radio', { name: /LXQt · Wayland/ }) }).click();
    assert.equal(await page.getByRole('radio', { name: /LXQt · Wayland/ }).isChecked(), true);
    await options.locator('label').filter({ has: page.getByRole('radio', { name: /LXQt · Wayland/ }) }).hover();
    await waitForPreview(page, 'LXQt · Wayland');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.keyboard.press('Tab');
    await page.getByRole('radio', { name: /LXQt · Wayland/ }).focus();
    await waitForPreview(page, 'LXQt · Wayland');
    await page.waitForFunction(() => {
      const figure = document.querySelector('figure[aria-label="LXQt · Wayland preview"]');
      const bounds = figure?.getBoundingClientRect();
      return bounds && bounds.x >= 0 && bounds.right <= innerWidth;
    });
    const previewBounds = await page.getByRole('figure', { name: 'LXQt · Wayland preview', exact: true }).boundingBox();
    assert.ok(previewBounds && previewBounds.x >= 0 && previewBounds.x + previewBounds.width <= 390);
    if (process.env.SENTINEL_PREVIEW_SCREENSHOTS) {
      await page.screenshot({ path: `${process.env.SENTINEL_PREVIEW_SCREENSHOTS}-narrow.png` });
    }
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.keyboard.press('Tab');
    await page.getByRole('radio', { name: /LXQt · Wayland/ }).focus();
    await waitForPreview(page, 'LXQt · Wayland');
    if (process.env.SENTINEL_PREVIEW_SCREENSHOTS) {
      await page.waitForFunction(() => {
        const bounds = document.querySelector('figure[aria-label="LXQt · Wayland preview"]')?.getBoundingClientRect();
        return bounds && bounds.width > 400;
      });
      await page.screenshot({ path: `${process.env.SENTINEL_PREVIEW_SCREENSHOTS}-desktop.png` });
    }
    await page.getByRole('button', { name: 'Next: Browser' }).click();
    assert.equal(await page.getByRole('radio', { name: /^Chromium/ }).isChecked(), true);
    assert.equal(await page.getByRole('radio', { name: /^Google Chrome/ }).isEnabled(), true);
    await page.locator('label').filter({ has: page.getByRole('radio', { name: /^Firefox/ }) }).click();
    assert.equal(await page.getByRole('radio', { name: /^Firefox/ }).isChecked(), true);
    await page.getByRole('button', { name: 'Next: Stacks' }).click();
    assert.equal(await page.getByRole('button', { name: /Desktop stack/ }).count(), 0);
    await page.getByRole('button', { name: 'Next: Tools' }).click();
    await page.getByRole('checkbox', { name: /Python/ }).check();
    await page.getByRole('button', { name: 'Next: Resources' }).click();
    assert.equal(await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).inputValue(), '2');
    assert.equal(await page.getByRole('button', { name: 'Use suggested' }).count(), 0);
    await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).fill('1');
    await page.getByRole('button', { name: 'Use suggested' }).click();
    assert.equal(await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).inputValue(), '2');
    await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).fill('4');
    assert.equal(await page.getByRole('button', { name: 'Use suggested' }).count(), 0);
    assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).fill('0');
    assert.equal(await page.getByRole('button', { name: 'Create workspace' }).isDisabled(), true);
    assert.equal(await page.evaluate(() => window.savedWorkspace), undefined);
    await page.getByRole('spinbutton', { name: 'CPUs', exact: true }).fill('2');
    await page.getByRole('button', { name: 'Create workspace' }).click();
    const value = await page.evaluate(() => window.savedWorkspace);
    assert.equal(value.distribution, 'ubuntu');
    assert.equal(value.desktop, 'lxqt');
    assert.equal(value.browser, 'firefox');
    assert.deepEqual(value.development_tools, ['git', 'python']);
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html?edit`);
    const toolsTab = page.getByRole('tab', { name: 'Tools', exact: true });
    assert.equal(await toolsTab.isEnabled(), true);
    await toolsTab.click();
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    assert.equal((await page.evaluate(() => window.savedWorkspace)).name, 'Existing');
    for (const desktop of workspaceDesktopChoices.filter(id => id !== 'none')) {
      const label = new RegExp(workspaceDesktops[desktop].name);
      await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html?edit&desktop=${desktop}`);
      await page.getByRole('tab', { name: 'Desktop', exact: true }).click();
      assert.equal(await page.getByRole('radio', { name: label }).isChecked(), true);
      await page.getByRole('button', { name: 'Save changes', exact: true }).click();
      assert.equal((await page.evaluate(() => window.savedWorkspace)).desktop, desktop);
    }
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/workspace-distribution.html?edit&desktop=lxqt&distribution=ubuntu`);
    await page.getByRole('tab', { name: 'Desktop', exact: true }).click();
    assert.equal(await page.getByRole('radio', { name: /LXQt · Wayland/ }).isChecked(), true);
    assert.equal(await page.getByRole('radio', { name: /LXQt · Wayland/ }).isEnabled(), true);
    await page.getByRole('button', { name: 'Save changes', exact: true }).click();
    assert.equal((await page.evaluate(() => window.savedWorkspace)).desktop, 'lxqt');
    await page.getByRole('tab', { name: 'OS', exact: true }).click();
    await page.locator('label').filter({ has: page.getByRole('radio', { name: /Debian/ }) }).click();
    assert.match(await page.getByRole('alert').innerText(), /requires reinstalling this workspace/);
    await page.getByRole('button', { name: 'Review reinstall' }).click();
    assert.equal((await page.evaluate(() => window.savedWorkspace)).distribution, 'debian');
  } finally { await browser?.close(); await server.close(); }
});
