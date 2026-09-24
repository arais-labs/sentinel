import { AppShell } from '../AppShell';
import { resetWorkspaceBrowser } from '../files/useWorkspaceBrowser';
import { WorkspaceRecoveryDialog } from './WorkspaceRecoveryDialog';
import { RuntimeUpdateNotice } from './RuntimeUpdateNotice';
import { WorkspaceRemovalDialog } from './WorkspaceRemovalDialog';
import { WorkspaceReinstallDialog } from './WorkspaceReinstallDialog';
import { toolNames, toolLogos } from './workspaceTools';
import { distributionLogos, distributionNames } from './workspaceDistributions';
import { workspaceDesktops } from './workspaceDesktops';
import { useEffect, useState } from 'react';
import { useWorkspaceLibrary } from './useWorkspaceLibrary';
import { Code2, FolderOpen, Server, Plus, Pencil, Trash2, Loader2, Play, Square, RefreshCw, Wrench, Search, ChevronDown, Monitor, Terminal } from 'lucide-react';
import { notificationPublisher } from '../../lib/notifications';
import { MachinesPanel } from './MachinesPanel';
import { WorkspaceEditor, type WorkspaceDraft } from './WorkspaceEditor';
import { api } from '../../lib/api';
import { workspaceSaveError } from './workspaceValidation';
import { useInstanceName } from '../../lib/workspace-context';
import type { Workspace } from '../../types/api';
import './workspaces-panel.css';

const notify = notificationPublisher('Workspaces');

