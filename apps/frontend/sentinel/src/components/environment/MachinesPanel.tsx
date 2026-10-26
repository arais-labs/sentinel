import { RemoteRuntimeDialog } from './RemoteRuntimeDialog';
import { MachineRuntimeStatus } from './MachineRuntimeStatus';
import { useState, useEffect, useCallback } from 'react';
import { notificationPublisher } from '../../lib/notifications';
import { Check, ShieldAlert, Info, Loader2, RefreshCw, KeyRound, Pencil, Plus, Trash2, Server, Wifi, Power, Square, RotateCcw, Unlink, X } from 'lucide-react';
import { AppShell } from '../../components/AppShell';
import { Panel } from '../../components/ui/Panel';
import { StatusChip } from '../../components/ui/StatusChip';
import { LocalMachineForm } from './LocalMachineForm';
import { MACHINE_PROVIDER_TILES, buildManagedMachineBody } from '../../lib/machine-create';
import { api } from '../../lib/api';
import { useInstanceName } from '../../lib/workspace-context';
import type { Machine, MachineProvider, MachineLifecycleResponse, MachineCapabilitiesResponse, MachineJob, MachineTestResponse } from '../../types/api';

const notify = notificationPublisher('Machines');

const emptyMachineForm = {
  name: '',
  host: '',
  port: '22',
  username: '',
  auth_type: 'private_key' as 'private_key' | 'password',
  private_key: '',
  password: '',
};

const RUNTIME_VERIFICATION_FIELDS = new Set<keyof typeof emptyMachineForm>([
  'host', 'port', 'username', 'auth_type', 'private_key', 'password',
]);

const machineInputClass = 'h-10 rounded-lg border border-(--border-subtle) bg-(--surface-1) px-3 text-xs font-medium text-(--text-primary) placeholder:text-(--text-muted) outline-hidden transition-colors focus:border-(--accent-solid)';
const machineTextAreaClass = 'min-h-28 rounded-lg border border-(--border-subtle) bg-(--surface-1) px-3 py-2 font-mono text-[10px] font-medium leading-relaxed text-(--text-primary) placeholder:text-(--text-muted) outline-hidden transition-colors focus:border-(--accent-solid)';

function machineProviderLabel(machine: Machine): string {
  if (machine.provider !== 'ssh') return machine.provider;
  if (machine.auth_type === 'private_key') return 'Key';
  if (machine.auth_type === 'password') return 'Password';
  return 'SSH';
}

