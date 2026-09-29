import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../lib/api';
import { runtimeUpdateRequirement, type RuntimeUpdateRequirement } from '../../lib/runtime-compatibility';
import type { Machine, Workspace } from '../../types/api';

type LibraryWorkspace = Workspace & { runtime_update?: RuntimeUpdateRequirement };
type RuntimeStatus = Pick<LibraryWorkspace, 'container_state' | 'container_error' | 'container_message' | 'resources' | 'recovery_available' | 'recovery_backup' | 'runtime_update'>;

/** Catalog data and each workspace's live connection load independently. */
export function useWorkspaceLibrary(instanceName: string | undefined, active: boolean) {
  const [workspaces, setWorkspaces] = useState<LibraryWorkspace[]>([]);
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
    const workerRows = new Map<string, Workspace[]>();
    const workerErrors = new Map<string, RuntimeStatus>();
    const workersPending = new Set<string>();
    let remoteMachines = new Set<string>();
    const prefix = instanceName ? `/instances/${encodeURIComponent(instanceName)}` : '';
    setWorkspaces([]); setMachines([]); setLoading(true); setMachinesLoading(true); setError('');

    const discovered = (rows: LibraryWorkspace[]) => {
      // Replace a cached catalog only after a successful discovery. Connection
      // errors must not erase workspaces, regardless of request completion order.
      const merged = new Map(rows.filter(row => !workerRows.has(row.machine_id)).map(row => [row.id, row]));
      for (const worker of workerRows.values()) for (const row of worker) merged.set(row.id, row);
      return Array.from(merged.values(), row => ({ ...row, ...workerErrors.get(row.machine_id) }));
    };
    const refreshWorker = async (machine: Machine) => {
      if (workersPending.has(machine.id)) return;
      workersPending.add(machine.id);
      try {
        const rows = await api.get<Workspace[]>(`${prefix}/workspaces/discover/${machine.id}`, { timeoutMs: 12_000 });
        if (!disposed) {
          workerRows.set(machine.id, rows);
          workerErrors.delete(machine.id);
          setWorkspaces(discovered);
        }
      } catch (reason) {
        if (!disposed) {
          workerErrors.set(machine.id, {
            container_state: 'unavailable' as const,
            container_error: reason instanceof Error ? reason.message : 'Worker disconnected',
            runtime_update: runtimeUpdateRequirement(reason),
          });
          setWorkspaces(discovered);
        }
      } finally { workersPending.delete(machine.id); }
    };

    const refreshStatus = async (id: string) => {
      if (pending.has(id)) return;
      pending.add(id);
      let status: RuntimeStatus;
      try {
        const result = await api.get<Workspace>(`${prefix}/workspaces/${id}/status`, { timeoutMs: 12_000 });
        const { container_state, container_error, container_message, resources, recovery_available, recovery_backup } = result;
        status = { container_state, container_error, container_message, resources, recovery_available, recovery_backup, runtime_update: undefined };
      } catch (error) {
        status = { container_state: 'unavailable', container_message: null, recovery_available: false,
          runtime_update: runtimeUpdateRequirement(error),
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
        if (!disposed) {
          setMachines(rows);
          remoteMachines = new Set(rows.filter(machine => machine.provider === 'ssh').map(machine => machine.id));
          setWorkspaces(current => discovered(current));
          for (const machine of rows) if (machine.provider === 'ssh') void refreshWorker(machine);
        }
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
        setWorkspaces(discovered(rows.map(row => ({ ...row, ...statuses.get(row.id) }))));
        setError('');
        for (const row of rows) if (!remoteMachines.has(row.machine_id)) void refreshStatus(row.id);
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
