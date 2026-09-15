import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('only the affected workspace offers recovery, with themed confirmation and progress', {timeout: 60000}, async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{name: 'recovery-fixture', enforce: 'pre',
      resolveId(id, importer) { if (id === '\0recovery-fixture') return id; if (id === './env' && importer?.endsWith('/lib/api.ts')) return '\0fixture-env'; },
      load(id) {
        if (id === '\0fixture-env') return 'export const API_BASE_URL = "/api/v1";';
        if (id === '\0recovery-fixture') return `import React from 'react'; import {createRoot} from 'react-dom/client';
          import {MemoryRouter} from 'react-router-dom'; import {WorkspaceProvider} from '/src/lib/workspace-context.tsx';
          import {WorkspacesPanel} from '/src/components/runtime/WorkspacesPanel.tsx'; import '/src/index.css';
          createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,
          React.createElement(WorkspaceProvider,{instanceName:'test'},React.createElement(WorkspacesPanel))));`;
      },
      configureServer(s) { s.middlewares.use(async (req,res,next) => {
        if (req.url !== '/instances/test/recovery-preview') return next();
        res.setHeader('Content-Type','text/html'); res.end(await s.transformIndexHtml(req.url,
          '<html class="dark"><body><div id="root"></div><script type="module" src="/@id/__x00__recovery-fixture"></script></body></html>'));
      }); }
    },react()], server:{host:'127.0.0.1',port:0},logLevel:'error' });
  let browser;
  try {
    await server.listen(); browser = await chromium.launch({headless:true, channel:process.env.PLAYWRIGHT_CHANNEL || 'chrome'});
    const page = await browser.newPage({viewport:{width:1100,height:800}});
    const errors=[]; page.on('pageerror',error => errors.push(error.message));
    let recovering=false, fail=false, posts=0;
    const rows=['stopped','unavailable','affected'].map(id => ({id,name:id,machine_id:'mac',directory:'/project',development_tools:[],container_state:'checking',recovery_available:false}));
    await page.route('**/api/v1/**',async route => {
      const req=route.request(), url=new URL(req.url());
      if (url.pathname.endsWith('/recover')) {
        posts++; assert.equal(url.pathname,'/api/v1/instances/test/workspaces/affected/recover');
        assert.deepEqual(req.postDataJSON(),{confirmed:true});
        if (fail) { await route.fulfill({status:503,json:{detail:'Could not confirm VM power-off. Disk unchanged.'}}); return; }
        recovering=true; await route.fulfill({status:202,json:{state:'recovering'}}); return;
      }
      if (url.pathname.endsWith('/machines')) { await route.fulfill({json:[{id:'mac',name:'mac-primary'}]}); return; }
      if (url.pathname.endsWith('/status')) {
        const id=url.pathname.split('/').at(-2);
        await route.fulfill({json:{...rows.find(row=>row.id===id),container_state:id==='affected'?(recovering?'recovering':'failed'):id,
          recovery_available:id==='affected'&&!recovering,container_message:recovering&&id==='affected'?'Backing up and checking disk…':null}}); return;
      }
      await route.fulfill({json:rows});
    });
    await page.goto(server.resolvedUrls.local[0]+'instances/test/recovery-preview');
    const recover=page.getByRole('button',{name:'Recover',exact:true}); await recover.waitFor();
    assert.equal(await recover.count(),1);
    for(const name of ['stopped','unavailable']) assert.equal(await page.locator('article').filter({has:page.getByRole('heading',{name,exact:true})}).getByRole('button',{name:'Recover',exact:true}).count(),0);
    await recover.click(); const dialog=page.getByRole('dialog');
    await dialog.getByRole('heading',{name:'Recover workspace'}).waitFor(); assert.equal(posts,0);
    await dialog.getByRole('button',{name:'Close',exact:true}).click(); await dialog.waitFor({state:'hidden'});
    await recover.click(); await page.mouse.click(5,5); await dialog.waitFor({state:'hidden'});
    await recover.click(); await page.keyboard.press('Escape'); await dialog.waitFor({state:'hidden'});
    await recover.click();
    assert.notEqual(await dialog.evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
    await page.screenshot({path:'/tmp/sentinel-workspace-recovery-dialog.png'});
    fail=true; await dialog.getByRole('button',{name:'Recover and restart'}).click();
    await dialog.getByRole('alert').waitFor(); assert.equal(await dialog.isVisible(),true);
    assert.equal(await dialog.getByRole('button',{name:'Recover and restart'}).isEnabled(),true);
    fail=false; await dialog.getByRole('button',{name:'Recover and restart'}).click();
    await dialog.waitFor({state:'hidden'});
    const affected=page.locator('article').filter({has:page.getByRole('heading',{name:'affected',exact:true})});
    await affected.getByText('Recovering',{exact:true}).waitFor();
    assert.equal(await affected.getByRole('button',{name:'Remove',exact:true}).isDisabled(),true);
    assert.equal(await recover.count(),0); assert.equal(posts,2); assert.deepEqual(errors,[]);
  } finally { await browser?.close(); await server.close(); }
});
