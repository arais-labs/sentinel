import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from 'playwright';

test('terminal placement uses available space, preserves other views, and reveals focused layouts', async () => {
  const fixture = `
    import { createDockview } from 'dockview-react';
    import 'dockview-react/dist/styles/dockview.css';
    import { useWorkspaceStore as store, sessionLayoutKey } from '/src/store/workspace-store.ts';
    import { useFocusModeStore as focus } from '/src/store/focus-mode-store.ts';
    const host = document.getElementById('layout');
    const api = createDockview(host, { createComponent: () => ({ element: document.createElement('div'), init() {} }) });
    store.getState().bindApi(api, sessionLayoutKey('test', 'first'));
    window.terminalTest = {
      api, store, focus,
      reset(width, height) {
        focus.getState().setPaneId(null, { animate: false });
        if (api.hasMaximizedGroup()) api.exitMaximizedGroup();
        store.getState().resetWorkspace();
        host.style.width = width + 'px'; host.style.height = height + 'px';
        api.layout(width, height);
        return store.getState().openTab('sessions');
      },
      bounds(id) {
        const r = api.getPanel(id).group.element.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      },
    };
  `;
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{
      name: 'terminal-placement-fixture', enforce: 'pre',
      resolveId(id) {
        if (/\/workspace-tabs(?:\.tsx)?$/.test(id)) return '\0terminal-placement-tabs';
        if (id === '\0terminal-placement') return id;
      },
      load(id) {
        if (id === '\0terminal-placement-tabs') return "export const WORKSPACE_TAB_IDS = ['sessions', 'files', 'logs', 'terminal']; export const WORKSPACE_TABS = WORKSPACE_TAB_IDS.map(id => ({ id, label: id })); export const isWorkspaceTabId = id => WORKSPACE_TAB_IDS.includes(id);";
        if (id === '\0terminal-placement') return fixture;
      },
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (req.url !== '/terminal-placement') return next();
          res.setHeader('Content-Type', 'text/html');
          res.end('<html><style>body{margin:0}</style><div id="layout"></div><script type="module" src="/@id/__x00__terminal-placement"></script></html>');
        });
      },
    }], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error',
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`${server.resolvedUrls.local[0]}terminal-placement`);
    await page.waitForFunction(() => window.terminalTest).catch(error => { throw new Error(errors.join('\n') || error.message); });

    for (const [width, height, direction] of [[1440, 800, 'left'], [720, 900, 'left']]) {
      const result = await page.evaluate(([width, height]) => {
        const t = window.terminalTest, chat = t.reset(width, height);
        const terminal = t.store.getState().openTerminalPane(chat);
        const before = t.bounds(terminal);
        const repeated = t.store.getState().openTerminalPane(chat);
        return { chat: t.bounds(chat), terminal: before, repeated: t.bounds(repeated), same: terminal === repeated,
          tabs: t.api.panels.map(p => p.params.tabId), count: t.api.groups.length };
      }, [width, height]);
      assert.deepEqual(result.tabs, ['sessions', 'terminal']);
      assert.equal(result.count, 2);
      assert.ok(result.same);
      assert.deepEqual(result.repeated, result.terminal);
      if (direction === 'left') assert.ok(result.terminal.x + result.terminal.width <= result.chat.x + 1);
      else assert.ok(result.terminal.y >= result.chat.y + result.chat.height - 1);
    }

    const crowded = await page.evaluate(() => {
      const t = window.terminalTest, chat = t.reset(1500, 800), s = t.store.getState();
      const files = s.splitPane(chat, 'files', 'right');
      t.api.getPanel(chat).api.group.api.setSize({ width: 300 });
      const before = t.bounds(chat), terminal = s.openTerminalPane(chat);
      return { before, after: t.bounds(chat), terminal: t.bounds(terminal), files: t.bounds(files), tabs: t.api.panels.map(p => p.params.tabId) };
    });
    assert.deepEqual(crowded.before, crowded.after);
    assert.deepEqual(crowded.tabs, ['sessions', 'files', 'terminal']);
    assert.ok(crowded.terminal.x >= crowded.after.x + crowded.after.width - 1);
    assert.ok(crowded.terminal.width >= 480 && crowded.terminal.height >= 300);

    const cramped = await page.evaluate(() => {
      const t = window.terminalTest, chat = t.reset(1000, 500), s = t.store.getState();
      s.splitPane(chat, 'files', 'right');
      const terminal = s.openTerminalPane(chat);
      return { terminal: t.bounds(terminal), tabs: t.api.panels.map(p => p.params.tabId), groups: t.api.groups.length };
    });
    assert.deepEqual(cramped.tabs, ['sessions', 'files', 'terminal']);
    assert.equal(cramped.groups, 3);
    assert.ok(cramped.terminal.width >= 480 && cramped.terminal.height >= 300);

    const focused = await page.evaluate(() => {
      const t = window.terminalTest, chat = t.reset(1440, 800), s = t.store.getState();
      const terminal = s.openTerminalPane(chat), before = t.bounds(terminal);
      t.api.getPanel(chat).api.maximize();
      t.focus.getState().setPaneId(chat, { animate: false });
      s.openTerminalPane(chat);
      return { maximized: t.api.hasMaximizedGroup(), focus: t.focus.getState().paneId, active: t.api.activePanel.id,
        terminal, before, after: t.bounds(terminal), count: t.api.panels.length };
    });
    assert.equal(focused.maximized, false);
    assert.equal(focused.focus, null);
    assert.equal(focused.active, focused.terminal);
    assert.deepEqual(focused.before, focused.after);
    assert.equal(focused.count, 2);

    const firstFromFocus = await page.evaluate(() => {
      const t = window.terminalTest, chat = t.reset(720, 900);
      t.api.getPanel(chat).api.maximize();
      t.focus.getState().setPaneId(chat, { animate: false });
      const terminal = t.store.getState().openTerminalPane(chat);
      return { chat: t.bounds(chat), terminal: t.bounds(terminal), maximized: t.api.hasMaximizedGroup() };
    });
    assert.equal(firstFromFocus.maximized, false);
    assert.ok(firstFromFocus.terminal.x + firstFromFocus.terminal.width <= firstFromFocus.chat.x + 1);

    const empty = await page.evaluate(() => {
      const t = window.terminalTest, chat = t.reset(720, 900);
      t.api.getPanel(chat).api.updateParameters({ tabId: null });
      const terminal = t.store.getState().openTerminalPane();
      return { reused: terminal === chat, tabs: t.api.panels.map(p => p.params.tabId) };
    });
    assert.ok(empty.reused);
    assert.deepEqual(empty.tabs, ['terminal']);

    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    await server.close();
  }
});
