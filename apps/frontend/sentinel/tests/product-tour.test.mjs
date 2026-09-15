import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer, transformWithOxc } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// Real session shortcuts and tour UI; API calls are intercepted in the isolated
// browser. No real conversations, workspace settings, or user storage change.
const fixture = `
import React, {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import {GlobalSessionSelector} from '/src/components/session/GlobalSessionSelector.tsx';
import {ProductTour} from '/src/components/onboarding/ProductTour.tsx';
import {OnboardingHost} from '/src/components/onboarding/OnboardingHost.tsx';
import {useActiveSessionStore} from '/src/store/active-session-store.ts';
import {useFocusModeStore} from '/src/store/focus-mode-store.ts';
import '/src/index.css';
import '/src/components/session/chat-composer.css';
useActiveSessionStore.setState({byInstance:{test:'a'},recentByInstance:{test:['a','b']}});
function App(){
 const [open,setOpen]=useState(true),[text,setText]=useState('');
 const ref=useRef(null), active=useActiveSessionStore(s=>s.byInstance.test);
 useEffect(()=>{setText(''); if(useActiveSessionStore.getState().composerFocusRequest?.sessionId===active) requestAnimationFrame(()=>ref.current?.focus());},[active]);
 window.tourTest={reopen:()=>setOpen(true),active:()=>useActiveSessionStore.getState().byInstance.test,focus:useFocusModeStore};
 return <><header className="fixture-titlebar"><GlobalSessionSelector instanceName="test"/></header>
 <main className="fixture-workspace"><aside><span>Workspace</span>{['Chat','Terminal','Files','Workspaces','Memory','Triggers'].map(label=><button key={label}>{label}</button>)}</aside>
 <section className="focus-chat-surface"><header data-tour-pane="sessions">Chat</header><div className="fixture-history"><h1>A place for the work.</h1><p>Your conversations, tools, and project stay within reach.</p></div><div className="chat-composer-area"><form className="chat-composer" onSubmit={e=>e.preventDefault()}><textarea ref={ref} className="chat-composer-input" aria-label="Message" placeholder="Ask Sentinel anything…" value={text} onChange={e=>setText(e.target.value)}/><div className="chat-composer-toolbar"><button type="button" className="chat-composer-attach">Attach</button><span className="chat-composer-actions">↵ Send · ⇧ ↵ New line</span></div></form></div></section></main>
 {open&&<ProductTour instanceName="test" onClose={()=>setOpen(false)}/>}</>;
}
createRoot(document.getElementById('root')).render(<MemoryRouter initialEntries={['/instances/test/workspace']}>{new URLSearchParams(location.search).has('setup')?<OnboardingHost/>:<App/>}</MemoryRouter>);
`;

