import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createElement as h, Fragment } from 'react';
import { createPortal } from 'react-dom';
import { createServer } from 'vite';

test('opening header overlays preserves toolbar slots, order and control identity', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    server: { middlewareMode: true }, logLevel: 'error',
    optimizeDeps: { noDiscovery: true, include: [] },
  });
  try {
    const { splitPaneActions } = await server.ssrLoadModule('/src/lib/pane-action-items.ts');
    // React only needs a container node type to construct a portal; no UI is mounted.
    const container = { nodeType: 1 };
    const overlay = createPortal(h('div', null, 'Live usage'), container);
    const backdrop = createPortal(h('div', null, 'Backdrop'), container);
    const control = label => h('button', { key: label }, label);
    const header = open => h('div', { className: 'flex gap-2' },
      open && backdrop,
      h('div', { className: 'flex' }, control('Live'), open && overlay, control('Workspace')),
      h(Fragment, null, control('Model'), control('Mode'), control('Max steps')),
    );
    const closed = splitPaneActions(header(false));
    const opened = splitPaneActions(header(true));
    const identity = result => result.items.map(item => [item.type, item.key, item.props.children]);
    assert.deepEqual(identity(opened), identity(closed));
    assert.equal(closed.items.length, 5);
    assert.equal(closed.overlays.length, 0);
    assert.deepEqual(opened.overlays, [backdrop, overlay]);
    assert.deepEqual(identity(splitPaneActions(header(false))), identity(closed));

    // A control with its own ref and popup remains one indivisible toolbar item.
    const group = h('div', { ref: { current: null }, className: 'relative flex' }, control('Model'), overlay);
    const grouped = splitPaneActions(group);
    assert.equal(grouped.items.length, 1);
    assert.equal(grouped.overlays.length, 0);
    assert.equal(grouped.items[0].props.children[1], overlay);
  } finally {
    await server.close();
  }
});
