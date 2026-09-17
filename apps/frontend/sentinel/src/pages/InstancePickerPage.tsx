import { DynamicIcon, iconNames, type IconName } from 'lucide-react/dynamic';
import { ArrowUpRight, Check, Loader2, SlidersHorizontal, Plus, Trash2, X } from 'lucide-react';
import { type CSSProperties, FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { notificationPublisher } from '../lib/notifications';

import { Panel } from '../components/ui/Panel';
import { SolidInstanceIcon } from '../components/ui/SolidInstanceIcon';
import { InstanceClouds } from '../components/ui/InstanceClouds';
import { api } from '../lib/api';
import { instanceColor, instanceColors } from '../lib/instance-appearance';

const notify = notificationPublisher('Instances');

interface SentinelInstance {
  name: string;
  database_name: string;
  display_name: string | null;
  appearance?: { color?: string | null; icon?: string };
}

const colorNames = ['Blue', 'Mint', 'Lavender', 'Amber', 'Rose', 'Slate'];
const availableIcons = new Set<string>(iconNames);
const iconLabel = (name: string) => name.replace(/-/g, ' ');

function InstanceIcon({ icon, label, size }: { icon?: string; label: string; size: number }) {
  // Preserve the original glyphs for saved selections.
  const name = icon === 'code' ? 'code-xml' : icon === 'flask' ? 'flask-conical' : icon;
  return name && availableIcons.has(name)
    ? <DynamicIcon name={name as IconName} size={size} strokeWidth={1.5} />
    : <>{label.slice(0, 1).toUpperCase()}</>;
}

export function InstancePickerPage() {
  const navigate = useNavigate();
  const [instances, setInstances] = useState<SentinelInstance[]>([]);
  const [name, setName] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Appearance changes stay in the draft until saved.
  const [editingName, setEditingName] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [savingAppearance, setSavingAppearance] = useState(false);
  const [draftColor, setDraftColor] = useState(instanceColors[0]);
  const [draftIcon, setDraftIcon] = useState('initial');
  const [iconSearch, setIconSearch] = useState('');
  const [iconLimit, setIconLimit] = useState(60);
  const matchingIcons = iconNames.filter(icon => iconLabel(icon).includes(iconSearch.trim().toLowerCase().replace(/-/g, ' ')));
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Delete-confirmation modal state.
  const [deleteTarget, setDeleteTarget] = useState<SentinelInstance | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState('');
  const [pendingDelete, setPendingDelete] = useState(false);

  useEffect(() => {
    let disposed = false;
    let pending = false;
    let initial = true;
    const refresh = async () => {
      if (pending || document.hidden) return;
      pending = true;
      const [instanceResult] = await Promise.allSettled([
        api.get<SentinelInstance[]>('/instances'),
      ]);
      pending = false;
      if (disposed) return;
      if (instanceResult.status === 'fulfilled') setInstances(instanceResult.value);
      else if (initial) notify.error('Failed to load instances. Retrying automatically.');
      setLoading(false);
      initial = false;
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5000);
    const resume = () => void refresh();
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    return () => {
      disposed = true;
      window.clearInterval(interval);
      window.removeEventListener('focus', resume);
      document.removeEventListener('visibilitychange', resume);
    };
  }, []);

  useEffect(() => {
    if (editingName && nameInputRef.current) {
      nameInputRef.current.focus();
      nameInputRef.current.select();
    }
  }, [editingName]);

  const openInstance = (instanceName: string) => {
    navigate(`/instances/${encodeURIComponent(instanceName)}/workspace`);
  };

  const createInstance = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    try {
      const instance = await api.post<SentinelInstance>('/instances', { name: trimmed });
      setName('');
      setInstances((current) =>
        [...current.filter((row) => row.name !== instance.name), instance].sort((a, b) =>
          a.name.localeCompare(b.name),
        ),
      );
      notify.success(`Created instance “${instance.name}”`);
      openInstance(instance.name);
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to create instance');
    } finally {
      setCreating(false);
    }
  };

  const startCustomize = (instance: SentinelInstance) => {
    setEditingName(instance.name);
    setIconSearch('');
    setIconLimit(60);
    setDisplayName(instance.display_name || instance.name);
    setDraftColor(instance.appearance?.color || instanceColor(instance.database_name));
    setDraftIcon(instance.appearance?.icon || 'initial');
  };

  const cancelCustomize = () => {
    setEditingName(null);
    setDisplayName('');
  };

  const saveAppearance = async () => {
    if (!editingName) return;
    const newName = displayName.trim();
    if (!newName) {
      cancelCustomize();
      return;
    }
    setSavingAppearance(true);
    try {
      const updated = await api.patch<SentinelInstance>(
        `/instances/${encodeURIComponent(editingName)}`,
        { display_name: newName, appearance: { color: draftColor, icon: draftIcon } },
      );
      setInstances((current) =>
        current
          .map((row) => (row.name === editingName ? updated : row))
          .sort((a, b) => a.name.localeCompare(b.name)),
      );
      notify.success('Instance updated');
      cancelCustomize();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to update instance');
    } finally {
      setSavingAppearance(false);
    }
  };

  const handleEditorKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      void saveAppearance();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      cancelCustomize();
    }
  };

  const askDelete = (instance: SentinelInstance) => {
    setDeleteTarget(instance);
    setDeleteConfirm('');
  };

  const cancelDelete = () => {
    setDeleteTarget(null);
    setDeleteConfirm('');
  };

  const confirmDelete = async () => {
    if (!deleteTarget || deleteConfirm !== deleteTarget.name) return;
    setPendingDelete(true);
    try {
      await api.delete(`/instances/${encodeURIComponent(deleteTarget.name)}`);
      const removed = deleteTarget.name;
      setInstances((current) => current.filter((row) => row.name !== removed));
      notify.success(`Deleted instance “${removed}”`);
      cancelDelete();
    } catch (error) {
      notify.error(error instanceof Error ? error.message : 'Failed to delete instance');
    } finally {
      setPendingDelete(false);
    }
  };

  const selected = instances.find(instance => instance.database_name === selectedId) ?? instances[0];
  const selectedName = selected?.display_name || selected?.name;
  const editing = editingName !== null;
  const dialogOpen = createOpen || editing || deleteTarget !== null;
  const accent = editing ? draftColor : selected?.appearance?.color || (selected ? instanceColor(selected.database_name) : instanceColors[0]);
  const selectedIcon = editing ? draftIcon : selected?.appearance?.icon;

  return (
    <section className="instance-gallery" data-dialog-open={dialogOpen} aria-label="Instances" style={{ '--space-accent': accent } as CSSProperties}>
      {!loading && selected && <InstanceClouds color={accent} paused={dialogOpen} />}
      {loading ? <div className="instance-loading" role="status"><Loader2 size={24} className="animate-spin" />Loading your instances…</div> : <>
        <div className="instance-stage" data-selected={Boolean(selected)} key={selected?.database_name ?? 'empty'}>
          <div className="instance-art" aria-hidden="true">
            <div className="instance-core">
              {selectedName ? <SolidInstanceIcon color={accent} paused={dialogOpen}>
                <InstanceIcon icon={selectedIcon} label={editing ? displayName || selectedName : selectedName} size={64} />
              </SolidInstanceIcon> : <Plus size={52} strokeWidth={1} />}
            </div>
          </div>
          <div className="instance-intro">
            {!selected && <p className="instance-eyebrow">Get started</p>}
            <h3>{selectedName || 'Your first instance'}</h3>
            <p className="instance-description">{selected ? 'Independent conversations, memories, settings, and accounts.' : 'Create an instance for your ideas, conversations, and agents.'}</p>
            <button className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest mt-6" onClick={() => selected ? openInstance(selected.name) : setCreateOpen(true)}>
              {selected ? 'Enter instance' : 'Create your first instance'}<ArrowUpRight size={19} />
            </button>
            {selected && <div className="instance-tools">
              <button onClick={() => startCustomize(selected)}><SlidersHorizontal size={13} />Customize</button>
              <button onClick={() => askDelete(selected)} className="instance-delete"><Trash2 size={13} />Delete</button>
            </div>}
          </div>
        </div>

        <div className="instance-roster">
          <div className="instance-roster-heading"><span>{instances.length} {instances.length === 1 ? 'instance' : 'instances'}</span></div>
          <div className="instance-choices" role="group" aria-label="Choose an instance">
            {instances.map(instance => {
              const active = selected?.database_name === instance.database_name;
              const label = instance.display_name || instance.name;
              return <button key={instance.database_name} className="instance-choice" aria-pressed={active}
                style={{ '--choice-accent': instance.appearance?.color || instanceColor(instance.database_name) } as CSSProperties}
                onClick={() => setSelectedId(instance.database_name)} onDoubleClick={() => openInstance(instance.name)}>
                <span className="instance-choice-avatar" aria-hidden="true"><InstanceIcon icon={instance.appearance?.icon} label={label} size={20} /></span>
                <span className="instance-choice-label">{label}</span>
                {active && <Check size={15} className="instance-choice-check" aria-label="Selected" />}
              </button>;
            })}
            {selected && <button className="instance-choice instance-choice-new" onClick={() => setCreateOpen(true)}><span className="instance-choice-avatar"><Plus size={22} strokeWidth={1.5} /></span><span>New instance</span></button>}
          </div>
        </div>
      </>}

      {(createOpen || editing) && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4" onClick={() => { if (!creating && !savingAppearance) { setCreateOpen(false); cancelCustomize(); } }}>
        <Panel role="dialog" aria-modal="true" aria-label={editing ? 'Customize instance' : 'New instance'} className="relative w-full max-w-md max-h-[90vh] overflow-y-auto bg-(--surface-0) p-7 shadow-2xl">
          <form onClick={event => event.stopPropagation()} onSubmit={editing ? event => { event.preventDefault(); void saveAppearance(); } : createInstance} className="space-y-5">
            <div className="flex items-start justify-between gap-4"><div><h3 className="text-sm font-bold uppercase tracking-widest">{editing ? 'Customize instance' : 'New instance'}</h3></div><button type="button" aria-label="Close dialog" disabled={creating || savingAppearance} onClick={() => { setCreateOpen(false); cancelCustomize(); }}><X size={18} /></button></div>
            <div className="space-y-2"><label htmlFor="instance-name" className="text-sm text-(--text-secondary)">Instance name</label>
              <input id="instance-name" ref={editing ? nameInputRef : undefined} autoFocus value={editing ? displayName : name} onChange={event => editing ? setDisplayName(event.target.value) : setName(event.target.value)} maxLength={editing ? 120 : 80} onKeyDown={editing ? handleEditorKey : undefined} disabled={creating || savingAppearance} placeholder="e.g. studio, research, side-project" className="h-11 w-full rounded-lg border border-(--border-subtle) bg-(--surface-0) px-3 text-sm outline-hidden focus:border-(--accent-solid)" />
            </div>
            {editing && <>
              <fieldset className="space-y-3">
                <legend className="text-[10px] font-bold uppercase tracking-widest text-(--text-secondary)">Icon</legend>
                <div className="flex items-center gap-3 text-sm capitalize">
                  <span className="instance-choice-avatar"><InstanceIcon icon={draftIcon} label={displayName || 'I'} size={22} /></span>
                  <span>{iconLabel(draftIcon)}</span>
                  <button type="button" className="ml-auto text-xs text-(--text-secondary)" onClick={() => setDraftIcon('initial')}>Use initial</button>
                </div>
                <input aria-label="Search icons" placeholder="Search icons…" value={iconSearch} onChange={event => { setIconSearch(event.target.value); setIconLimit(60); }} className="h-10 w-full rounded-lg border border-(--border-subtle) bg-(--surface-0) px-3 text-sm outline-hidden focus:border-(--accent-solid)" />
                <div className="instance-icon-browser">
                  <div className="instance-icon-options">
                    {matchingIcons.slice(0, iconLimit).map(icon => <button type="button" key={icon} title={iconLabel(icon)} aria-label={iconLabel(icon)} aria-pressed={draftIcon === icon} onClick={() => setDraftIcon(icon)}><InstanceIcon icon={icon} label={displayName || 'I'} size={20} /></button>)}
                  </div>
                  {!matchingIcons.length && <p className="py-6 text-center text-sm text-(--text-secondary)">No icons found.</p>}
                  {matchingIcons.length > iconLimit && <button type="button" className="w-full py-3 text-xs text-(--text-secondary)" onClick={() => setIconLimit(limit => limit + 60)}>Show more icons</button>}
                </div>
              </fieldset>
              <fieldset className="space-y-3"><legend className="text-[10px] font-bold uppercase tracking-widest text-(--text-secondary)">Glow color</legend><div className="instance-color-options">
                {instanceColors.map((color, index) => <button type="button" key={color} aria-label={colorNames[index]} aria-pressed={draftColor.toLowerCase() === color} style={{ backgroundColor: color }} onClick={() => setDraftColor(color)}>{draftColor.toLowerCase() === color && <Check size={15} />}</button>)}
                <label className="instance-custom-color" title="Custom glow color"><Plus size={16} /><input aria-label="Custom glow color" type="color" value={draftColor} onChange={event => setDraftColor(event.target.value)} /></label>
              </div></fieldset>
            </>}
            <button className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest w-full disabled:opacity-40" disabled={creating || savingAppearance || !(editing ? displayName : name).trim()}>{creating || savingAppearance ? <Loader2 size={16} className="animate-spin" /> : null}{editing ? 'Save changes' : 'Create instance'}<ArrowUpRight size={17} /></button>
          </form>
        </Panel>
      </div>}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-xs"
            onClick={pendingDelete ? undefined : cancelDelete}
          />
          <Panel className="relative w-full max-w-md bg-(--surface-0) shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="flex items-center justify-between px-6 py-4 border-b border-(--border-subtle) bg-(--surface-1)">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-red-500/10 text-red-500">
                  <Trash2 size={18} />
                </div>
                <div className="flex flex-col">
                  <h2 className="font-bold text-sm uppercase tracking-widest">Delete instance</h2>
                  <span className="text-[9px] text-(--text-muted) font-mono uppercase tracking-tighter">
                    Permanent action
                  </span>
                </div>
              </div>
              <button
                type="button"
                onClick={cancelDelete}
                disabled={pendingDelete}
                className="text-(--text-muted) hover:text-(--text-primary)"
              >
                <X size={20} />
              </button>
            </div>

            <div className="p-6 space-y-4">
              <p className="text-sm text-(--text-secondary) leading-relaxed">
                This permanently deletes instance{' '}
                <span className="font-mono text-(--text-primary)">{deleteTarget.name}</span>.
                All sessions, memory, and logs for this instance are lost.
              </p>
              <div className="space-y-2">
                <label
                  htmlFor="delete-confirm"
                  className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)"
                >
                  Type{' '}
                  <span className="font-mono normal-case tracking-normal text-(--text-primary)">
                    {deleteTarget.name}
                  </span>{' '}
                  to confirm
                </label>
                <input
                  id="delete-confirm"
                  value={deleteConfirm}
                  onChange={(event) => setDeleteConfirm(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && deleteConfirm === deleteTarget.name && !pendingDelete) {
                      void confirmDelete();
                    } else if (event.key === 'Escape' && !pendingDelete) {
                      cancelDelete();
                    }
                  }}
                  disabled={pendingDelete}
                  autoFocus
                  className="h-10 w-full rounded-md border border-(--border-subtle) bg-(--surface-0) px-3 text-sm outline-hidden focus:border-red-500"
                  placeholder={deleteTarget.name}
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-(--border-subtle) bg-(--surface-1)">
              <button
                type="button"
                onClick={cancelDelete}
                disabled={pendingDelete}
                className="h-9 px-4 rounded-md text-sm text-(--text-secondary) hover:bg-(--surface-0) disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmDelete()}
                disabled={pendingDelete || deleteConfirm !== deleteTarget.name}
                className="inline-flex h-9 items-center gap-2 rounded-md bg-red-500 px-4 text-sm font-medium text-white hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {pendingDelete ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Trash2 size={14} />
                )}
                Delete
              </button>
            </div>
          </Panel>
        </div>
      )}
    </section>
  );
}