test('guide verifies real shortcut outcomes, cancellation, persistence, and responsive UI', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    plugins: [
      {
        name: 'tour-fixture',
        enforce: 'pre',
        resolveId(id) {
          if (/\/workspace-tabs(?:\.tsx)?$/.test(id)) return '\0tour-tabs';
          if (/(^|\/)env(?:\.ts)?$/.test(id)) return '\0tour-env';
          if (id === '\0tour-fixture.tsx') return id;
        },
        async load(id) {
          if (id === '\0tour-tabs')
            return "export const WORKSPACE_TAB_IDS=['sessions','terminal','files']; export const WORKSPACE_TABS = WORKSPACE_TAB_IDS.map(id => ({ id, label: id })); export const isWorkspaceTabId=id=>WORKSPACE_TAB_IDS.includes(id);";
          if (id === '\0tour-env')
            return "export const API_BASE_URL='/api/v1'; export const SESSION_DEBUG_PANEL_ENABLED=false; export const APP_VERSION='0.0.0-test';";
          if (id === '\0tour-fixture.tsx')
            return (await transformWithOxc(fixture, 'tour-fixture.tsx', { jsx: { runtime: 'automatic' } })).code;
        },
        configureServer(s) {
          s.middlewares.use(async (req, res, next) => {
            if (req.url?.split('?')[0] !== '/instances/test/workspace') return next();
            res.setHeader('Content-Type', 'text/html');
            res.end(
              await s.transformIndexHtml(
                req.url,
                '<html class="dark"><style>html,body,#root{height:100%;margin:0}body{font:14px Inter,system-ui,sans-serif;background:#0c0c0e}.fixture-titlebar{height:54px;display:flex;padding-right:20px}.global-session-selector{margin-right:10px}.fixture-workspace{height:calc(100% - 54px);display:flex;border-top:1px solid #ffffff09}.fixture-workspace>aside{width:150px;padding:24px 20px;border-right:1px solid #ffffff09;color:#90909c;display:flex;flex-direction:column;align-items:flex-start;gap:22px;font-size:12px}.fixture-workspace>aside>span{font-size:10px;letter-spacing:.1em;color:#60606b}.focus-chat-surface{display:flex;flex:1;min-width:0;flex-direction:column}.focus-chat-surface>header{height:42px;padding:12px 24px;color:#bdbdc8;border-bottom:1px solid #ffffff09}.fixture-history{padding:50px;flex:1}.fixture-history h1{font-size:25px;font-weight:500;color:#b0b0ba}.fixture-history p{margin-top:12px;color:#777784}.chat-composer-input{height:110px}.chat-composer-actions{font-size:10px;color:#6f6f7c}@media(max-width:500px){.fixture-workspace>aside{display:none}.fixture-history{padding:25px}.global-session-selector{margin-left:12px}}</style><div id="root"></div><script type="module" src="/@id/__x00__tour-fixture.tsx"></script></html>'
              )
            );
          });
        },
      },
      react(),
    ],
    server: { host: '127.0.0.1', port: 0 },
    logLevel: 'error',
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({
      headless: true,
      ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
    });
    const page = await browser.newPage({ viewport: { width: 1280, height: 880 } });
    page.setDefaultTimeout(12000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    let failNew = false,
      serial = 0,
      deletions = 0;
    let sessions = [
      { id: 'a', title: 'Project planning', has_unread: false },
      { id: 'b', title: 'Design review', has_unread: false },
    ];
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request(),
        url = new URL(request.url());
      let body = { items: [], total: 0 },
        status = 200;
      if (url.pathname.endsWith('/sessions') && request.method() === 'GET')
        body = { items: sessions, total: sessions.length };
      if (url.pathname.endsWith('/sessions') && request.method() === 'POST') {
        if (failNew) {
          status = 500;
          body = { detail: 'Test creation failure' };
        } else {
          body = { id: 'new-' + ++serial, title: 'New chat', has_unread: false };
          sessions = [body, ...sessions];
          await new Promise((resolve) => setTimeout(resolve, 120));
        }
      }
      if (url.pathname.endsWith('/close')) body = { status: 'closed' };
      if (request.method() === 'DELETE') deletions++;
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(`${server.resolvedUrls.local[0]}instances/test/workspace`);
    await page.getByRole('button', { name: 'Begin the guide' }).waitFor();
    await page.locator('.tour-chapters > button').first().hover();
    const chapterPadding = await page
      .locator('.tour-chapters > button')
      .first()
      .evaluate((button) => {
        const bounds = button.getBoundingClientRect(),
          number = button.querySelector('.tour-chapter-number').getBoundingClientRect();
        return number.left - bounds.left;
      });
    assert.ok(chapterPadding >= 14, 'Chapter numbers need space inside the hover surface');
    await page.screenshot({ path: '/tmp/sentinel-tour-welcome.png' });
    await page.getByRole('button', { name: 'Begin the guide' }).click();
    const next = () =>
      page.locator('.tour-coach-footer').getByRole('button', { name: 'Continue', exact: true });
    const verified = () => page.locator('.tour-feedback[data-verified="true"]').waitFor();
    const topic = async (title) => {
      await page.getByRole('button', { name: 'Guide contents' }).click();
      await page
        .locator('.tour-contents')
        .getByRole('button', { name: new RegExp(title) })
        .click();
    };
    assert.equal(await next().isDisabled(), true);
    await page.getByRole('button', { name: 'Locate the control' }).click();
    await page
      .locator('.tour-highlight')
      .evaluate((element) =>
        Promise.all(element.getAnimations().map((animation) => animation.finished.catch(() => {})))
      );
    await page.screenshot({ path: '/tmp/sentinel-tour-locator.png' });
    const highlight = await page.locator('.tour-highlight').evaluate((element) => {
      const r = element.getBoundingClientRect(),
        css = getComputedStyle(element);
      return { radius: parseFloat(css.borderTopLeftRadius), height: r.height, shadow: css.boxShadow };
    });
    assert.ok(highlight.radius >= highlight.height / 2 - 1, 'The highlight should follow the pill shape');
    const dimAlpha = Number(/rgba\(0, 0, 0, ([\d.]+)\)/.exec(highlight.shadow)?.[1]);
    assert.ok(dimAlpha >= 0.7, `Locate should dim the rest of the workspace clearly: ${highlight.shadow}`);
    await page.getByRole('button', { name: 'Clear highlight' }).click();
    await page.locator('.global-new-chat').click();
    await page.waitForFunction(() => window.tourTest.active() === 'new-1');
    assert.equal(await next().isDisabled(), true, 'A click must not pass a shortcut exercise');
    failNew = true;
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.request().method() === 'POST' &&
          response.url().endsWith('/sessions') &&
          response.status() === 500
      ),
      page.keyboard.press('Meta+n'),
    ]);
    await page.getByRole('region', { name: 'Guided tour' }).waitFor();
    assert.equal(await next().isDisabled(), true, 'A failed request must not pass');
    failNew = false;
    await page.locator('.global-new-chat').click();
    await page.waitForFunction(() => window.tourTest.active() === 'new-2');
    assert.equal(await next().isDisabled(), true, 'A later click must not count as the failed shortcut');
    await page.keyboard.press('Control+n');
    await page.getByText('Use the Command (⌘) key for this shortcut on Mac.').waitFor();
    assert.equal(await next().isDisabled(), true, 'Control must not pretend to be Command on Mac');
    // Verify on keydown even when macOS omits the letter keyup. Releasing the
    // modifier first also must not lose which shortcut originally ran.
    await page.keyboard.down('Meta');
    await page.keyboard.down('n');
    await verified();
    await page.keyboard.up('Meta');
    await page.keyboard.up('n');
    await page.screenshot({ path: '/tmp/sentinel-tour-verified.png' });
    await page.getByRole('heading', { name: 'Give the agent a clear brief.' }).waitFor();
    await page
      .getByRole('textbox', { name: 'Message', exact: true })
      .fill('Review the project and explain the next steps.');
    await page.locator('.tour-tasks li[data-verified]').waitFor();
    await page.keyboard.press('Shift+Enter');
    await verified();
    await page.getByRole('heading', { name: 'Your history, a keystroke away.' }).waitFor();
    await page.keyboard.press('Meta+k');
    await page.getByRole('dialog', { name: 'All chats', exact: true }).waitFor();
    await page.locator('.tour-tasks li[data-verified]').waitFor();
    await page.keyboard.press('Escape');
    await verified();
    await page.getByRole('heading', { name: 'Move between the dots.' }).waitFor();
    await page.keyboard.down('Meta');
    await page.locator('.tour-tasks li[data-verified]').first().waitFor();
    await page.keyboard.up('Meta');
    await page.keyboard.press('Meta+1');
    await page.waitForFunction(() => document.querySelectorAll('.tour-tasks li[data-verified]').length === 2);
    await page.keyboard.press('Meta+ArrowRight');
    await verified();
    await page.getByRole('heading', { name: 'Step through your history.' }).waitFor();
    await page.keyboard.press('Meta+ArrowUp');
    await verified();
    await topic('Make the shortcuts yours');
    await page.locator('[data-session-dot]').first().focus();
    await page.keyboard.press('Alt+ArrowRight');
    await verified();
    await topic('Close a view');
    await page.keyboard.press('Meta+n');
    await page.locator('.tour-tasks li[data-verified]').waitFor();
    await page.keyboard.press('Meta+w');
    await verified();
    await topic('Deletion is a separate action');
    await page.keyboard.press('Meta+Backspace');
    await page.getByRole('alertdialog').waitFor();
    await page.locator('.tour-tasks li[data-verified]').waitFor();
    await page.keyboard.press('Escape');
    await verified();
    assert.equal(deletions, 0);
    await page.getByRole('button', { name: 'Shortcut reference' }).click();
    await page.getByRole('textbox', { name: 'Search shortcuts' }).fill('focus');
    await page.waitForTimeout(850);
    assert.equal(
      await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.product-tour:test')).step),
      'delete',
      'The shortcut reference pauses automatic advance'
    );
    assert.ok((await page.locator('.tour-shortcut-row').count()) >= 1);
    await page.screenshot({ path: '/tmp/sentinel-tour-shortcuts.png' });
    await page.setViewportSize({ width: 375, height: 620 });
    await page.getByRole('textbox', { name: 'Search shortcuts' }).fill('');
    const fits = await page.locator('.tour-coach').evaluate((element) => {
      const r = element.getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
    });
    assert.ok(fits, 'Coach must stay within narrow viewports');
    await page.screenshot({ path: '/tmp/sentinel-tour-narrow.png' });
    await page.getByRole('button', { name: 'Finish guide later' }).click();
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.product-tour:test')));
    assert.ok(saved.verified.includes('new-chat/create'));
    assert.ok(saved.verified.includes('delete/cancel'));
    await page.evaluate(() => window.tourTest.reopen());
    await page.getByRole('button', { name: 'Resume guide' }).click();
    await verified();
    await topic('Read what actually happened');
    await page.getByRole('button', { name: 'Skip for now' }).click();
    const skipped = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.product-tour:test')));
    assert.ok(skipped.skipped.includes('tool-cards'));
    assert.ok(!skipped.verified.includes('tool-cards/inspect'));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(
      await page.locator('.tour-coach').evaluate((element) => getComputedStyle(element).animationName),
      'none'
    );

    // Render the actual setup route without saving, importing, or provisioning.
    await page.setViewportSize({ width: 1280, height: 880 });
    await page.goto(`${server.resolvedUrls.local[0]}instances/test/workspace?setup`);
    await page.getByRole('button', { name: 'Set up Sentinel' }).waitFor();
    await page.screenshot({ path: '/tmp/sentinel-setup-welcome.png' });
    await page.getByRole('button', { name: 'Set up Sentinel' }).click();
    await page.getByRole('heading', { name: 'Connect a model provider.' }).waitFor();
    await page.screenshot({ path: '/tmp/sentinel-setup-providers.png' });
    await page.setViewportSize({ width: 375, height: 620 });
    const footerFits = await page.locator('.setup-footer').evaluate((element) => {
      const r = element.getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    });
    assert.ok(footerFits, 'Setup navigation must remain visible on small screens');
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      'Setup must not overflow horizontally'
    );
    await page.screenshot({ path: '/tmp/sentinel-setup-narrow.png' });
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    await server.close();
  }
});