export function MachinesPanel() {
  const instanceName = useInstanceName() ?? null;
  const [enrollingMachine, setEnrollingMachine] = useState<Machine | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [runtimeRevision, setRuntimeRevision] = useState(0);
  const [loadingMachines, setLoadingMachines] = useState(true);
  const [savingMachine, setSavingMachine] = useState(false);
  const [testingMachine, setTestingMachine] = useState(false);
  const [machineForm, setMachineForm] = useState(emptyMachineForm);
  const [editingMachineId, setEditingMachineId] = useState<string | null>(null);
  const [addingNewTarget, setAddingNewTarget] = useState(false);
  const [confirmDeleteTargetId, setConfirmDeleteTargetId] = useState<string | null>(null);
  const [deletingMachine, setDeletingMachine] = useState(false);
  const [machineVerified, setMachineVerified] = useState(false);
  const [machineResolvedHome, setMachineResolvedHome] = useState<string | null>(null);
  const [machineCapabilities, setMachineCapabilities] = useState<MachineCapabilitiesResponse | null>(null);
  const [creatingProvider, setCreatingProvider] = useState<string | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<MachineProvider | null>(null);
  const [creatingJobMessage, setCreatingJobMessage] = useState<string | null>(null);

  function updateMachineForm(updates: Partial<typeof emptyMachineForm>) {
    setMachineForm((f) => ({ ...f, ...updates }));
    const touchesVerification = (Object.keys(updates) as Array<keyof typeof emptyMachineForm>)
      .some((k) => RUNTIME_VERIFICATION_FIELDS.has(k));
    if (touchesVerification) {
      setMachineVerified(false);
      setMachineResolvedHome(null);
    }
  }

  const fetchMachines = useCallback(async () => {
    if (!instanceName) return;
    setLoadingMachines(true);
    try {
      const [capabilities, rows] = await Promise.all([
        api.get<MachineCapabilitiesResponse>('/machines/capabilities'),
        api.get<Machine[]>('/machines'),
      ]);
      setMachineCapabilities(capabilities);
      setMachines(rows);
      setRuntimeRevision(value => value + 1);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to load machines');
    } finally {
      setLoadingMachines(false);
    }
  }, [instanceName]);

  useEffect(() => { void fetchMachines(); }, [fetchMachines]);

  function startEditingMachine(target: Machine) {
    setAddingNewTarget(false);
    setSelectedProvider(target.provider);
    setConfirmDeleteTargetId(null);
    setEditingMachineId(target.id);
    setMachineForm({
      name: target.name,
      host: target.host ?? '',
      port: String(target.port ?? 22),
      username: target.username ?? '',
      auth_type: target.auth_type ?? 'private_key',
      private_key: '',
      password: '',
    });
    setMachineVerified(true);
    setMachineResolvedHome(null);
  }

  function startAddingMachine() {
    setEditingMachineId(null);
    setConfirmDeleteTargetId(null);
    setMachineForm(emptyMachineForm);
    setAddingNewTarget(true);
    setSelectedProvider(null);
    setMachineVerified(false);
    setMachineResolvedHome(null);
  }

  function autoSuggestName(provider: MachineProvider): string {
    const prefix = provider;
    const taken = new Set(machines.map((r) => r.name));
    for (let n = 1; n < 999; n++) {
      const candidate = `${prefix}-${n}`;
      if (!taken.has(candidate)) return candidate;
    }
    return `${prefix}-new`;
  }

  function pickProvider(provider: MachineProvider) {
    setSelectedProvider(provider);
    setMachineForm((f) => ({ ...f, name: f.name || autoSuggestName(provider) }));
    if (provider !== 'ssh') {
      setMachineVerified(false);
      setMachineResolvedHome(null);
    }
  }

  function resetMachineForm() {
    setEditingMachineId(null);
    setAddingNewTarget(false);
    setSelectedProvider(null);
    setMachineForm(emptyMachineForm);
    setMachineVerified(false);
    setMachineResolvedHome(null);
    setCreatingJobMessage(null);
  }

  async function handleDeleteMachine(targetId: string) {
    setDeletingMachine(true);
    try {
      await api.delete(`/machines/${targetId}`);
      notify.success('Machine removed');
      setConfirmDeleteTargetId(null);
      if (editingMachineId === targetId) resetMachineForm();
      await fetchMachines();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to remove machine');
    } finally {
      setDeletingMachine(false);
    }
  }

  async function handleSaveMachine(testOnly = false) {
    const isEditing = editingMachineId !== null;
    const secretValue = machineForm.auth_type === 'private_key'
      ? machineForm.private_key.trim()
      : machineForm.password;
    const body = {
      name: machineForm.name.trim(),
      host: machineForm.host.trim(),
      port: Number(machineForm.port || 22),
      username: machineForm.username.trim(),
      ...(isEditing && !secretValue ? {} : { auth_type: machineForm.auth_type }),
      private_key: machineForm.auth_type === 'private_key' && secretValue ? machineForm.private_key : undefined,
      password: machineForm.auth_type === 'password' && secretValue ? machineForm.password : undefined,
    };
    if (testOnly) {
      if (!body.host || !body.username) {
        notify.error('Host and username are required to test');
        return;
      }
      if (!isEditing && !secretValue) {
        notify.error(machineForm.auth_type === 'private_key' ? 'Private key is required' : 'SSH password is required');
        return;
      }
      if (!secretValue) {
        notify.error('Enter the SSH secret to test this machine');
        return;
      }
      setTestingMachine(true);
      try {
        const result = await api.post<MachineTestResponse>(
          '/machines/test',
          { ...body, auth_type: machineForm.auth_type },
          { timeoutMs: 20_000 },
        );
        if (result.ok) {
          setMachineVerified(true);
          setMachineResolvedHome(result.resolved_home ?? null);
          notify.success(result.detail);
        } else {
          setMachineVerified(false);
          setMachineResolvedHome(null);
          notify.error(result.detail);
        }
      } catch (error) {
        setMachineVerified(false);
        setMachineResolvedHome(null);
        notify.error(error instanceof Error ? error.message : 'Machine test failed');
      } finally {
        setTestingMachine(false);
      }
      return;
    }
    if (!body.name || !body.host || !body.username) {
      notify.error('Machine name, host, username, and Sentinel data directory are required');
      return;
    }
    if (!isEditing && !secretValue) {
      notify.error(machineForm.auth_type === 'private_key' ? 'Private key is required' : 'SSH password is required');
      return;
    }
    setSavingMachine(true);
    try {
      const target = isEditing
        ? await api.patch<Machine>(`/machines/${editingMachineId}`, body)
        : await api.post<Machine>('/machines', { ...body, provider: 'ssh' });
      resetMachineForm();
      await fetchMachines();
      notify.success(isEditing ? 'Machine updated' : 'Machine saved');
      if (!isEditing) setEnrollingMachine(target);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to save machine');
    } finally {
      setSavingMachine(false);
    }
  }

  async function saveLocalMachineEdit() {
    if (!editingMachineId) return;
    const name = machineForm.name.trim();
    if (!name) {
      notify.error('Name and Sentinel data directory are required');
      return;
    }
    setSavingMachine(true);
    try {
      await api.patch<Machine>(`/machines/${editingMachineId}`, { name });
      resetMachineForm();
      await fetchMachines();
      notify.success('Machine updated');
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to update machine');
    } finally {
      setSavingMachine(false);
    }
  }

  async function createMachine(provider: Exclude<MachineProvider, 'ssh'>) {
    const name = machineForm.name.trim();
    if (!name) {
      notify.error('Machine name is required');
      return;
    }
    setCreatingProvider(provider);
    setCreatingJobMessage('Submitting…');
    try {
      const response = await api.post<MachineLifecycleResponse>(
        '/machines',
        buildManagedMachineBody(provider, name),
        { timeoutMs: 30_000 },
      );
      await fetchMachines();
      // Poll the job until terminal status, showing the latest event message.
      let jobId: string | null = response.job.id;
      let lastStatus: 'queued' | 'running' | 'succeeded' | 'failed' = response.job.status;
      while (jobId && (lastStatus === 'queued' || lastStatus === 'running')) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const job = await api.get<Pick<MachineJob, 'id' | 'status' | 'events' | 'error'>>(`/machines/jobs/${jobId}`);
          lastStatus = job.status;
          const latest = job.events.length > 0 ? job.events[job.events.length - 1].message : null;
          setCreatingJobMessage(latest ?? (lastStatus === 'queued' ? 'Queued…' : 'Working…'));
          if (lastStatus === 'failed') {
            notify.error(job.error ?? 'Machine creation failed');
          } else if (lastStatus === 'succeeded') {
            notify.success(`Machine ${name} ready`);
          }
        } catch {
          // Polling glitch — keep trying for the next interval, but don't spam toasts.
        }
      }
      await fetchMachines();
      if (lastStatus === 'succeeded') {
        resetMachineForm();
      }
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to create machine');
    } finally {
      setCreatingProvider(null);
      setCreatingJobMessage(null);
    }
  }

  async function runMachineAction(machine: Machine, action: 'start' | 'stop' | 'rebuild' | 'delete') {
    try {
      const response = await api.post<MachineLifecycleResponse>(
        `/machines/${machine.id}/${action}`,
        undefined,
        { timeoutMs: 30_000 },
      );
      notify.success(`Machine job started: ${response.job.action}`);
      await fetchMachines();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : `Failed to ${action} machine`);
    }
  }


  function renderMachineForm() {
    const isEditing = editingMachineId !== null;
    return (
      <div className="rounded-xl border border-(--accent-solid)/40 bg-(--surface-0) p-4 space-y-4 animate-in fade-in slide-in-from-top-1 duration-200">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-bold uppercase tracking-widest text-(--accent-solid)">
            {isEditing ? 'Edit machine' : 'New machine'}
          </span>
          {machineVerified ? (
            <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">
              <Check size={10} /> Verified
            </span>
          ) : (
            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">
              Not verified
            </span>
          )}
          <div className="flex-1" />
          <button
            type="button"
            onClick={resetMachineForm}
            className="p-1 rounded-md text-(--text-muted) hover:text-(--text-primary) transition-colors"
            title="Cancel"
          >
            <X size={14} />
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Name</span>
            <input
              value={machineForm.name}
              onChange={(e) => updateMachineForm({ name: e.target.value })}
              className={`${machineInputClass} w-full`}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Username</span>
            <input
              value={machineForm.username}
              onChange={(e) => updateMachineForm({ username: e.target.value })}
              className={`${machineInputClass} w-full`}
            />
          </label>
          <label className="space-y-1 sm:col-span-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Host</span>
            <input
              value={machineForm.host}
              onChange={(e) => updateMachineForm({ host: e.target.value })}
              placeholder="hostname or IP"
              className={`${machineInputClass} w-full font-mono`}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Port</span>
            <input
              value={machineForm.port}
              onChange={(e) => updateMachineForm({ port: e.target.value })}
              className={`${machineInputClass} w-full font-mono`}
            />
          </label>
        </div>



        <div className="space-y-2">
          <span className="block text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Authentication</span>
          <div className="flex rounded-lg bg-(--surface-2) p-0.5 w-fit">
            {(['private_key', 'password'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => updateMachineForm({ auth_type: mode })}
                className={`px-3 py-1.5 rounded-md text-[10px] font-bold uppercase tracking-widest transition-all ${
                  machineForm.auth_type === mode
                    ? 'bg-(--accent-solid) text-(--app-bg)'
                    : 'text-(--text-muted) hover:text-(--text-primary)'
                }`}
              >
                {mode === 'private_key' ? 'Private key' : 'Password'}
              </button>
            ))}
          </div>
          {machineForm.auth_type === 'private_key' ? (
            <textarea
              value={machineForm.private_key}
              onChange={(e) => updateMachineForm({ private_key: e.target.value })}
              placeholder={isEditing ? '' : 'Paste your SSH private key'}
              className={`${machineTextAreaClass} w-full`}
            />
          ) : (
            <input
              type="password"
              value={machineForm.password}
              onChange={(e) => updateMachineForm({ password: e.target.value })}
              className={`${machineInputClass} w-full`}
            />
          )}
          {isEditing && (
            <p className="text-[10px] leading-relaxed text-(--text-muted)">
              Leave the SSH secret empty to keep the existing credential.
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-(--border-subtle)">
          <button
            type="button"
            onClick={resetMachineForm}
            className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest"
          >
            Cancel
          </button>
          <div className="flex-1" />
          {!machineVerified && (
            <span className="text-[10px] text-amber-400 font-medium">
              Test connection to enable save
            </span>
          )}
          <button
            type="button"
            onClick={() => void handleSaveMachine(true)}
            disabled={testingMachine || savingMachine}
            className="btn-secondary h-10 px-3 gap-2 text-[10px] font-bold uppercase tracking-widest"
          >
            {testingMachine ? <Loader2 size={14} className="animate-spin" /> : <Wifi size={14} />}
            Test connection
          </button>
          <button
            type="button"
            onClick={() => void handleSaveMachine(false)}
            disabled={savingMachine || testingMachine || !machineVerified}
            title={!machineVerified ? 'Run Test connection successfully before saving' : undefined}
            className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {savingMachine ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
            {isEditing ? 'Save changes' : 'Save machine'}
          </button>
        </div>
      </div>
    );
  }

  function renderTargetCard(target: Machine) {
    const isConfirmingDelete = confirmDeleteTargetId === target.id;

    return (
      <div
        key={target.id}
        className="machine-card group relative rounded-xl border border-(--border-subtle) bg-(--surface-0)"
      >
        <div className="flex items-stretch">
          <div className="flex-1 min-w-0 text-left px-4 py-3 flex items-start gap-3">
            <div className={`mt-1 h-2.5 w-2.5 rounded-full shrink-0 ${target.status === 'ready' || target.status === 'running' ? 'bg-emerald-500' : 'bg-(--text-muted)'}`} />
            <div className="flex-1 min-w-0 space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-bold truncate">{target.name}</span>
                <StatusChip
                  label={machineProviderLabel(target)}
                  tone="info"
                  className="scale-90"
                />
                <StatusChip
                  label={target.provider === 'ssh' ? `SSH ${target.status === 'ready' ? 'verified' : target.status}` : target.status}
                  tone={target.status === 'ready' || target.status === 'running' ? 'good' : target.status === 'error' ? 'danger' : 'default'}
                  className="scale-90"
                />
              </div>
              <div className="font-mono text-[10px] text-(--text-muted) truncate">
                {target.provider === 'local'
                  ? 'This Mac'
                  : target.username && target.host
                    ? `${target.username}@${target.host}:${target.port ?? 22}`
                    : 'SSH details pending'}
              </div>
              {target.status_detail && (
                <div className="mt-2 flex items-start gap-2 rounded-md border border-rose-500/20 bg-rose-500/10 px-2.5 py-2 text-[10px] leading-relaxed text-rose-300">
                  <ShieldAlert size={12} className="mt-0.5 shrink-0" />
                  <span className="min-w-0">{target.status_detail}</span>
                </div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1 pr-3 shrink-0">
            {isConfirmingDelete ? (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => void handleDeleteMachine(target.id)}
                  disabled={deletingMachine}
                  className="text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:opacity-70 transition-opacity px-2 py-1"
                >
                  {deletingMachine ? <Loader2 size={12} className="animate-spin" /> : 'Confirm'}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDeleteTargetId(null)}
                  className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted) hover:text-(--text-primary) transition-colors px-2 py-1"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <>
                {!(machineCapabilities?.providers.find((p) => p.provider === target.provider)?.has_lifecycle ?? true) && (
                  <button
                    type="button"
                    onClick={() => startEditingMachine(target)}
                    className="p-1.5 rounded-md text-(--text-muted) hover:text-(--text-primary) hover:bg-(--surface-2) transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                    title="Edit machine"
                  >
                    <Pencil size={13} />
                  </button>
                )}
                {(machineCapabilities?.providers.find((p) => p.provider === target.provider)?.has_lifecycle ?? false) && (
                  <>
                    <button type="button" onClick={() => void runMachineAction(target, 'start')} className="p-1.5 rounded-md text-(--text-muted) hover:text-emerald-400 hover:bg-(--surface-2) transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100" title="Start machine">
                      <Power size={13} />
                    </button>
                    <button type="button" onClick={() => void runMachineAction(target, 'stop')} className="p-1.5 rounded-md text-(--text-muted) hover:text-(--text-primary) hover:bg-(--surface-2) transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100" title="Stop machine">
                      <Square size={13} />
                    </button>
                    <button type="button" onClick={() => void runMachineAction(target, 'rebuild')} className="p-1.5 rounded-md text-(--text-muted) hover:text-(--text-primary) hover:bg-(--surface-2) transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100" title="Rebuild machine">
                      <RotateCcw size={13} />
                    </button>
                  </>
                )}
                <button
                  type="button"
                  onClick={() => setConfirmDeleteTargetId(target.id)}
                  className="p-1.5 rounded-md text-(--text-muted) hover:text-rose-500 hover:bg-rose-500/10 transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
                  title="Delete machine"
                >
                  <Trash2 size={13} />
                </button>
              </>
            )}
          </div>
        </div>
        {target.provider === 'ssh' && <MachineRuntimeStatus machineId={target.id} revision={runtimeRevision} onAction={() => setEnrollingMachine(target)} />}
      </div>
    );
  }

  const isFormOpen = editingMachineId !== null || addingNewTarget;

  return (
    <>
      {enrollingMachine && <RemoteRuntimeDialog machine={enrollingMachine} onClose={() => setEnrollingMachine(null)} onInstalled={fetchMachines} />}
      <div className="w-full animate-in fade-in duration-300">
        <Panel className="machines-panel p-6 space-y-4 md:col-span-2">
          <div className="machines-heading flex items-center gap-3 border-b border-(--border-subtle) pb-4">
            <div className="p-2 rounded-lg bg-(--surface-2) text-(--accent-solid)">
              <KeyRound size={20} />
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold uppercase tracking-widest">Machines</h2>
              <p className="text-[10px] text-(--text-muted) font-medium uppercase tracking-tighter">Local computers, SSH hosts, and managed environments</p>
            </div>
            <button onClick={() => { void fetchMachines(); }}
              className="text-(--text-muted) hover:text-(--text-primary) transition-colors p-1"
              title="Refresh machines">
              <RefreshCw size={14} className={loadingMachines ? 'animate-spin' : ''} />
            </button>
          </div>

          {/* Existing machines list */}
          {loadingMachines && machines.length === 0 ? (
            <div className="flex items-center justify-center py-8 text-(--text-muted)">
              <Loader2 size={20} className="animate-spin" />
            </div>
          ) : machines.length === 0 && !addingNewTarget ? (
            <div className="flex flex-col items-center justify-center py-12 text-center gap-4 rounded-xl border border-dashed border-(--border-subtle) bg-(--surface-0)">
              <div className="p-3 rounded-full bg-(--surface-1) border border-(--border-subtle)">
                <Server size={24} className="text-(--text-muted)" />
              </div>
              <div className="space-y-1 max-w-sm">
                <p className="text-xs font-bold uppercase tracking-widest">No machines yet</p>
                <p className="text-[11px] text-(--text-muted) leading-relaxed">
                  Use this computer or connect an SSH host you already manage.
                </p>
              </div>
              <button onClick={startAddingMachine} className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest">
                <Plus size={14} /> Add your first machine
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {machines.map((target) =>
                editingMachineId === target.id
                  ? (
                    <div key={target.id}>
                      {target.provider === 'local' ? (
                        <LocalMachineForm
                          mode="edit"
                          name={machineForm.name}
                          onNameChange={(v) => updateMachineForm({ name: v })}
                          isBusy={savingMachine}
                          inputClass={machineInputClass}
                          cancelLabel="Cancel"
                          onCancel={resetMachineForm}
                          onSubmit={() => void saveLocalMachineEdit()}
                          surface="card"
                        />
                      ) : renderMachineForm()}
                    </div>
                  )
                  : renderTargetCard(target),
              )}
            </div>
          )}

          {/* Add-machine affordance — collapses chooser/form */}
          {addingNewTarget && !selectedProvider && (() => {
            const capByProvider = new Map((machineCapabilities?.providers ?? []).map((p) => [p.provider, p]));
            const tiles = MACHINE_PROVIDER_TILES;
            return (
              <div className="rounded-xl border border-(--accent-solid)/40 bg-(--surface-0) p-4 space-y-3 animate-in fade-in slide-in-from-top-1 duration-200">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-bold uppercase tracking-widest text-(--accent-solid)">Choose how to add a machine</span>
                  <button type="button" onClick={resetMachineForm} className="p-1 rounded-md text-(--text-muted) hover:text-(--text-primary) transition-colors" title="Cancel">
                    <X size={14} />
                  </button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {tiles.map((tile) => {
                    const cap = capByProvider.get(tile.id);
                    const isSsh = tile.id === 'ssh';
                    const available = isSsh ? true : (cap?.available ?? false);
                    const Icon = tile.icon;
                    return (
                      <button
                        key={tile.id}
                        type="button"
                        onClick={() => pickProvider(tile.id)}
                        className="group text-left rounded-lg border border-(--border-subtle) bg-(--surface-1) hover:border-(--accent-solid) hover:bg-(--surface-2) transition-all p-3 space-y-1.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <Icon size={16} className="text-(--text-secondary) group-hover:text-(--accent-solid) transition-colors" />
                          {isSsh ? null : available ? (
                            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400">Ready</span>
                          ) : (
                            <span className="text-[9px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400">Setup needed</span>
                          )}
                        </div>
                        <div className="text-[11px] font-bold text-(--text-primary)">{tile.label}</div>
                        <div className="text-[10px] text-(--text-muted) leading-relaxed">{tile.description}</div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* SSH form — selected from chooser */}
          {addingNewTarget && selectedProvider === 'ssh' && renderMachineForm()}

          {/* Local (this Mac) — selected from chooser */}
          {addingNewTarget && selectedProvider === 'local' && (() => {
            const cap = machineCapabilities?.providers.find((p) => p.provider === 'local');
            return (
              <LocalMachineForm
                mode="create"
                available={cap?.available ?? false}
                detail={cap?.detail}
                name={machineForm.name}
                onNameChange={(v) => updateMachineForm({ name: v })}
                isBusy={creatingProvider === 'local'}
                jobMessage={creatingJobMessage}
                inputClass={machineInputClass}
                cancelLabel="Cancel"
                onCancel={resetMachineForm}
                onRecheck={() => { void fetchMachines(); }}
                onSubmit={() => void createMachine('local')}
                surface="card"
              />
            );
          })()}

          {/* Add button — visible only when no flow is active */}
          {!addingNewTarget && editingMachineId === null && machines.length > 0 && (
            <button
              type="button"
              onClick={startAddingMachine}
              className="machines-add w-full h-12 rounded-xl border border-dashed border-(--border-subtle) bg-(--surface-0) hover:border-(--accent-solid) hover:bg-(--surface-1) text-(--text-muted) hover:text-(--accent-solid) transition-colors flex items-center justify-center gap-2 text-[10px] font-bold uppercase tracking-widest"
            >
              <Plus size={14} /> Add machine
            </button>
          )}

          <div className="machines-note bg-(--surface-1) p-3 rounded-xl border border-(--border-subtle) flex items-start gap-2.5">
            <Info size={14} className="text-(--accent-solid) shrink-0 mt-0.5" />
            <p className="text-[10px] text-(--text-secondary) leading-relaxed">
              Machines are shared across instances. Choose a machine when creating a workspace; multiple workspaces can use the same machine.
            </p>
          </div>
        </Panel>

      </div>
    </>
  );
}
