import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowRight, Check, ChevronDown, Code2, FolderOpen, GitBranch, Loader2, Plus, Search, Server, X } from 'lucide-react';
import { api } from '../../lib/api';
import type { Machine, Workspace } from '../../types/api';
import { MachineFolderPicker } from './MachineFolderPicker';
import { toolLogos } from './workspaceTools';
import alpineLogo from '../../assets/distro-logos/alpine.svg';
import ubuntuLogo from '../../assets/distro-logos/ubuntu.svg';
import debianLogo from '../../assets/distro-logos/debian.svg';
import './workspace-editor.css';
import { projectDirectoryError } from './workspaceValidation';

const distributionLogos = { alpine: alpineLogo, ubuntu: ubuntuLogo, debian: debianLogo };

type Tool = { id: string; name: string; detail: string; category: string };
type Stack = { id: string; name: string; description: string; tools: string[] };
type Distribution = { id: "alpine" | "ubuntu" | "debian"; name: string; detail: string; tools: string[] | null };
type Catalog = { distributions?: Distribution[]; os: string; container_available: boolean; stacks: Stack[]; tools: Tool[] };
export type WorkspaceDraft = { distribution: "alpine" | "ubuntu" | "debian"; name: string; machine_id: string; directory: string; development_tools: string[]; resources?: { cpus: number; memory_gib: number; disk_gib: number } };

