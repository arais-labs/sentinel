import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

// Layout serialization is owned by Dockview. This double lets us delay its
// change event to exercise switching/unmounting before that event is delivered.
function dock() {
  let value = { panels: {} };
  const listeners = new Set();
  const activeListeners = new Set();
  return {
    groups: [],
    get panels() { return Object.values(value.panels); },
    toJSON: () => structuredClone(value),
    fromJSON(saved) { if (saved.invalid) throw new Error('Invalid layout'); value = structuredClone(saved); },
    addPanel(panel) { value.panels[panel.id] = panel; },
    clear() { value = { panels: {} }; },
    onDidLayoutChange(listener) { listeners.add(listener); return { dispose: () => listeners.delete(listener) }; },
    onDidActivePanelChange(listener) { activeListeners.add(listener); return { dispose: () => activeListeners.delete(listener) }; },
    edit(next) { value = structuredClone(next); },
    pendingEvent() { const pending = [...listeners]; return () => pending.forEach(listener => listener()); },
  };
}

test('session layouts isolate, capture pending edits, restore and persist independently', async () => {
  const values = new Map();
  const previousWindow = globalThis.window;
  const previousStorage = globalThis.localStorage;
  globalThis.window = new EventTarget();
  globalThis.localStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    // The production registry imports page renderers (including noVNC). Only
    // tab IDs are relevant to these store tests; don't mount any UI here.
    plugins: [{
      name: 'layout-test-tab-registry',
      enforce: 'pre',
      resolveId(id) {
        if (/\/workspace-tabs(?:\.tsx)?$/.test(id)) return '\0layout-test-tabs';
      },
      load(id) {
        if (id === '\0layout-test-tabs' || /\/workspace-tabs(?:\.tsx)?$/.test(id)) return "export const WORKSPACE_TAB_IDS = ['sessions', 'files', 'terminal']; export const isWorkspaceTabId = id => WORKSPACE_TAB_IDS.includes(id);";
      },
    }],
    server: { middlewareMode: true }, optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'error',
  });
  let unbind;
  try {
    const { useWorkspaceStore: store, sessionLayoutKey: key } = await server.ssrLoadModule('/src/store/workspace-store.ts');
    const first = key('one', 'a'), second = key('one', 'b'), other = key('two', 'a');
    const layoutA = { panels: { chat: { id: 'chat', params: { tabId: 'sessions' } }, files: { id: 'files', params: { tabId: 'files' } } }, activeGroup: 'files', grid: { width: 1200, height: 800, orientation: 0, splitSize: 420 } };
    const layoutB = { panels: { term: { id: 'term', params: { tabId: 'terminal' } } }, activeGroup: 'term', grid: { width: 1200, height: 800, orientation: 1 } };
    const apiA = dock();
    const unbindA = store.getState().bindApi(apiA, first);
    assert.equal(apiA.panels[0].params.tabId, 'sessions');
    apiA.edit(layoutA);
    const lateEvent = apiA.pendingEvent();
    const apiB = dock();
    unbind = store.getState().bindApi(apiB, second);
    assert.equal(apiB.panels.length, 1);
    assert.equal(apiB.panels[0].params.tabId, 'sessions');
    assert.deepEqual(store.getState().sessionLayouts[first], layoutA);
    apiB.edit(layoutB);
    unbindA(); lateEvent(); // Old cleanup must not save B's layout under A.
    unbind();
    assert.deepEqual(store.getState().sessionLayouts[first], layoutA);
    assert.deepEqual(store.getState().sessionLayouts[second], layoutB);

    const restored = dock();
    unbind = store.getState().bindApi(restored, first);
    assert.deepEqual(restored.toJSON(), layoutA);
    assert.deepEqual(store.getState().openTabs, { sessions: 'chat', files: 'files' });
    store.getState().resetWorkspace();
    unbind();
    const empty = dock();
    unbind = store.getState().bindApi(empty, first);
    assert.equal(empty.panels.length, 0);
    unbind();

    const isolated = dock();
    unbind = store.getState().bindApi(isolated, other);
    assert.equal(isolated.panels[0].params.tabId, 'sessions');
    unbind();
    const persisted = values.get('sentinel.workspace');
    store.setState({ sessionLayouts: {}, layout: null, openTabs: {} });
    values.set('sentinel.workspace', persisted);
    await store.persist.rehydrate();
    const reloaded = dock();
    unbind = store.getState().bindApi(reloaded, second);
    assert.deepEqual(reloaded.toJSON(), layoutB);
    assert.deepEqual(store.getState().sessionLayouts[first], { panels: {} });
    unbind();

    store.setState({ sessionLayouts: { ...store.getState().sessionLayouts, [first]: { invalid: true } } });
    const recovered = dock();
    unbind = store.getState().bindApi(recovered, first);
    assert.equal(recovered.panels[0].params.tabId, 'sessions');
    assert.deepEqual(store.getState().sessionLayouts[second], layoutB);
  } finally {
    unbind?.();
    await server.close();
    globalThis.window = previousWindow;
    globalThis.localStorage = previousStorage;
  }
});
