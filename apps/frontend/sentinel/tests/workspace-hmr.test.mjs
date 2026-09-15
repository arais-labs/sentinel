import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('chat retains its selected session across hot reload', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const server = await createServer({root,configFile:false,plugins:[react()],server:{host:'127.0.0.1',port:0},logLevel:'error'});
  let browser;
  try {
    browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
    await server.listen();
    const page = await browser.newPage({viewport:{width:1200,height:800}});
    const errors=[];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
      const listeners = new Set(); const sockets = new Map(); window.socketOpens=[];
      window.documentIdentity = crypto.randomUUID();
      window.socketSnapshot = () => ({open:sockets.size,listeners:listeners.size});
      window.disconnectBackend = () => { for(const [id] of sockets) for(const fn of listeners) fn({id,type:'close',code:1006,reason:'backend restarting'}); sockets.clear(); };
      window.sentinelDesktop={
        onSocketEvent(fn) { listeners.add(fn);return () => listeners.delete(fn); },
        async socketOpen(id,url) {sockets.set(id,url);window.socketOpens.push(url);setTimeout(()=>{for(const fn of listeners){fn({id,type:'open'});fn({id,type:'message',data:JSON.stringify({type:'connected',run_active:false,history_via_http:true,panes:[]})});}},20);},
        socketClose(id,code,reason){sockets.delete(id);for(const fn of listeners) fn({id,type:'close',code,reason});},socketSend(){},
      };
      window.fetch=async input=>{
        const path=String(input);let payload={};
        if(path.includes('/messages')) payload={items:[...Array.from({length:40},(_,i)=>({id:'history-'+i,session_id:'session-a',role:'user',content:'Historical message '+i,metadata:{},created_at:new Date(Date.UTC(2026,8,9,0,i)).toISOString()})),{id:'message-a',session_id:'session-a',role:'user',content:'HMR retained message',metadata:{},created_at:'2026-09-10T00:00:00Z'}],has_more:false};
        else if(path.includes('/sessions?')) payload={items:[{id:'session-a',title:'HMR test',status:'active'}]};
        else if(path.endsWith('/instances')) payload=[{name:'test',status:'running'}];
        else if(path.endsWith('/workspaces')) payload=[];
        else if(path.endsWith('/workspace')) payload=null;
        else if(path.includes('/models')) payload={models:[]};
        else if(path.includes('/agent-modes')) payload={modes:[]};
        else if(path.includes('/sub-agents')) payload=[];
        else if(path.includes('/runtime/files')) payload={items:[]};
        return new Response(JSON.stringify(payload),{status:200,headers:{'Content-Type':'application/json'}});
      };
    });
    const fixtureUrl = `${server.resolvedUrls.local[0]}tests/fixtures/workspace-hmr.html`;
    await page.goto(fixtureUrl);
    await page.locator('[data-session-view="session-a"]').waitFor();
    await page.getByText('HMR retained message', {exact:true}).waitFor();
    const chatHeader = page.locator('[data-tour-pane="sessions"]');
    assert.equal(await chatHeader.isVisible(), true);
    await chatHeader.getByRole('button', { name: 'Enter focus mode', exact: true }).click();
    await page.locator('.workspace-focused').waitFor();
    assert.equal(await page.getByRole('button', { name: 'Exit Focus', exact: true }).count(), 0);
    assert.equal(await chatHeader.isVisible(), false);
    await page.keyboard.press('Escape');
    await chatHeader.waitFor({ state: 'visible' });
    await page.getByRole('button', { name: 'Run settings', exact: true }).waitFor();
    await page.evaluate(() => window.hmrHarness.splitChat());
    await page.waitForFunction(() => Object.keys(window.hmrHarness.layout().panels).length === 2);
    const panelIds = await page.evaluate(() => Object.keys(window.hmrHarness.layout().panels));
    await page.locator('textarea').fill('Unsent draft survives reload');
    const initialDocument = await page.evaluate(() => window.documentIdentity);
    for(const file of ['src/pages/SessionsPage.tsx','src/pages/sessionStreaming.ts','src/components/workspace/WorkspacePane.tsx']) {
      const mod=server.moduleGraph.getModuleById(`${root}${file}`);assert.ok(mod,file);
      await server.reloadModule(mod);await page.waitForTimeout(1800);
      assert.equal(await page.locator('[data-session-view="session-a"]').count(),1,file);
      assert.equal(await page.locator('textarea').inputValue(), 'Unsent draft survives reload');
      assert.deepEqual(await page.evaluate(() => Object.keys(window.hmrHarness.layout().panels)), panelIds);
      assert.equal(await page.locator('[data-global-session]').getAttribute('data-global-session'), 'session-a');
      assert.equal(await page.evaluate(() => window.documentIdentity), initialDocument, 'HMR must not force a full reload');
      await page.getByText('HMR retained message', {exact:true}).waitFor();
      // One chat stream and one session-layout control connection.
      assert.ok((await page.evaluate(() => window.socketSnapshot())).open <= 11);
    }
    // Reloading the actual focused chat must restore its header and controls.
    await chatHeader.getByRole('button', { name: 'Enter focus mode', exact: true }).click();
    await page.locator('.workspace-focused').waitFor();
    assert.equal(await page.getByRole('button', { name: 'Exit Focus', exact: true }).count(), 0);
    // The fixture replaces the address with the session URL; load its entry again.
    await page.goto(fixtureUrl);
    await page.getByText('HMR retained message', {exact:true}).waitFor();
    await chatHeader.waitFor({ state: 'visible' });
    await page.getByRole('button', { name: 'Run settings', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Exit Focus', exact: true }).count(), 0);
    assert.deepEqual(await page.evaluate(() => Object.keys(window.hmrHarness.layout().panels)), panelIds);
    await page.evaluate(() => window.disconnectBackend());
    await page.waitForFunction(() => window.socketSnapshot().open === 2);
    await page.getByText('HMR retained message', {exact:true}).waitFor();
    assert.equal(await page.locator('[data-session-view="session-a"]').count(),1);
    await page.waitForTimeout(500);
    await page.evaluate(() => {
      const root = document.querySelector('[data-session-view="session-a"]');
      window.savedScroller = root.querySelector('.session-chat-messages');
      if (!window.savedScroller) throw new Error('No scrollable conversation');
      Object.assign(window.savedScroller.style,{height:'400px',maxHeight:'400px',overflowY:'auto',flex:'none'});
      window.savedScroller.scrollTop = window.savedScroller.scrollHeight;
    });
    await page.waitForTimeout(100);
    await page.evaluate(() => { window.savedScroller.scrollTop = 300; });
    await page.waitForTimeout(100);
    await page.evaluate(() => window.hmrHarness.select('session-b'));
    await page.locator('[data-session-view="session-b"]').waitFor();
    assert.equal(await page.locator('[data-session-view="session-a"]').count(),1);
    assert.equal(await page.locator('[data-session-view="session-a"]').isVisible(),false);
    await page.evaluate(() => { window.retainedChat = document.querySelector('[data-session-view="session-a"]'); });
    await page.evaluate(() => window.hmrHarness.select('session-a'));
    await page.locator('[data-session-view="session-a"]').waitFor({state:'visible'});
    assert.equal(await page.evaluate(() => window.retainedChat === document.querySelector('[data-session-view="session-a"]')),true);
    await page.waitForTimeout(500);
    assert.ok(Math.abs(await page.evaluate(() => window.savedScroller.scrollTop) - 300) < 3, 'reading position survives hiding: '+await page.evaluate(()=>JSON.stringify({top:savedScroller.scrollTop,h:savedScroller.scrollHeight,c:savedScroller.clientHeight})));
    await page.evaluate(() => { window.savedScroller.scrollTop=window.savedScroller.scrollHeight; });
    await page.waitForTimeout(100);
    await page.evaluate(() => window.hmrHarness.select('session-b'));
    await page.waitForTimeout(500);
    await page.evaluate(() => window.hmrHarness.select('session-a'));
    await page.waitForTimeout(500);
    assert.ok(await page.evaluate(() => { const el=window.savedScroller; return el.scrollHeight-el.clientHeight-el.scrollTop < 20; }), 'bottom stays pinned after switching');

    for (const id of Array.from({length:9},(_,i)=>`session-extra-${i}`)) {
      await page.evaluate(id => window.hmrHarness.select(id),id);
      await page.locator(`[data-session-view="${id}"]`).waitFor({state:'visible'});
    }
    assert.equal(await page.locator('[data-session-view]').count(),10);
    assert.equal(await page.locator('[data-session-view="session-b"]').count(),0);
    assert.ok((await page.evaluate(() => window.socketSnapshot())).open <= 11);
    assert.deepEqual(errors, []);
  } finally {await browser?.close();await server.close();}
});
