import { useCallback, useEffect, useState } from 'react';
import { HardDrive, Loader2, RefreshCw } from 'lucide-react';
import { api } from '../../lib/api';
import { formatBytes, sharedLabels, usageSegments, type UsageSegment } from './workspaceUsage';
import type { Machine, MachineUsage } from '../../types/api';

type MachineUsageState = { usage?: MachineUsage; error?: string; loading: boolean };

const segmentLabels: Record<UsageSegment['kind'], string> = {
  workspaces: 'Workspaces', ...sharedLabels, elsewhere: 'Other files on this disk', free: 'Free',
};

/** How much of each machine's disk Sentinel uses: per workspace, its shared items, and what is free. */
export function WorkspaceDiskUsage({ machines }: { machines: Machine[] }) {
  const [usage, setUsage] = useState<Record<string, MachineUsageState>>({});
  const ids = machines.map(machine => machine.id).join('\n');
  const load = useCallback((machineIds: string[]) => {
    for (const id of machineIds) {
      setUsage(current => ({ ...current, [id]: { ...current[id], loading: true } }));
      api.get<MachineUsage>(`/workspaces/usage/${id}`, { timeoutMs: 30_000 })
        .then(value => setUsage(current => ({ ...current, [id]: { usage: value, loading: false } })))
        .catch(error => setUsage(current => ({ ...current, [id]: { ...current[id], loading: false,
          error: error instanceof Error ? error.message : 'Could not read disk usage' } })));
    }
  }, []);
  useEffect(() => { load(ids.split('\n').filter(Boolean)); }, [ids, load]);
  if (!machines.length) return null;
  const busy = machines.some(machine => usage[machine.id]?.loading);
  return <section className="workspace-usage" aria-labelledby="workspace-usage-title">
    <div className="workspace-usage-header">
      <h2 id="workspace-usage-title"><HardDrive size={14} aria-hidden="true" />Disk usage</h2>
      <button type="button" disabled={busy} onClick={() => load(machines.map(machine => machine.id))} aria-label="Refresh disk usage">
        {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}Refresh
      </button>
    </div>
    {machines.map(machine => {
      const state = usage[machine.id];
      return <div key={machine.id} className="workspace-usage-machine">
        {machines.length > 1 && <h3>{machine.name}</h3>}
        {state?.error ? <p role="alert" className="workspace-usage-error">{state.error}</p>
          : !state?.usage ? <p className="workspace-usage-loading"><Loader2 size={13} className="animate-spin" />Reading disk usage…</p>
          : <MachineUsageBreakdown usage={state.usage} />}
      </div>;
    })}
  </section>;
}

function MachineUsageBreakdown({ usage }: { usage: MachineUsage }) {
  const segments = usageSegments(usage);
  const row = (key: string, kind: UsageSegment['kind'], label: string, bytes: number) =>
    <li key={key} data-kind={kind} data-empty={bytes === 0 || undefined}><span className="workspace-usage-dot" aria-hidden="true" /><span className="workspace-usage-label" title={label}>{label}</span><span>{formatBytes(bytes)}</span></li>;
  return <>
    <div className="workspace-usage-bar" role="img" aria-label={segments.map(segment => `${segmentLabels[segment.kind]} ${formatBytes(segment.bytes)}`).join(', ')}>
      {segments.map(segment => <span key={segment.kind} data-kind={segment.kind} style={{ flexGrow: segment.share }} title={`${segmentLabels[segment.kind]}: ${formatBytes(segment.bytes)}`} />)}
    </div>
    <ul className="workspace-usage-rows">
      {usage.workspaces.map(space => row(space.id, 'workspaces', space.name, space.bytes))}
      {usage.other > 0 && row('other', 'workspaces', 'Other workspaces', usage.other)}
      {(Object.keys(sharedLabels) as (keyof typeof sharedLabels)[]).map(kind => row(kind, kind, sharedLabels[kind], usage.shared[kind]))}
    </ul>
    <p className="workspace-usage-total"><span>Sentinel total <strong>{formatBytes(usage.total)}</strong></span>
      {usage.free !== null && <span>{formatBytes(usage.free)} free{usage.capacity ? ` of ${formatBytes(usage.capacity)}` : ''}</span>}</p>
  </>;
}
