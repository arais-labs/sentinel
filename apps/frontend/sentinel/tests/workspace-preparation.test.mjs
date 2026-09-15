import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('preparation displays download progress, failure, retry and optional diagnostics', { timeout: 60000 }, async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{ name: 'preparation-fixture',
      resolveId(id) { if (id === '\0preparation-fixture') return id; },
      load(id) {
        if (id === '\0preparation-fixture') return `
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {WorkspacePreparation} from '/src/components/WorkspacePreparation.tsx';
          import '/src/index.css';
          window.sentinelDesktop = {getDevMode:async () => false, onDevModeChanged:listener => { window.setDevMode = listener; return () => {}; }, getLogs: async () => [{service:'manager',at:new Date().toISOString(),line:'Download started'}]};
          const root = createRoot(document.getElementById('root'));
          window.showPreparation = props => root.render(React.createElement(WorkspacePreparation, {...props, onRetry: () => window.retried = true}));
          window.showPreparation({preparing:true});`;
      },
      configureServer(s) { s.middlewares.use(async (req, res, next) => {
        if (req.url !== '/preparation') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(await s.transformIndexHtml(req.url, '<html class="dark"><body><div id="root"></div><script type="module" src="/@id/__x00__preparation-fixture"></script></body></html>'));
      }); },
    }, react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error',
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/preparation`);
    await page.getByRole('heading', {name:'Preparing Sentinel'}).waitFor();
    assert.equal(await page.getByText('Local services', {exact:true}).count(), 0);
    await page.evaluate(() => window.showPreparation({preparing:true,progress:{phase:'download',message:'Downloading your app…',fractionComplete:0.42}}));
    await page.getByRole('progressbar').waitFor();
    assert.equal(await page.getByRole('progressbar').getAttribute('value'), '0.42');
    const barStyle = await page.getByRole('progressbar').evaluate(element => ({
      height: getComputedStyle(element).height,
      radius: getComputedStyle(element).borderRadius,
      appearance: getComputedStyle(element).appearance,
    }));
    assert.deepEqual(barStyle, {height:'6px', radius:'999px', appearance:'none'});
    assert.equal(await page.getByRole('button', {name:'Show details'}).count(), 0);
    await page.evaluate(() => window.setDevMode(true));
    await page.getByRole('button', {name:'Show details'}).waitFor();
    for (const dark of [true, false]) {
      await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), dark);
      const details = page.getByRole('button', {name:'Show details'});
      const style = await details.evaluate(element => {
        const style = getComputedStyle(element);
        return {radius:style.borderRadius, fontSize:style.fontSize, color:style.color};
      });
      assert.deepEqual(style, {radius:'999px', fontSize:'10px', color:dark ? 'rgb(244, 244, 245)' : 'rgb(15, 23, 42)'});
      await page.waitForFunction(dark => getComputedStyle(document.querySelector('.workspace-preparation-action')).backgroundColor === (dark ? 'rgb(17, 17, 19)' : 'rgb(248, 250, 252)'), dark);
      if (process.env.PREPARATION_SCREENSHOTS) await page.screenshot({path:`${process.env.PREPARATION_SCREENSHOTS}/preparation-${dark ? 'dark' : 'light'}.png`});
    }
    await page.evaluate(() => window.showPreparation({preparing:true,error:'Connection unavailable'}));
    await page.getByRole('alert').waitFor();
    assert.equal(await page.getByRole('progressbar').count(), 0);
    await page.getByRole('button', {name:'Retry',exact:true}).click();
    assert.equal(await page.evaluate(() => window.retried), true);
    await page.getByRole('button', {name:'Show details'}).click();
    await page.getByText('Download started', {exact:true}).waitFor();
    await page.getByRole('button', {name:'Hide details'}).click();
    assert.equal(await page.locator('#preparation-diagnostics').getAttribute('aria-hidden'), 'true');
    await page.evaluate(() => window.setDevMode(false));
    await page.getByRole('button', {name:'Show details'}).waitFor({state:'detached'});
    assert.equal(await page.locator('#preparation-diagnostics').count(), 0);
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await page.locator('.workspace-preparation-step').evaluate(el => getComputedStyle(el).animationName), 'none');
  } finally { await browser?.close(); await server.close(); }
});
