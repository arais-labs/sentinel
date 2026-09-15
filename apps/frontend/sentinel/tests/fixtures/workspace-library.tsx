import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { WorkspaceProvider } from '../../src/lib/workspace-context';
import { WorkspacesPanel } from '../../src/components/runtime/WorkspacesPanel';

function Harness() {
  const [instance, setInstance] = useState('test');
  Object.assign(window, { selectLibraryInstance: setInstance });
  return <WorkspaceProvider instanceName={instance}><WorkspacesPanel page /></WorkspaceProvider>;
}
createRoot(document.getElementById('root')!).render(<MemoryRouter initialEntries={['/instances/test/workspace']}><Harness /></MemoryRouter>);
