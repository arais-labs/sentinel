import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../../src/index.css';
import { WorkspacePerformanceChip } from '../../src/components/session/WorkspaceRuntimeStats';
import { useSessionWorkspace } from '../../src/hooks/useSessionWorkspace';

function Fixture() {
  const { workspace } = useSessionWorkspace('session-a', 'test');
  const [mounted, setMounted] = useState(true);
  return <main style={{ padding: 24 }}>
    <button onClick={() => setMounted(value => !value)}>Toggle header</button>
    {mounted && workspace && <>
      <WorkspacePerformanceChip workspace={workspace} instanceName="test" />
      <WorkspacePerformanceChip workspace={workspace} instanceName="test" />
    </>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
