import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('session approval controls grant, persist, revoke and stay usable in both themes', { timeout: 60000 }, async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{ name: 'session-approval-fixture', enforce: 'pre',
      resolveId(id, importer) {
        if (id === '\0session-approval-fixture') return id;
        if (id === './env' && importer?.endsWith('/lib/api.ts')) return '\0session-approval-env';
      },
      load(id) {
        if (id === '\0session-approval-env') return 'export const API_BASE_URL = "/api/v1";';
        if (id === '\0session-approval-fixture') return `
          import React from 'react'; import {createRoot} from 'react-dom/client';
          import {SessionPreviewApprovals} from '/src/components/session/SessionPreviewApprovals.tsx';
          import {SessionPermissions} from '/src/components/session/SessionPermissions.tsx';
          import {ApprovalActions} from '/src/components/session/ApprovalActions.tsx';
          import {StreamToolCard} from '/src/components/session/StreamToolCard.tsx';
          import {approvalRefFromMetadata} from '/src/lib/approvals.ts';
          import '/src/index.css';
          window.parseApproval = approvalRefFromMetadata;
          window.renderLiveApproval = () => {
            const host = document.createElement('section'); host.setAttribute('aria-label','Live chat approval'); document.body.append(host);
            createRoot(host).render(React.createElement(StreamToolCard,{
              sessionId:'conversation', active:true, resolvingApprovalKey:null,
              call:{id:'live-call',name:'git',argumentsJson:'{}',outputJson:'',complete:false,isError:false,contentIndex:0,
                metadata:{approval:{provider:'git',approval_id:'live-approval',pending:true,action:'git.write'}}},
              onResolveApproval:(approval,decision,scope)=>{window.liveDecision={approval,decision,scope};}
            }));
          };
          createRoot(document.getElementById('root')).render(React.createElement(React.Fragment,null,
            React.createElement(SessionPreviewApprovals,{instanceName:'test',sessionId:'conversation'}),
            React.createElement(SessionPermissions,{instanceName:'test',sessionId:'conversation'}),
            React.createElement('section',{'aria-label':'Sessionless approval'},React.createElement(ApprovalActions,{action:'git.write',onResolve:()=>{}}))));`;
      },
      configureServer(s) { s.middlewares.use(async (req, res, next) => {
        if (req.url !== '/approval-preview') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(await s.transformIndexHtml(req.url, '<html class="dark"><body><div id="root" style="width:min(620px,100%);padding:16px;box-sizing:border-box"></div><script type="module" src="/@id/__x00__session-approval-fixture"></script></body></html>'));
      }); },
    }, react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error',
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 660, height: 700 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let grants = [];
    let items = ['git.write', 'git.delete'].map((action, index) => ({ provider:'git', approval_id:String(index), action, session_id:'conversation', pending:true, can_resolve:true }));
    const decisions = [];
    let failRevoke = true;
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      assert.ok(pathname.startsWith('/api/v1/instances/test/approvals'));
      if (request.method() === 'POST') {
        const body = request.postDataJSON();
        decisions.push(body);
        const id = pathname.split('/').at(-2);
        const item = items.find(item => item.approval_id === id);
        if (body.scope === 'session') grants = [{ id:'grant-1', session_id:'conversation', action:item.action }];
        items = items.filter(item => item.approval_id !== id);
        await route.fulfill({json:{}});
      } else if (request.method() === 'DELETE') {
        assert.equal(pathname, '/api/v1/instances/test/approvals/sessions/conversation/grants/grant-1');
        if (failRevoke) { failRevoke = false; await route.fulfill({status:500,json:{detail:'Temporary failure'}}); }
        else { grants = []; await route.fulfill({status:204}); }
      } else await route.fulfill({json:pathname.endsWith('/grants') ? grants : {items}});
    });
    const url = server.resolvedUrls.local[0] + 'approval-preview';
    await page.goto(url);
    const write = page.locator('.session-preview-approval').filter({hasText:'git.write'});
    await write.getByRole('button', {name:'Allow for this session',exact:true}).click();
    await page.getByRole('button', {name:'Revoke git.write',exact:true}).waitFor();
    assert.deepEqual(decisions, [{scope:'session'}]);
    assert.equal(await page.getByRole('region', {name:'Sessionless approval'}).getByRole('button', {name:'Allow for this session',exact:true}).count(), 0);
    const parsed = await page.evaluate(() => window.parseApproval({approval:{provider:'git',approval_id:'x',pending:true,session_id:'conversation',action:'git.write'}}));
    assert.equal(parsed.sessionId, 'conversation');
    assert.equal(parsed.action, 'git.write');
    // Reopening the UI reloads persistent permissions from the backend.
    await page.reload();
    await page.getByRole('button', {name:'Revoke git.write',exact:true}).waitFor();
    for (const dark of [true, false]) {
      await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), dark);
      await page.setViewportSize({width:320,height:720});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const button = page.getByRole('button', {name:'Allow for this session',exact:true});
      assert.notEqual(await button.evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)');
      if (process.env.SENTINEL_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.SENTINEL_SCREENSHOT_DIR}/session-permissions-${dark ? 'dark' : 'light'}.png`,fullPage:true});
    }
    await page.getByRole('button', {name:'Revoke git.write',exact:true}).click();
    await page.getByRole('alert').filter({hasText:'Could not revoke'}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Revoke git.write',exact:true}).count(), 1);
    await page.getByRole('button', {name:'Revoke git.write',exact:true}).click();
    await page.getByText('No actions automatically approved for this conversation.').waitFor();
    await page.locator('.session-preview-approval').filter({hasText:'git.delete'}).getByRole('button', {name:'Approve once',exact:true}).click();
    assert.deepEqual(decisions, [{scope:'session'},{scope:'once'}]);
    assert.deepEqual(grants, []);
    // The active chat card must expose session approval without opening its
    // inspector or navigating to the approvals tab. Older events use sessionId.
    await page.evaluate(() => window.renderLiveApproval());
    const live = page.getByRole('region', {name:'Live chat approval'});
    await live.getByRole('button', {name:'Allow for this session',exact:true}).click();
    const liveDecision = await page.evaluate(() => window.liveDecision);
    assert.equal(liveDecision.approval.approvalId, 'live-approval');
    assert.equal(liveDecision.scope, 'session');
    assert.equal(liveDecision.decision, 'approve');
    await live.getByRole('button', {name:'Approve once',exact:true}).click();
    assert.equal(await page.evaluate(() => window.liveDecision.scope), 'once');
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
