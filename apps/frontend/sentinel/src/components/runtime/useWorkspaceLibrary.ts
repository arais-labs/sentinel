import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import type { Machine, Workspace } from '../../types/api';

type RuntimeStatus = Pick<Workspace, 'container_state' | 'container_error' | 'container_message' | 'resources' | 'recovery_available' | 'recovery_backup'>;

/** Catalog data and each workspace's live connection load independently. */
export function useWorkspaceLibrary(instanceName: string | undefined, active: boolean) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [loading, setLoading] = useState(true);
  const [machinesLoading, setMachinesLoading] = useState(true);
  const [error, setError] = useState('');
  const activeRef = useRef(active);
  activeRef.current = active;
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  const reload = useCallback(() => refreshRef.current(), []);

  useEffect(() => {
    let disposed = false, catalogPending = false, machinesPending = false;
    const pending = new Set<string>();
    const statuses = new Map<string, RuntimeStatus>();
    const prefix = instanceName ? `/instances/${encodeURIComponent(instanceName)}` : '';
    setWorkspaces([]); setMachines([]); setLoading(true); setMachinesLoading(true); setError('');

    const refreshStatus = async (id: string) => {
      if (pending.has(id)) return;
      pending.add(id);
      let status: RuntimeStatus;
      try {
        const result = await api.get<Workspace>(`${prefix}/workspaces/${id}/status`, { timeoutMs: 12_000 });
        const { container_state, container_error, container_message, resources, recovery_available, recovery_backup } = result;
        status = { container_state, container_error, container_message, resources, recovery_available, recovery_backup };
      } catch (error) {
        status = { container_state: 'unavailable', container_message: null, recovery_available: false,
          container_error: error instanceof Error ? error.message : 'Could not check connection. Retrying…' };
      } finally { pending.delete(id); }
      if (disposed) return;
      statuses.set(id, status);
      setWorkspaces(rows => rows.map(row => row.id === id ? { ...row, ...status } : row));
    };
    const refreshMachines = async () => {
      if (machinesPending) return;
      machinesPending = true;
      try {
        const rows = await api.get<Machine[]>('/machines');
        if (!disposed) setMachines(rows);
      } catch { /* Machine names must not block the workspace catalog. Retry on the next refresh. */ }
      finally { machinesPending = false; if (!disposed) setMachinesLoading(false); }
    };
    const refresh = async () => {
      void refreshMachines();
      if (catalogPending) return;
      catalogPending = true;
      try {
        const rows = await api.get<Workspace[]>(`${prefix}/workspaces?include_runtime=false`);
        if (disposed) return;
        setWorkspaces(rows.map(row => ({ ...row, ...statuses.get(row.id) })));
        setError('');
        for (const row of rows) void refreshStatus(row.id);
      } catch (error) {
        if (!disposed) setError(error instanceof Error ? error.message : 'Could not load workspaces');
      } finally { catalogPending = false; if (!disposed) setLoading(false); }
    };
    refreshRef.current = refresh;
    void refresh();
    const timer = window.setInterval(() => {
      if (activeRef.current && document.visibilityState === 'visible') void refresh();
    }, 2500);
    return () => { disposed = true; window.clearInterval(timer); refreshRef.current = async () => {}; };
  }, [instanceName]);

  return { workspaces, machines, loading, machinesLoading, error, reload };
}
