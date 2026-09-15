import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer, transformWithOxc } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

// Real Workspace, Dockview drop handlers, pane headers, and store. Pane content
// and the desktop socket are stubbed so tests cannot start a runtime or agent.
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import {Workspace} from '/src/components/workspace/Workspace.tsx';
import {ProductTour} from '/src/components/onboarding/ProductTour.tsx';
import {useWorkspaceStore,WORKSPACE_DND_MIME} from '/src/store/workspace-store.ts';
import {useActiveSessionStore} from '/src/store/active-session-store.ts';
import {useThemeStore} from '/src/store/theme-store.ts';
import '/src/index.css';
useActiveSessionStore.setState({byInstance:{test:'a'},recentByInstance:{test:['a']}});
useWorkspaceStore.setState({layout:null,sessionLayouts:{},openTabs:{}});
useThemeStore.setState({theme:'dark'});
localStorage.removeItem('sentinel.product-tour:test');
window.layoutGuide={store:useWorkspaceStore};
createRoot(document.getElementById('root')).render(<MemoryRouter initialEntries={['/instances/test/workspace']}>
 <aside id="sidebar">{['terminal','files'].map(id=><button key={id} data-tour-nav={id} draggable onDragStart={event=>{event.dataTransfer.setData(WORKSPACE_DND_MIME,id);event.dataTransfer.effectAllowed='copy';}}>{id}</button>)}<div id="cancel-target">Sidebar</div></aside>
 <main><Workspace instanceName="test"/></main>
 <ProductTour instanceName="test" onClose={()=>{}}/>
</MemoryRouter>);
`;

test('guide recognizes real edge drops, moving existing panes, slow drags, and automatic advance', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    plugins: [
      {
        name: 'guide-layout-fixture',
        enforce: 'pre',
        resolveId(id) {
          if (/\/desktop-socket(?:\.ts)?$/.test(id)) return '\0guide-layout-socket';
          if (/\/workspace-tabs(?:\.tsx)?$/.test(id)) return '\0guide-layout-tabs';
          if (/\/WorkspacePane(?:\.tsx)?$/.test(id)) return '\0guide-layout-content';
          if (id === '\0guide-layout-fixture') return id;
        },
        async load(id) {
          if (id === '\0guide-layout-socket')
            return `export class DesktopSocket extends EventTarget { readyState=0; send(){} close(){} }`;
          if (id === '\0guide-layout-tabs')
            return `export const WORKSPACE_TAB_IDS=['sessions','terminal','files']; export const WORKSPACE_TABS=WORKSPACE_TAB_IDS.map(id=>({id,label:id,icon:()=>null})); export const getWorkspaceTab=id=>WORKSPACE_TABS.find(tab=>tab.id===id); export const isWorkspaceTabId=id=>WORKSPACE_TAB_IDS.includes(id);`;
          if (id === '\0guide-layout-content')
            return `import {createElement} from 'react'; export function WorkspacePane({params}){return createElement('div',{'data-layout-view':params.tabId,style:{height:'100%',padding:'20px'}},params.tabId);}`;
          if (id === '\0guide-layout-fixture')
            return (await transformWithOxc(fixture, 'guide-layout-fixture.tsx', {
              jsx: { runtime: 'automatic' },
            })).code;
        },
        configureServer(s) {
          s.middlewares.use(async (req, res, next) => {
            if (req.url !== '/layout-guide') return next();
            res.setHeader('Content-Type', 'text/html');
            res.end(
              await s.transformIndexHtml(
                req.url,
                '<html class="dark"><style>html,body,#root{height:100%;margin:0}#root{display:flex}#sidebar{width:150px;flex-shrink:0;padding:24px 15px;display:flex;flex-direction:column;gap:24px}#sidebar button{padding:12px;text-align:left}#cancel-target{height:250px}main{flex:1;min-width:0}</style><div id="root"></div><script type="module" src="/@id/__x00__guide-layout-fixture"></script></html>'
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
  let browser, page;
  try {
    await server.listen();
    browser = await chromium.launch({
      headless: true,
      ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}),
    });
    page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.setDefaultTimeout(12000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const reset = async (existing = false) => {
      await page.goto(`${server.resolvedUrls.local[0]}layout-guide`);
      await page.locator('[data-tour-pane="sessions"]').waitFor();
      if (existing)
        await page.evaluate(() => {
          const s = window.layoutGuide.store.getState();
          s.splitPane(s.openTabs.sessions, 'terminal', 'right');
        });
      await page
        .locator('.tour-chapters')
        .getByRole('button', { name: /Your workspace/ })
        .click();
      await page.getByRole('heading', { name: 'Arrange the work around you.' }).waitFor();
    };
    const count = () =>
      page.evaluate(() => Object.keys(window.layoutGuide.store.getState().layout.panels).length);
    const notVerified = async () => {
      await page.waitForTimeout(850);
      assert.equal(
        await page
          .locator('.tour-coach-footer')
          .getByRole('button', { name: 'Continue', exact: true })
          .isDisabled(),
        true
      );
      assert.equal(await page.getByRole('heading', { name: 'Arrange the work around you.' }).count(), 1);
    };
    const dropAt = async (position) => {
      const target = page.locator('[data-layout-view="sessions"]'),
        box = await target.boundingBox();
      await page
        .locator('[data-tour-nav="terminal"]')
        .dragTo(target, {
          targetPosition: {
            x: position === 'left' ? 10 : position === 'right' ? box.width - 10 : box.width / 2,
            y: box.height / 2,
          },
        });
    };
    await reset();
    await page.locator('[data-tour-nav="terminal"]').dragTo(page.locator('#cancel-target'));
    assert.equal(await count(), 1);
    await notVerified();

    await dropAt('center');
    assert.equal(await count(), 1);
    await notVerified();

    await reset(true);
    await dropAt('left');
    assert.equal(await count(), 2, 'Moving an existing pane keeps the pane count');
    await page.getByRole('heading', { name: 'Room to concentrate.' }).waitFor();
    assert.ok(
      await page.evaluate(() =>
        JSON.parse(localStorage.getItem('sentinel.product-tour:test')).verified.includes('layout/split')
      )
    );

    await reset();
    // Native drag focus can return after drop, before the observer's next
    // animation frame. The layout changed, but verification is still queued.
    await page.evaluate(() => {
      document.addEventListener('drop', () => window.dispatchEvent(new Event('blur')), { once: true, capture: true });
    });
    await dropAt('right');
    assert.equal(await count(), 2);
    await page.getByRole('heading', { name: 'Room to concentrate.' }).waitFor();

    await reset();
    const source = await page.locator('[data-tour-nav="terminal"]').boundingBox();
    const target = await page.locator('[data-layout-view="sessions"]').boundingBox();
    await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x + target.width - 10, target.y + target.height / 2, { steps: 20 });
    await page.mouse.move(target.x + target.width - 9, target.y + target.height / 2);
    await page.locator('.tour-live[data-dragging]').waitFor();
    assert.equal(
      await page.locator('.tour-coach').evaluate((el) => getComputedStyle(el).pointerEvents),
      'none',
      'The guide must not block a drop zone'
    );
    await page.waitForTimeout(8500);
    await page.mouse.move(target.x + target.width - 10, target.y + target.height / 2);
    await page.mouse.up();
    await page.getByRole('heading', { name: 'Room to concentrate.' }).waitFor();
    assert.equal(await count(), 2, 'A slow edge drop still adds and verifies a pane');
    await page.screenshot({ path: '/tmp/sentinel-tour-edge-drop.png' });

    // Real focus shortcut also advances between actions, then into the next lesson.
    await page.keyboard.press('Meta+f');
    await page.locator('.tour-tasks li[data-verified]').waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('heading', { name: 'Choose where work runs.' }).waitFor();

    await reset();
    await page.getByTitle('Split pane', { exact: true }).click();
    await page.getByRole('menu').getByRole('button', { name: 'terminal', exact: true }).click();
    await page.getByRole('heading', { name: 'Room to concentrate.' }).waitFor();
    assert.equal(await count(), 2, 'The split menu also verifies and advances');
    assert.deepEqual(errors, []);
  } catch (error) {
    await page?.screenshot({ path: '/tmp/sentinel-tour-layout-failure.png' });
    throw error;
  } finally {
    await browser?.close();
    await server.close();
  }
});
