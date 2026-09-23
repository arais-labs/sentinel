import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import type { RuntimeUpdateRequirement } from '../../lib/runtime-compatibility';
import type { Machine } from '../../types/api';
import { RemoteRuntimeDialog } from './RemoteRuntimeDialog';

/** Shared recovery action; only the existing update dialog can approve changes. */
export function RuntimeUpdateNotice({ requirement, machineId, onUpdated }: {
  requirement: RuntimeUpdateRequirement; machineId?: string; onUpdated: () => Promise<unknown>;
}) {
  const [machine, setMachine] = useState<Machine | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { setMachine(null); setError(''); }, [machineId, requirement.code]);
  async function open() {
    setOpening(true); setError('');
    try {
      const rows = await api.get<Machine[]>('/machines');
      const selected = rows.find(row => row.id === (requirement.machine_id ?? machineId));
      if (!selected) throw new Error('Machine unavailable. Refresh the workspace.');
      if (selected.provider !== 'ssh') throw new Error('The local runtime is bundled with Sentinel. Restart or update Sentinel.');
      setMachine(selected);
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not open runtime update.'); }
    finally { setOpening(false); }
  }
  return <div className="runtime-update-notice" role="status">
    <p>{requirement.message}</p>
    {requirement.code === 'runtime_update_required' && <button className="workspace-desktop-primary" type="button" disabled={opening} onClick={() => void open()}>{opening ? 'Checking runtime…' : 'Update runtime'}</button>}
    {error && <p role="alert">{error}</p>}
    {machine && <RemoteRuntimeDialog machine={machine} onClose={() => setMachine(null)} onInstalled={async () => { await onUpdated(); }} />}
  </div>;
}
