import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Workspace } from '../../src/components/workspace/Workspace';
import { useWorkspaceStore } from '../../src/store/workspace-store';
import { useActiveSessionStore } from '../../src/store/active-session-store';
window.history.replaceState({}, '', '/instances/test/sessions');
useActiveSessionStore.getState().setActiveSession('test', 'session-a');
Object.assign(window, { hmrHarness: {
  splitChat() {
    const store = useWorkspaceStore.getState();
    store.splitPane(store.openTabs.sessions!, 'files', 'right');
  },
  layout() { return useWorkspaceStore.getState().layout; },
  select(id: string) { useActiveSessionStore.getState().setActiveSession('test', id); },
} });
function Harness() {
  const selected = useActiveSessionStore(state => state.byInstance.test);
  return <><span data-global-session={selected}>{selected}</span><div style={{height:'90vh'}}><Workspace instanceName="test" /></div></>;
}
createRoot(document.getElementById('root')!).render(<MemoryRouter initialEntries={['/instances/test/sessions']}><Harness /></MemoryRouter>);
