import { useEffect, useState } from 'react';
import { ChevronRight, Loader2, Server, Settings } from 'lucide-react';
import { api } from '../../lib/api';
import { runtimePresentation, type RuntimeInspection } from '../../lib/runtime-status';
import { StatusChip } from '../ui/StatusChip';

export function MachineRuntimeStatus({ machineId, revision, onAction }: {
  machineId: string; revision: number; onAction: () => void;
}) {
  const [inspection, setInspection] = useState<RuntimeInspection>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setInspection(undefined); setFailed(false);
    const check = async () => {
      try {
        const value = await api.get<RuntimeInspection>(`/machines/${machineId}/runtime`);
        if (active) {
          setInspection(value);
          if (value.progress) timer = setTimeout(() => void check(), 2000);
        }
      } catch { if (active) setFailed(true); }
    };
    void check();
    return () => { active = false; clearTimeout(timer); };
  }, [machineId, revision]);
  const state = runtimePresentation(inspection, failed);
  const repairAction = state.action === 'Verify / repair…';
  return <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-(--border-subtle) px-4 py-2.5">
    <div className="flex min-w-0 items-center gap-2" role="status" title={state.description}>
      {state.busy ? <Loader2 size={12} className="shrink-0 animate-spin text-(--text-muted)" aria-hidden="true" /> : <Server size={12} className="shrink-0 text-(--text-muted)" aria-hidden="true" />}
      <span className="text-[10px] font-medium text-(--text-secondary)">Runtime</span>
      <StatusChip label={state.label} tone={state.tone} className={state.tone === 'warn' ? 'text-orange-700 dark:text-orange-300' : ''} />
    </div>
    {state.action && <button type="button" onClick={onAction} aria-haspopup="dialog" aria-label={state.action}
      className={repairAction ? 'ml-auto inline-flex size-7 shrink-0 items-center justify-center rounded-full text-(--text-secondary) transition-colors hover:bg-(--surface-2) hover:text-(--text-primary) focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--sentinel-blue)' : 'btn-secondary ml-auto h-7 gap-1.5 px-2.5 text-[10px] font-medium'}
      title={repairAction ? 'Verify or repair runtime' : state.description}>
      {repairAction ? <Settings size={15} aria-hidden="true" /> : <>{state.action}<ChevronRight size={12} aria-hidden="true" /></>}
    </button>}
  </div>;
}
