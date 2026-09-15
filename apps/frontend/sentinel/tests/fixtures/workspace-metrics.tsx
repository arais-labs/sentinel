import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../../src/index.css';
import { WorkspaceMetricsProvider, WorkspaceRuntimeStats } from '../../src/components/session/WorkspaceRuntimeStats';

function Fixture() {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(true);
  return <main style={{ padding: 24 }}>
    <button onClick={() => setOpen(value => !value)}>Toggle metrics</button>
    <button onClick={() => setMounted(value => !value)}>Toggle header</button>
    {mounted && <>
      <WorkspaceMetricsProvider sessionId="session-a" instanceName="test">
        {open && <div className="session-telemetry-panel"><WorkspaceRuntimeStats /></div>}
      </WorkspaceMetricsProvider>
      <WorkspaceMetricsProvider sessionId="session-b" instanceName="test">{null}</WorkspaceMetricsProvider>
    </>}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
