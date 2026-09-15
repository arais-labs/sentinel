import { createDockview } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { executeSessionLayout, sessionLayoutKey, useWorkspaceStore } from '../../src/store/workspace-store';
import { useFocusModeStore } from '../../src/store/focus-mode-store';

function mount() {
  const api = createDockview(document.getElementById('layout')!, {
    createComponent: () => ({ element: document.createElement('div'), init() {} }),
  });
  api.layout(window.innerWidth, window.innerHeight);
  const unbind = useWorkspaceStore.getState().bindApi(api, sessionLayoutKey('test', 'session-a'));
  return { api, unbind };
}
let current = mount();
// Compare the persisted JSON contract, without Dockview's undefined fields.
const snapshot = () => JSON.parse(JSON.stringify(current.api.toJSON()));
Object.assign(window, { layoutTest: {
  snapshot,
  orphanedLayout() {
    const layout = snapshot();
    // A persisted layout can contain an empty lower group.
    layout.grid.root.data.push({ type: 'leaf', data: { id: 'orphan', views: [] }, size: 334 });
    return layout;
  },
  addEmptyGroup() {
    current.api.addGroup({ referencePanel: current.api.panels[0], direction: 'below' });
  },
  groupSizes: () => current.api.groups.map(group => group.panels.length),
  headersVisible: () => current.api.groups.every(group => !group.model.header.hidden),
  focusAndSave() {
    const panel = current.api.panels.find(panel => panel.params?.tabId === 'sessions')!;
    useFocusModeStore.getState().setPaneId(panel.id, { animate: false });
    panel.api.maximize();
    panel.group.model.header.hidden = true;
    window.dispatchEvent(new Event('pagehide'));
    return snapshot();
  },
  undoFromFocus() {
    const key = sessionLayoutKey('test', 'session-a');
    executeSessionLayout(key, { action: 'apply', operations: [{ operation: 'restore' }] });
    executeSessionLayout(key, { action: 'undo' });
    return { layout: snapshot(), focused: useFocusModeStore.getState().paneId };
  },
  arrange() {
    const store = useWorkspaceStore.getState();
    const first = current.api.panels[0].id;
    const terminal = store.splitPane(first, 'terminal', 'right')!;
    store.splitPane(first, 'files', 'below');
    current.api.getPanel(terminal)!.api.group.api.setSize({ width: 420 });
    current.api.getPanel(terminal)!.api.setActive();
    window.dispatchEvent(new Event('pagehide'));
    return snapshot();
  },
  remount() {
    current.unbind();
    current.api.dispose();
    current = mount();
    return snapshot();
  },
  closeAll() {
    for (const panel of [...current.api.panels]) useWorkspaceStore.getState().closePane(panel.id);
  },
} });