export function WorkspacesPanel({ onboarding = false, onAddMachine, page = false }: { onboarding?: boolean; onAddMachine?: () => void; page?: boolean }) {
  const instanceName = useInstanceName();
  const [tab, setTab] = useState<'workspaces' | 'machines'>('workspaces');
  const [search, setSearch] = useState('');
  const [machineFilter, setMachineFilter] = useState('');
  useEffect(() => { setSearch(''); setMachineFilter(''); }, [instanceName]);
  const { workspaces, machines, loading, machinesLoading, error, reload } = useWorkspaceLibrary(instanceName, tab === 'workspaces');
  const [editor, setEditor] = useState<Workspace | 'new' | null>(null);
  const [saving, setSaving] = useState(false);
  const [changing, setChanging] = useState<string | null>(null);
  const [recovering, setRecovering] = useState<Workspace | null>(null);
  const [removing, setRemoving] = useState<Workspace | null>(null);
  const [reinstalling, setReinstalling] = useState<Workspace | null>(null);
  const [reinstallDraft, setReinstallDraft] = useState<WorkspaceDraft | null>(null);
  const query = search.trim().toLocaleLowerCase();
  const visibleWorkspaces = workspaces.filter(space =>
    (!machineFilter || space.machine_id === machineFilter)
    && `${space.name} ${space.directory} ${machines.find(machine => machine.id === space.machine_id)?.name ?? ''}`.toLocaleLowerCase().includes(query));
  const filtering = Boolean(query || machineFilter);
  async function changeState(space: Workspace, action: 'start' | 'stop') {
    setChanging(space.id);
    try { await api.post(`/workspaces/${space.id}/${action}`, {}); await reload(); }
    catch (error) { notify.error(error instanceof Error ? error.message : 'Could not update workspace'); }
    finally { setChanging(null); }
  }
  function edit(workspace: Workspace | 'new') {
    setEditor(workspace);
  }
  async function save(draft: WorkspaceDraft) {
    if (!editor) return;
    if (editor !== 'new' && draft.distribution !== (editor.distribution ?? 'alpine')) {
      setReinstallDraft(draft);
      setReinstalling(editor);
      setEditor(null);
      return;
    }
    setSaving(true);
    try {
      if (editor === 'new') await api.post('/workspaces', draft);
      else await api.patch(`/workspaces/${editor.id}`, { name: draft.name, directory: draft.directory, desktop: draft.desktop, browser: draft.browser, development_tools: draft.development_tools, resources: draft.resources, revision: editor.revision });
      if (instanceName && editor !== 'new' && draft.directory !== editor.directory) resetWorkspaceBrowser(instanceName, editor.id);
      setEditor(null); await reload();
    } catch (error) { notify.error(workspaceSaveError(error)); }
    finally { setSaving(false); }
  }
  const navigation = <div className="workspace-library-tabs" role="group" aria-label="Workspace management">
    {(['workspaces', 'machines'] as const).map(value => <button key={value} aria-pressed={tab === value} onClick={() => setTab(value)} className="chat-header-pill workspace-library-tab">{value === 'workspaces' ? <FolderOpen size={14} /> : <Server size={14} />}{value === 'workspaces' ? 'Workspaces' : 'Machines'}</button>)}
  </div>;
  const createButton = tab === 'workspaces' && !loading && machines.length > 0
    ? <button className={page ? "workspace-library-create-local" : "btn-primary h-9 px-4 gap-2 text-xs"} onClick={() => edit('new')}><Plus size={15} />New workspace</button>
    : null;
  const content = <>
    <div className={`workspace-library${page ? " workspace-library--page" : ""}${onboarding ? " workspace-library--onboarding" : ""}`}>
      {!onboarding && <div className="workspace-library-navigation">{page ? <div className="workspace-library-switch" role="group" aria-label="Workspace management"><span aria-hidden="true" className="workspace-library-switch-indicator" style={{ transform: `translateX(${tab === 'machines' ? 100 : 0}%)` }} />{(['workspaces', 'machines'] as const).map(value => <button key={value} aria-pressed={tab === value} onClick={() => setTab(value)}>{value === 'workspaces' ? <FolderOpen size={14} /> : <Server size={14} />}{value}</button>)}</div> : navigation}{createButton}</div>}
      {tab === 'workspaces' && error && <div role="alert" className="workspace-container-error">{error} <button type="button" onClick={() => void reload()}>Retry</button></div>}
      {tab === 'workspaces' && !loading && <>
        <div className="workspace-library-filters">
          <div className="workspace-library-search">
            <Search size={15} aria-hidden="true" />
            <input type="search" aria-label="Search workspaces" placeholder="Search workspaces…" value={search} onChange={event => setSearch(event.target.value)} className="input-field" />
          </div>
          <div className="workspace-library-machine-filter">
          <select aria-label="Filter by machine" value={machineFilter} onChange={event => setMachineFilter(event.target.value)} className="input-field">
            <option value="">All machines</option>
            {machines.map(machine => <option key={machine.id} value={machine.id}>{machine.name}</option>)}
            {machineFilter && !machines.some(machine => machine.id === machineFilter) && <option value={machineFilter}>Machine unavailable</option>}
          </select>
          <ChevronDown size={14} aria-hidden="true" />
          </div>
        </div>
        <div className="workspace-library-toolbar"><span className="workspace-library-count" role="status">{filtering ? `${visibleWorkspaces.length} of ${workspaces.length} workspaces` : `${workspaces.length} ${workspaces.length === 1 ? 'workspace' : 'workspaces'}`}</span>{onboarding && createButton}</div>
      </>}
      {tab === 'machines' ? <MachinesPanel /> : loading ? <div className="py-16 flex justify-center"><Loader2 className="animate-spin" size={22} /></div> : workspaces.length === 0 && error ? null : workspaces.length === 0 ? <div className="py-20 flex flex-col items-center text-center gap-4">
        <FolderOpen size={36} className="text-(--text-muted)" />
        <h2 className="text-lg font-semibold">A place for your projects</h2>
        <p className="text-sm text-(--text-secondary) max-w-md">Give your project an isolated Linux environment. Reuse its tools, packages, and containers across conversations.</p>
        {machines.length === 0 && <button onClick={() => onAddMachine ? onAddMachine() : setTab('machines')} className="btn-primary h-9 px-4 gap-2 text-xs"><Plus size={15} />Add a machine</button>}
      </div> : visibleWorkspaces.length === 0 ? <div className="py-16 flex flex-col items-center text-center gap-4">
        <Search size={28} className="text-(--text-muted)" />
        <p className="text-sm text-(--text-secondary)">No matching workspaces</p>
        <button className="workspace-library-create-local" onClick={() => { setSearch(''); setMachineFilter(''); }}>Clear filters</button>
      </div> : <div className="workspace-library-grid">
        {visibleWorkspaces.map(space => <article key={space.id} className="workspace-library-card">
          <div className="workspace-library-card-heading">
            <div className="workspace-library-icon"><FolderOpen size={22} strokeWidth={1.6} /></div>
            <div className="workspace-library-identity"><h2 title={space.name}>{space.name}</h2><span className="workspace-library-machine"><Server size={12} />{machines.find(m => m.id === space.machine_id)?.name ?? (machinesLoading ? 'Loading machine…' : 'Machine unavailable')}</span></div>
            <div className="workspace-container-status" data-state={space.container_state || 'stopped'} role="status">{['checking', 'preparing', 'recovering'].includes(space.container_state || '') ? <Loader2 size={13} className="animate-spin shrink-0" /> : <span className="workspace-container-dot" />}<span>{space.container_state === 'preparing' ? 'Preparing' : ({ checking: 'Checking connection…', running: 'Running', stopped: 'Stopped', stopping: 'Stopping', recovering: 'Recovering', failed: 'Needs attention', unavailable: 'Runtime unavailable' })[space.container_state || 'stopped']}</span></div>
          </div>
          <div className="workspace-library-environment" aria-label="Workspace environment">
            <span><img src={distributionLogos[space.distribution ?? 'alpine']} alt="" />{distributionNames[space.distribution ?? 'alpine']}</span>
            <span>{workspaceDesktops[space.desktop ?? 'none'].logo
              ? <img src={workspaceDesktops[space.desktop ?? 'none'].logo} alt="" />
              : space.desktop && space.desktop !== 'none' ? <Monitor size={16} aria-hidden="true" /> : <Terminal size={16} aria-hidden="true" />}
              {space.desktop && space.desktop !== 'none' ? workspaceDesktops[space.desktop].name.split(' · ')[0] : 'No desktop'}</span>
          </div>
          {space.container_message && <p className="workspace-container-message">{space.container_message}</p>}
          {space.runtime_update ? <div className="workspace-container-error"><RuntimeUpdateNotice requirement={space.runtime_update} machineId={space.machine_id} onUpdated={reload} /></div>
            : space.container_error && <p className="workspace-container-error">{space.recovery_available ? 'This workspace needs recovery. Try repair, reinstall Linux, or remove the workspace.' : space.container_error}</p>}
          <div className="workspace-library-location"><span>Project folder</span><p title={space.directory}>{space.directory}</p></div>
          {space.resources && <div className="workspace-library-specs"><span>{space.resources.cpus} CPUs</span><span>{space.resources.memory_gib} GiB RAM</span><span title="Private workspace disk capacity; your project folder is separate">{space.resources.disk_gib} GiB disk</span></div>}
          {!!space.development_tools?.length && <div className="workspace-library-tools"><span className="workspace-library-tools-label">Tools</span><div className="workspace-library-tool-icons">{space.development_tools.map(tool => <span key={tool} className="workspace-library-tool" tabIndex={0} aria-label={toolNames[tool] ?? tool}>
            {toolLogos[tool] ? <img src={toolLogos[tool]} alt="" className={`workspace-tool-logo workspace-tool-logo--${tool}`} /> : <Code2 size={20} aria-hidden="true" />}
            <span className="workspace-library-tool-tooltip" role="tooltip">{toolNames[tool] ?? tool}</span>
          </span>)}</div></div>}
          <div className="workspace-library-card-actions"><button disabled={space.recovery_available || changing === space.id || space.container_state === 'stopping' || ['checking', 'recovering', 'unavailable'].includes(space.container_state || '')} onClick={() => void changeState(space, ['running', 'preparing'].includes(space.container_state || '') ? 'stop' : 'start')}>{changing === space.id ? <Loader2 size={13} className="animate-spin" /> : ['running', 'preparing'].includes(space.container_state || '') ? <Square size={13} /> : <Play size={13} />}{space.container_state === 'preparing' ? 'Cancel setup' : space.container_state === 'stopping' ? 'Stopping…' : space.container_state === 'running' ? 'Stop' : space.container_state === 'failed' ? 'Retry' : 'Start'}</button><button disabled={space.container_state === 'recovering'} onClick={() => edit(space)}><Pencil size={13} />Edit</button><button disabled={changing === space.id || ['checking', 'preparing', 'stopping', 'recovering', 'unavailable'].includes(space.container_state || '')} title="Erase and rebuild the private Linux disk. Your mounted project folder is kept." onClick={() => setReinstalling(space)}><RefreshCw size={13} />Reinstall</button>{space.recovery_available && <button title="Back up and repair this workspace" disabled={changing === space.id || space.container_state === 'recovering'} onClick={() => setRecovering(space)}><Wrench size={13} />Recover</button>}<button className="workspace-library-remove" disabled={space.container_state === 'recovering'} onClick={() => setRemoving(space)}><Trash2 size={13} />Remove</button></div>
        </article>)}
      </div>}
    </div>
    {editor && <WorkspaceEditor workspace={editor} machines={machines} saving={saving} onClose={() => setEditor(null)} onSave={save} />}
    {recovering && <WorkspaceRecoveryDialog key={recovering.id} workspace={recovering} onClose={() => setRecovering(null)} onStarted={reload} onReinstall={() => { setReinstalling(recovering); setRecovering(null); }} onRemove={() => { setRemoving(recovering); setRecovering(null); }} />}
    {removing && <WorkspaceRemovalDialog key={removing.id} workspace={removing} onClose={() => setRemoving(null)} onRemoved={reload} />}
    {reinstalling && <WorkspaceReinstallDialog key={reinstalling.id} workspace={reinstalling} settings={reinstallDraft} onClose={() => { setReinstalling(null); setReinstallDraft(null); }} onStarted={reload} />}
  </>;
  return page ? <AppShell title="Workspaces">{content}</AppShell> : content;
}
