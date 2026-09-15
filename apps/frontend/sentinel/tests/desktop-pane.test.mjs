import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('desktop uses shared overflow rows and pane focus mode', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const server = await createServer({root,configFile:false,plugins:[react()],server:{host:'127.0.0.1',port:0},logLevel:'error'});
  let browser;
  try {
    browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
    await server.listen();
    const page = await browser.newPage({viewport:{width:550,height:800}});
    page.setDefaultTimeout(10000);
    const errors=[];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const listeners = new Set(); const sockets = new Map(); window.socketOpens=[];
      window.documentIdentity = crypto.randomUUID();
      window.revalidateWorkspace = () => {
        window.workspaceDelay = 200;
        for (const [id, url] of sockets) if (url.endsWith('/stream'))
          for (const fn of listeners) fn({id,type:'message',data:JSON.stringify({type:'connected',run_active:false,history_via_http:true,panes:[]})});
      };
      window.socketSnapshot = () => ({open:sockets.size,listeners:listeners.size});
      window.disconnectBackend = () => { for(const [id] of sockets) for(const fn of listeners) fn({id,type:'close',code:1006,reason:'backend restarting'}); sockets.clear(); };
      window.sentinelDesktop={
        onSocketEvent(fn) { listeners.add(fn);return () => listeners.delete(fn); },
        async socketOpen(id,url) {sockets.set(id,url);window.socketOpens.push(url);setTimeout(()=>{for(const fn of listeners){fn({id,type:'open'});fn({id,type:'message',data:JSON.stringify({type:'connected',run_active:false,history_via_http:true,panes:[]})});}},20);},
        socketClose(id,code,reason){sockets.delete(id);for(const fn of listeners) fn({id,type:'close',code,reason});},socketSend(){},
      };
      window.fetch=async input=>{
        const path=String(input);let payload={};
        if (path.endsWith('/workspace') && window.workspaceDelay) await new Promise(resolve => setTimeout(resolve, window.workspaceDelay));
        if(path.includes('/messages')) payload={items:[{id:'message-a',session_id:'session-a',role:'user',content:'HMR retained message',metadata:{},created_at:'2026-09-10T00:00:00Z'}],has_more:false};
        else if(path.includes('/sessions?')) payload={items:[{id:'session-a',title:'HMR test',status:'active'}]};
        else if(path.endsWith('/instances')) payload=[{name:'test',status:'running'}];
        else if(path.endsWith('/workspaces')) payload=[];
        else if(path.endsWith('/workspace')) payload={id:'workspace-a',name:'Sample Ubuntu',container_state:'running',development_tools:['desktop']};
        else if(path.includes('/runtime/live-view')) payload={state:'running',available:true,geometry:'1920x1200'};
        else if(path.includes('/models')) payload={models:[]};
        else if(path.includes('/agent-modes')) payload={modes:[]};
        else if(path.includes('/sub-agents')) payload=[];
        else if(path.includes('/runtime/files')) payload={items:[]};
        return new Response(JSON.stringify(payload),{status:200,headers:{'Content-Type':'application/json'}});
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/desktop-pane.html`);
    await page.waitForFunction(()=>window.openDesktop);
    await page.evaluate(()=>window.openDesktop());
    const header=page.locator('[data-tour-pane="desktop"]');
    await header.waitFor();
    await page.waitForTimeout(500);
    await page.locator('[data-session-view="session-a"]').waitFor();
    await page.evaluate(() => {
      window.initialDesktop = document.querySelector('.workspace-desktop');
      window.initialChat = document.querySelector('[data-session-view="session-a"]');
    });
    await page.evaluate(() => window.revalidateWorkspace());
    await page.waitForTimeout(75);
    assert.equal(await page.evaluate(() => document.querySelector('.workspace-desktop') === window.initialDesktop), true, 'Workspace revalidation must not unmount the desktop while its response is pending');
    await page.waitForTimeout(250);
    assert.equal(await page.evaluate(() => document.querySelector('.workspace-desktop') === window.initialDesktop), true, 'An unchanged workspace must retain its desktop after reconnect');
    assert.equal(await page.locator('.focus-chat-surface').evaluate(element => getComputedStyle(element).viewTransitionName), 'none');
    await page.locator('[data-tour-pane="sessions"]').getByRole('button',{name:'Enter focus mode',exact:true}).click();
    await page.waitForFunction(() => window.focusedPane() !== null);
    assert.equal(await page.getByRole('button',{name:'Exit Focus',exact:true}).count(),0);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => window.focusedPane() === null);
    await page.setViewportSize({width:550,height:800});
    await header.getByRole('button',{name:'More pane actions'}).click();
    const menu=page.getByRole('dialog',{name:'More pane actions'});
    await menu.waitFor();
    await menu.getByRole('button',{name:'Share desktop clipboard'}).click();
    assert.equal(await menu.getByRole('button',{name:'Share desktop clipboard'}).getAttribute('aria-pressed'),'false');
    await menu.getByRole('button',{name:'Refresh desktop status'}).waitFor();
    const rows=await menu.locator('button').evaluateAll(buttons=>buttons.map(b=>({text:b.textContent?.trim(),width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height,font:getComputedStyle(b).fontSize})));
    assert.ok(rows.every(row=>row.text && row.width>180 && row.height>=34 && row.font==='12px'),JSON.stringify(rows));
    assert.equal(await page.getByRole('button',{name:'Fullscreen',exact:true}).count(),0);
    await page.screenshot({path:'/tmp/sentinel-desktop-overflow.png'});
    await page.keyboard.press('Escape');
    await header.getByRole('button',{name:'Enter focus mode',exact:true}).click();
    await page.locator('.workspace-focused').waitFor();
    assert.equal(await page.getByRole('button',{name:'Exit Focus',exact:true}).count(),0);
    assert.equal(await header.isVisible(),false);
    assert.equal(await page.locator('.workspace-focused .workspace-desktop').count(),1);
    assert.equal(await page.locator('.workspace-desktop.is-fullscreen').count(),0);
    await page.keyboard.press('Meta+f');
    await header.waitFor({state:'visible'});
    assert.equal(await page.evaluate(() => document.querySelector('.workspace-desktop') === window.initialDesktop && document.querySelector('[data-session-view="session-a"]') === window.initialChat), true, 'Focus must preserve both pane contents');
    assert.equal(await page.locator('[data-session-view="session-a"]').isVisible(), true);
    assert.equal(await page.locator('.workspace-desktop').isVisible(), true);
    await header.getByRole('button',{name:'More pane actions'}).click();
    assert.equal(await menu.getByRole('button',{name:'Share desktop clipboard'}).getAttribute('aria-pressed'),'false');
    assert.deepEqual(errors,[]);
  } finally {await browser?.close();await server.close();}
});