export function WorkspaceEditor({ workspace, machines, saving, onClose, onSave }: {
  workspace: Workspace | 'new'; machines: Machine[]; saving: boolean;
  onClose: () => void; onSave: (draft: WorkspaceDraft) => Promise<void>;
}) {
  const creating = workspace === 'new';
  const [name, setName] = useState(creating ? '' : workspace.name);
  const [machineId, setMachineId] = useState(creating ? machines[0]?.id ?? '' : workspace.machine_id);
  const [directory, setDirectory] = useState(creating ? '' : workspace.directory);
  const [tab, setTab] = useState<'location' | 'os' | 'resources' | 'stacks' | 'tools'>(creating ? 'location' : 'resources');
  const [furthestStep, setFurthestStep] = useState(0);
  const tabId = useId();
  const body = useRef<HTMLElement>(null);
  const [query, setQuery] = useState('');
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [tools, setTools] = useState<string[]>(creating ? ['git'] : [...new Set(['git', ...(workspace.development_tools || [])])]);
  const [picking, setPicking] = useState(false);
  const [resources, setResources] = useState(creating ? { cpus: 2, memory_gib: 2, disk_gib: 32 } : workspace.resources ?? { cpus: 2, memory_gib: 2, disk_gib: 32 });
  const [distribution, setDistribution] = useState<'alpine' | 'ubuntu' | 'debian'>(creating ? 'alpine' : workspace.distribution ?? 'alpine');
  const clusters = Number(tools.includes('kind')) + Number(tools.includes('k3s'));
  const recommended = clusters > 1 ? { cpus: 4, memory_gib: 8, disk_gib: 64 }
    : clusters ? { cpus: 4, memory_gib: 6, disk_gib: 48 }
    : tools.includes('docker-builder') ? { cpus: 4, memory_gib: 4, disk_gib: 32 }
    : { cpus: 2, memory_gib: 2, disk_gib: 32 };
  const minimumDisk = creating ? 8 : workspace.resources?.disk_gib ?? 8;
  const sizeChanged = !creating && !!workspace.resources && (Object.keys(resources) as Array<keyof typeof resources>).some(key => resources[key] !== workspace.resources![key]);
  const toolsAdded = !creating && tools.some(tool => !(workspace.development_tools ?? []).includes(tool));
  const directoryChanged = !creating && directory.trim() !== workspace.directory;
  const restartNeeded = (sizeChanged || directoryChanged) && !creating && workspace.container_state === 'running';
  const validResources = Number.isInteger(resources.cpus) && resources.cpus >= 1 && resources.cpus <= 32
    && Number.isInteger(resources.memory_gib) && resources.memory_gib >= 1 && resources.memory_gib <= 64
    && Number.isInteger(resources.disk_gib) && resources.disk_gib >= minimumDisk && resources.disk_gib <= 1024;
  const dialog = useRef<HTMLDialogElement>(null);
  const machine = machines.find(item => item.id === machineId);
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  useEffect(() => {
    if (!machineId) return;
    let active = true;
    setDetecting(true); setCatalog(null); setError(''); if (creating) setTools(['git']);
    api.get<Catalog>(`/machines/${machineId}/development-tools`).then(value => {
      if (active) { setCatalog(value); if (!value.container_available) setError('Install Sentinel Runtime for this machine in Machines, or start your local desktop services.'); }
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Could not check this machine'); })
      .finally(() => { if (active) setDetecting(false); });
    return () => { active = false; };
  }, [machineId, creating, retry]);
  const mac = catalog?.os === 'darwin';
  const distributions = catalog?.distributions ?? [{ id: 'alpine' as const, name: 'Alpine Linux', detail: 'Default · all tools and accelerated desktop', tools: null }];
  const distro = distributions.find(item => item.id === distribution);
  const supported = (id: string) => !distro?.tools || distro.tools.includes(id);
  const compatibleCatalog = catalog ? { ...catalog, tools: catalog.tools.filter(tool => supported(tool.id)), stacks: catalog.stacks.filter(stack => stack.tools.every(supported)) } : null;
  const installed = creating ? ['git'] : [...new Set(['git', ...(workspace.development_tools || [])])];
  const selected = catalog?.tools.filter(tool => tools.includes(tool.id)) ?? [];
  const directoryError = projectDirectoryError(directory);
  const canContinue = !directoryError && name.trim() && machineId && directory.trim() && !detecting && !error && catalog;
  function toggleStack(stack: Stack) {
    const chosen = stack.tools.every(id => tools.includes(id));
    setTools(current => {
      if (!chosen) return [...new Set([...current, ...stack.tools])];
      const retained = new Set(installed);
      for (const other of catalog?.stacks ?? []) {
        if (other.id !== stack.id && other.tools.every(id => current.includes(id)) && !other.tools.every(id => stack.tools.includes(id))) {
          other.tools.forEach(id => retained.add(id));
        }
      }
      return current.filter(id => retained.has(id) || !stack.tools.includes(id));
    });
  }
  async function submit() {
    if (!canContinue || saving || !validResources || (creating && !lastStep)) return;
    await onSave({ distribution, name: name.trim(), machine_id: machineId, directory: directory.trim(), development_tools: mac ? tools : [], ...(mac ? { resources } : {}) });
  }
  const tabs = [{ id: 'location' as const, label: 'Location' }, { id: 'os' as const, label: 'OS' },
    { id: 'stacks' as const, label: 'Stacks' }, { id: 'tools' as const, label: 'Tools' }, { id: 'resources' as const, label: 'Resources' }];
  const stepIndex = tabs.findIndex(item => item.id === tab);
  const lastStep = stepIndex === tabs.length - 1;
  function advance() {
    if (!canContinue || saving || (tab === 'resources' && !validResources)) return;
    if (stepIndex < tabs.length - 1) {
      setFurthestStep(value => Math.max(value, stepIndex + 1));
      setTab(tabs[stepIndex + 1].id);
      setQuery('');
      requestAnimationFrame(() => body.current?.focus());
    }
  }
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const matches = (text: string) => terms.every(term => text.toLowerCase().includes(term));
  const visibleTools = compatibleCatalog?.tools.filter(tool => matches(`${tool.name} ${tool.id} ${tool.detail} ${tool.category}`)) ?? [];
  const visibleStacks = compatibleCatalog?.stacks.filter(stack => matches(`${stack.name} ${stack.description} ${stack.tools.map(id => catalog?.tools.find(tool => tool.id === id)?.name ?? id).join(' ')}`)) ?? [];
  useEffect(() => { body.current?.scrollTo({ top: 0 }); }, [tab, query]);
  return createPortal(<>
    <dialog ref={dialog} className="workspace-editor" aria-labelledby="workspace-editor-title"
      onCancel={event => { event.preventDefault(); if (!saving) onClose(); }} onClick={event => { if (event.target === event.currentTarget && !saving) onClose(); }}>
      <form onSubmit={event => { event.preventDefault(); if (creating && !lastStep) advance(); else void submit(); }}>
        <header className="workspace-editor-header">
          <div className="workspace-editor-heading"><span className="workspace-editor-eyebrow">{creating ? 'NEW WORKSPACE' : 'WORKSPACE'}</span><h2 id="workspace-editor-title">{creating ? 'New workspace' : 'Workspace settings'}</h2></div>
          <button type="button" className="workspace-editor-close" aria-label="Close" disabled={saving} onClick={onClose}><X size={19} /></button>
        </header>
        <div className="workspace-editor-fields workspace-editor-name"><label>Name<input autoFocus={creating} required maxLength={120} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. My next big idea" disabled={saving} /></label></div>
        <div className="workspace-editor-tabs" role="tablist" aria-label="Workspace settings" onKeyDown={event => {
          const index = tabs.findIndex(item => item.id === tab);
          const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1;
          if (next < 0 || saving || (creating && next > furthestStep)) return;
          event.preventDefault(); setTab(tabs[next].id);
          document.getElementById(`${tabId}-${tabs[next].id}`)?.focus();
        }}>{tabs.map((item, index) => <button type="button" role="tab" id={`${tabId}-${item.id}`} key={item.id} aria-selected={tab === item.id} aria-controls={`${tabId}-panel`} tabIndex={tab === item.id ? 0 : -1} disabled={saving || (creating && index > furthestStep)} onClick={() => { setTab(item.id); setQuery(''); }}>{creating && <span className="workspace-step-number">{index + 1}</span>}{item.label}</button>)}</div>
        <section ref={body} className="workspace-editor-body" role="tabpanel" id={`${tabId}-panel`} aria-labelledby={`${tabId}-${tab}`} tabIndex={0}>
          {detecting && <p className="workspace-editor-note"><Loader2 size={13} className="animate-spin" />Checking machine…</p>}
          {error && <div role="alert" className="workspace-editor-error">{error}<button type="button" onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
          {tab === 'location' && <div className="workspace-editor-fields">
            {!creating && <p className="workspace-editor-note">Changing folders resets terminals and file views. If running, the workspace restarts. Installed tools and private disk data are kept; neither project folder is deleted. Active agents must finish first.</p>}
            {<><label>Machine<div className="workspace-editor-select"><Server size={16} /><select value={machineId} onChange={e => { setMachineId(e.target.value); setDirectory(''); setFurthestStep(0); }} disabled={saving || !creating}>{machines.map(host => <option key={host.id} value={host.id}>{host.name}</option>)}</select><ChevronDown size={15} /></div></label>
              <label>Project folder<div className="workspace-editor-folder"><input required aria-invalid={!!directoryError} aria-describedby={`${tabId}-directory-help`} value={directory} onChange={e => setDirectory(e.target.value)} placeholder="Choose a folder or enter a path" disabled={saving} /><button type="button" onClick={() => setPicking(true)} disabled={!machineId || saving}><FolderOpen size={16} />Browse</button></div><small id={`${tabId}-directory-help`} role={directoryError ? 'alert' : undefined}>{directoryError ?? 'Shared with your isolated Linux environment. Your original files stay in this folder.'}</small></label>
              </>}
          </div>}
          {tab === 'os' && <div className="workspace-editor-fields">
            <div className="workspace-editor-intro"><p>Operating system</p><span>Choose the Linux distribution for this workspace.</span></div>
            <fieldset className="workspace-distributions" disabled={saving || !creating}>
              <legend className="sr-only">Linux distribution</legend>
              <div className="workspace-stack-grid">{distributions.map(item => (
                <label key={item.id} className={`workspace-stack-card workspace-distro-${item.id}${distribution === item.id ? ' is-selected' : ''}`}>
                  <input className="sr-only" type="radio" name={`${tabId}-distribution`} value={item.id} checked={distribution === item.id} onChange={() => {
                    setDistribution(item.id);
                    setTools(current => item.tools ? current.filter(id => item.tools!.includes(id)) : current);
                  }} />
                  <span className="workspace-stack-top">
                    <span className="workspace-stack-symbol"><img src={distributionLogos[item.id]} alt="" className="workspace-tool-logo" /></span>
                    <span className="workspace-stack-check">{distribution === item.id && <Check size={13} />}</span>
                  </span>
                  <strong>{item.name}</strong>
                  <span className="workspace-stack-description">{item.detail}</span>
                </label>
              ))}</div>
            </fieldset>
            {!creating && <small>Set when this workspace was created.</small>}
          </div>}
          {tab === 'resources' && <>
            <div className="workspace-editor-intro"><p>Resources for your workspace</p><span>Adjust CPU, memory, and private disk capacity.</span></div>
            <fieldset className="workspace-resources" disabled={saving}>
              <legend>Workspace size</legend>
              <div className="workspace-resource-inputs">
                <label>CPUs<input type="number" min={1} max={32} step={1} required value={resources.cpus} onChange={e => setResources(current => ({ ...current, cpus: Number(e.target.value) }))} /></label>
                <label>Memory <small>GiB</small><input type="number" min={1} max={64} step={1} required value={resources.memory_gib} onChange={e => setResources(current => ({ ...current, memory_gib: Number(e.target.value) }))} /></label>
                <label>Disk <small>GiB</small><input type="number" min={minimumDisk} max={1024} step={1} required value={resources.disk_gib} onChange={e => setResources(current => ({ ...current, disk_gib: Number(e.target.value) }))} /></label>
              </div>
              <div className="workspace-resource-recommendation"><span>Suggested: {recommended.cpus} CPUs · {recommended.memory_gib} GiB memory · {Math.max(minimumDisk, recommended.disk_gib)} GiB disk</span><button type="button" onClick={() => setResources({ ...recommended, disk_gib: Math.max(minimumDisk, recommended.disk_gib) })}>Use suggested</button></div>
              {resources.memory_gib < recommended.memory_gib && clusters > 0 && <p className="workspace-resource-warning">Kubernetes and your apps share this memory. A larger allocation is recommended.</p>}
              <p>Private disk capacity, separate from your project folder. Space is used as data is written.</p>
              {!creating && <p>{restartNeeded ? 'Saving restarts this workspace. Running terminal commands will stop; files and installed tools are kept.' : sizeChanged ? (toolsAdded ? 'Saving installs the selected tools and starts this workspace with the new size.' : 'The new size applies the next time this workspace starts.') : 'CPU and memory can be changed. Disk capacity can only be increased.'}</p>}
            </fieldset>
          </>}
          {(tab === 'stacks' || tab === 'tools') && <>
            <label className="workspace-tool-search"><Search size={16} aria-hidden="true" /><input type="search" onKeyDown={event => { if (event.key === 'Enter') event.preventDefault(); }} aria-label={tab === 'stacks' ? 'Search stacks' : 'Search tools'} placeholder={tab === 'stacks' ? 'Search stacks or included tools…' : 'Search tools by name or purpose…'} value={query} onChange={event => setQuery(event.target.value)} />{query && <button type="button" aria-label="Clear search" onClick={() => setQuery('')}><X size={14} /></button>}</label>
            {tab === 'stacks' && <><div className="workspace-editor-intro"><p>Choose your stacks.</p><span>Combine as many as you need, or pick individual tools.</span></div>
            <div className="workspace-stack-grid">{visibleStacks.map(stack => {
              const checked = stack.tools.every(id => tools.includes(id));
              return <button type="button" key={stack.id} aria-pressed={checked} onClick={() => toggleStack(stack)} className={`workspace-stack-card workspace-stack-${stack.id}${checked ? ' is-selected' : ''}`} disabled={saving || (!creating && stack.tools.every(id => installed.includes(id)))}>
                <span className="workspace-stack-top"><span className="workspace-stack-symbol"><img src={toolLogos[stack.id]} alt="" className={`workspace-tool-logo workspace-tool-logo--${stack.id}`} /></span><span className="workspace-stack-check">{checked ? <Check size={13} /> : <Plus size={13} />}</span></span>
                <strong>{stack.name}</strong><span className="workspace-stack-description">{stack.description}</span><span className="workspace-stack-includes">{stack.tools.map(id => catalog?.tools.find(tool => tool.id === id)?.name).join(' + ')}</span>
              </button>;
            })}</div>
            {catalog && !error && !detecting && !visibleStacks.length && <p className="workspace-editor-empty" role="status">No stacks match “{query}”. Try a tool name or a different keyword.</p>}</>}
            {tab === 'tools' && <div className="workspace-tool-groups">{[...new Set(visibleTools.map(tool => tool.category))].map(category => <fieldset key={category}>
              <legend>{category}</legend><div className="workspace-tool-list">{visibleTools.filter(tool => tool.category === category).map(tool => <label key={tool.id}>
                <input type="checkbox" checked={tools.includes(tool.id)} disabled={saving || installed.includes(tool.id)} onChange={() => setTools(current => current.includes(tool.id) ? current.filter(id => id !== tool.id) : [...current, tool.id])} />
                {toolLogos[tool.id] ? <img src={toolLogos[tool.id]} alt="" className={`workspace-tool-logo workspace-tool-logo--${tool.id}`} /> : <Code2 size={22} aria-hidden="true" />}
                <span><strong>{tool.name}</strong><small>{tool.detail}</small></span>
              </label>)}</div>
            </fieldset>)}{catalog && !error && !detecting && !visibleTools.length && <p className="workspace-editor-empty" role="status">No tools match “{query}”. Try a name, category, or purpose.</p>}</div>}

            <div className="workspace-tool-summary"><div><GitBranch size={15} /><strong>{selected.length ? `${selected.length} ${selected.length === 1 ? 'tool' : 'tools'} selected` : 'No additional tools'}</strong><button type="button" disabled={saving} onClick={() => setTools(installed)}>Reset</button></div><p>{selected.length ? selected.map(tool => tool.name).join(' · ') : 'Bash, tmux, Git, and Docker are included.'}</p><small>Installed tools are kept across sessions and restarts. You can add more at any time.</small></div>
          </>}
        </section>
        <footer className="workspace-editor-footer">
          <span className="workspace-editor-context">{restartNeeded ? 'Saving restarts the workspace and stops running commands.' : !validResources ? 'Check the allocation in Resources.' : creating ? `Step ${stepIndex + 1} of ${tabs.length} · ${tabs[stepIndex].label}` : <><Server size={13} />{machine?.name}</>}</span>
          <div className="workspace-editor-navigation">
            {creating && stepIndex > 0 && <button type="button" className="workspace-editor-back" disabled={saving} onClick={() => { setTab(tabs[stepIndex - 1].id); setQuery(''); }}>Back</button>}
            <button type="submit" className="btn-primary workspace-editor-submit" disabled={!canContinue || saving || ((!creating || lastStep) && !validResources)}>
              {saving && <Loader2 size={15} className="animate-spin" />}
              {creating ? lastStep ? 'Create workspace' : `Next: ${tabs[stepIndex + 1].label}` : restartNeeded ? 'Save and restart' : 'Save changes'}
              {!saving && <ArrowRight size={15} />}
            </button>
          </div>
        </footer>
      </form>
    </dialog>
    {picking && <MachineFolderPicker machineId={machineId} machineName={machine?.name ?? 'Machine'} initialPath={directory} onClose={() => setPicking(false)} onSelect={path => { setDirectory(path); setPicking(false); }} />}
  </>, document.body);
}
