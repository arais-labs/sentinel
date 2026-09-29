import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('Reinstall requires clear disk-erasure confirmation and never promises VM data preservation', {timeout:60000}, async () => {
  const server = await createServer({root:fileURLToPath(new URL('..',import.meta.url)),configFile:false,
    plugins:[{name:'reinstall-fixture',enforce:'pre',
      resolveId(id,importer){if(id==='\0reinstall-fixture')return id;if(id==='./env'&&importer?.endsWith('/lib/api.ts'))return '\0fixture-env';},
      load(id){
        if(id==='\0fixture-env')return 'export const API_BASE_URL="/api/v1";';
        if(id==='\0reinstall-fixture')return `import React from 'react';import{createRoot}from'react-dom/client';
          import{MemoryRouter}from'react-router-dom';import{WorkspaceProvider}from'/src/lib/workspace-context.tsx';
          import{WorkspacesPanel}from'/src/components/runtime/WorkspacesPanel.tsx';import'/src/index.css';
          createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,
          React.createElement(WorkspaceProvider,{instanceName:'test'},React.createElement(WorkspacesPanel))));`;
      },
      configureServer(s){s.middlewares.use(async(req,res,next)=>{
        if(req.url!=='/instances/test/reinstall-preview')return next();
        res.setHeader('Content-Type','text/html');res.end(await s.transformIndexHtml(req.url,
          '<html><body><div id="root"></div><script type="module" src="/@id/__x00__reinstall-fixture"></script></body></html>'));
      });},
    },react()],server:{host:'127.0.0.1',port:0},logLevel:'error'});
  let browser;
  try {
    await server.listen();browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
    const page=await browser.newPage();let posts=0,fail=true;
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    const row={id:'workspace',name:'Example',machine_id:'mac',directory:'/host/Project',development_tools:[],container_state:'stopped'};
    await page.route('**/api/v1/**',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.pathname.endsWith('/reinstall')){
        posts++;assert.deepEqual(request.postDataJSON(),{confirmed:true});
        await route.fulfill(fail?{status:409,json:{detail:'Stop active agents first'}}:{json:{...row,container_state:'preparing'}});return;
      }
      if(url.pathname.endsWith('/machines')){await route.fulfill({json:[{id:'mac',name:'Local'}]});return;}
      await route.fulfill({json:url.pathname.endsWith('/status')?row:[row]});
    });
    await page.goto(server.resolvedUrls.local[0]+'instances/test/reinstall-preview');
    const open=page.getByRole('button',{name:'Reinstall',exact:true});await open.click();
    const dialog=page.getByRole('dialog');await dialog.waitFor();
    const erase=dialog.getByRole('button',{name:'Erase Linux disk and reinstall'});
    assert.equal(await erase.isDisabled(),true);assert.equal(posts,0);
    const content=await dialog.innerText();
    assert.match(content,/permanently erases its private Linux disk/);
    assert.match(content,/VM-only files, installed packages, browser profiles, and desktop settings/);
    assert.match(content,/no automatic backup/);
    assert.match(content,/mounted project folder is kept: \/host\/Project/);
    await dialog.getByRole('button',{name:'Cancel',exact:true}).click();await dialog.waitFor({state:'hidden'});assert.equal(posts,0);
    await open.click();await dialog.getByRole('checkbox').check();await erase.click();
    await dialog.getByRole('alert').waitFor();assert.equal(posts,1);assert.equal(await erase.isEnabled(),true);
    fail=false;await erase.click();await dialog.waitFor({state:'hidden'});
    assert.equal(posts,2);assert.deepEqual(errors,[]);
  } finally {await browser?.close();await server.close();}
});
