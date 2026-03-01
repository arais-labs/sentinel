import { FormEvent, useEffect, useMemo, useState, memo, useCallback } from 'react';
import {
  Plus,
  Search,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  Trash2,
  Pin,
  PinOff,
  Brain,
  Filter,
  Info,
  Save,
  X,
  Loader2,
  Pencil,
} from 'lucide-react';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { Markdown } from '../components/ui/Markdown';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { formatCompactDate, truncate } from '../lib/format';
import type { MemoryEntry, MemoryListResponse, MemoryStats } from '../types/api';

const categories = ['core', 'preference', 'project', 'correction'];

const SYSTEM_MEMORY_TITLES = ['Agent Identity', 'User Profile'];

function isSystemMemory(entry: MemoryEntry): boolean {
  if (!entry.title) return false;
  return SYSTEM_MEMORY_TITLES.some(t => entry.title!.trim() === t);
}

interface TreeRowProps {
  entry: MemoryEntry;
  depth: number;
  isExpanded: boolean;
  isSelected: boolean;
  isLoading: boolean;
  hasChildren: boolean;
  onToggle: (entry: MemoryEntry) => void;
  onSelect: (id: string) => void;
}

const TreeRow = memo(({ entry, depth, isExpanded, isSelected, isLoading, hasChildren, onToggle, onSelect }: TreeRowProps) => {
  return (
    <div 
      className={`group flex items-center gap-1 py-1 px-2 rounded-md transition-colors cursor-pointer ${
        isSelected ? 'bg-[color:var(--surface-accent)] text-[color:var(--text-primary)]' : 'hover:bg-[color:var(--surface-1)] text-[color:var(--text-secondary)]'
      }`}
      style={{ marginLeft: `${depth * 12}px` }}
      onClick={() => {
        onSelect(entry.id);
        if (hasChildren) onToggle(entry);
      }}
    >
      <div className="w-6 flex items-center justify-center">
        {isLoading ? (
          <RefreshCw size={12} className="animate-spin text-[color:var(--text-muted)]" />
        ) : hasChildren ? (
          <div className="p-1 rounded hover:bg-[color:var(--surface-2)] text-[color:var(--text-muted)]">
            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </div>
        ) : null}
      </div>
      <div className="flex-1 flex flex-col items-start text-left min-w-0 py-1">
        <div className="flex items-center gap-2 w-full">
          <span className="text-xs font-semibold truncate flex-1">
            {truncate(entry.title || entry.content, 60)}
          </span>
          {entry.pinned && <Pin size={10} className="text-amber-500 shrink-0" />}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[9px] font-bold uppercase tracking-wider opacity-60">{entry.category}</span>
        </div>
      </div>
    </div>
  );
});

TreeRow.displayName = 'TreeRow';

export function MemoryPage() {
  const [roots, setRoots] = useState<MemoryEntry[]>([]);
  const [childrenByParent, setChildrenByParent] = useState<Record<string, MemoryEntry[]>>({});
  const [nodesById, setNodesById] = useState<Record<string, MemoryEntry>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadingNodeIds, setLoadingNodeIds] = useState<Set<string>>(new Set());

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('all');
  const [searchResults, setSearchResults] = useState<MemoryEntry[]>([]);
  const [loadingRoots, setLoadingRoots] = useState(true);
  const [stats, setStats] = useState<MemoryStats>({ total_memories: 0, categories: {} });

  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [editorMode, setEditorMode] = useState<'create' | 'edit'>('create');
  const [editingNode, setEditingNode] = useState<MemoryEntry | null>(null);
  const [editorParent, setEditorParent] = useState<MemoryEntry | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [editSummary, setEditSummary] = useState('');
  const [editCategory, setEditCategory] = useState('core');
  const [editImportance, setEditImportance] = useState(50);
  const [editPinned, setEditPinned] = useState(false);
  const [togglingPin, setTogglingPin] = useState(false);

  const selectedNode = selectedId ? nodesById[selectedId] ?? null : null;

  const treeRows = useMemo(() => {
    const rows: { entry: MemoryEntry; depth: number }[] = [];
    const walk = (node: MemoryEntry, depth: number) => {
      rows.push({ entry: node, depth });
      if (expanded.has(node.id)) {
        const children = childrenByParent[node.id] ?? [];
        children.forEach((child) => walk(child, depth + 1));
      }
    };
    roots.forEach((root) => walk(root, 0));
    return rows;
  }, [roots, expanded, childrenByParent]);

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    if (!query.trim()) {
      setSearchResults([]);
      return;
    }
    const timer = window.setTimeout(() => { void runSearch(); }, 300);
    return () => window.clearTimeout(timer);
  }, [query, category]);

  async function refreshAll() {
    await Promise.all([loadStats(), loadRoots()]);
  }

  async function loadStats() {
    try {
      const payload = await api.get<MemoryStats>('/memory/stats');
      setStats(payload);
    } catch { /* ignore */ }
  }

  async function loadRoots(categoryOverride?: string) {
    setLoadingRoots(true);
    try {
      const nextCategory = categoryOverride ?? category;
      const queryPart = nextCategory === 'all' ? '' : `?category=${encodeURIComponent(nextCategory)}`;
      const payload = await api.get<MemoryListResponse>(`/memory/roots${queryPart}`);
      setRoots(payload.items);
      setNodesById((current) => {
        const next = { ...current };
        payload.items.forEach((item) => { next[item.id] = item; });
        return next;
      });
    } finally {
      setLoadingRoots(false);
    }
  }

  async function runSearch() {
    try {
      const payload = await api.post<MemoryListResponse>('/memory/search', {
        query: query.trim(),
        category: category !== 'all' ? category : undefined,
        limit: 100,
      });
      setSearchResults(payload.items);
      setNodesById((current) => {
        const next = { ...current };
        payload.items.forEach((item) => { next[item.id] = item; });
        return next;
      });
    } catch { /* ignore */ }
  }

  async function ensureChildren(nodeId: string) {
    if (childrenByParent[nodeId] || loadingNodeIds.has(nodeId)) return;
    setLoadingNodeIds((current) => new Set(current).add(nodeId));
    try {
      const payload = await api.get<MemoryListResponse>(`/memory/nodes/${nodeId}/children?limit=100`);
      setChildrenByParent((current) => ({ ...current, [nodeId]: payload.items }));
      setNodesById((current) => {
        const next = { ...current };
        payload.items.forEach((item) => { next[item.id] = item; });
        return next;
      });
    } finally {
      setLoadingNodeIds((current) => {
        const next = new Set(current);
        next.delete(nodeId);
        return next;
      });
    }
  }

  const toggleNode = useCallback(async (node: MemoryEntry) => {
    const isExpanding = !expanded.has(node.id);
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(node.id)) next.delete(node.id);
      else next.add(node.id);
      return next;
    });
    if (isExpanding) await ensureChildren(node.id);
  }, [expanded, childrenByParent, loadingNodeIds]);

  function openEditor(parent: MemoryEntry | null) {
    setEditorMode('create');
    setEditingNode(null);
    setEditorParent(parent);
    setEditTitle('');
    setEditContent('');
    setEditSummary('');
    setEditCategory(parent?.category ?? (category === 'all' ? 'core' : category));
    setEditImportance(50);
    setEditPinned(false);
    setIsEditorOpen(true);
  }

  function openEditorForEdit(node: MemoryEntry) {
    setEditorMode('edit');
    setEditingNode(node);
    setEditorParent(null);
    setEditTitle(node.title ?? '');
    setEditContent(node.content);
    setEditSummary(node.summary ?? '');
    setEditCategory(node.category);
    setEditImportance(node.importance ?? 50);
    setEditPinned(node.pinned ?? false);
    setIsEditorOpen(true);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!editContent.trim()) return;
    try {
      if (editorMode === 'edit' && editingNode) {
        const updated = await api.patch<MemoryEntry>(`/memory/nodes/${editingNode.id}`, {
          content: editContent.trim(),
          title: editTitle.trim() || null,
          summary: editSummary.trim() || null,
          category: editCategory,
          importance: editImportance,
          pinned: editPinned,
        });
        toast.success('Memory updated');
        setIsEditorOpen(false);
        setNodesById((current) => ({ ...current, [updated.id]: updated }));
        // Update in roots if it's a root
        setRoots((current) => current.map((r) => r.id === updated.id ? updated : r));
        // Update in children if it's a child
        if (updated.parent_id) {
          setChildrenByParent((current) => {
            const next = { ...current };
            if (next[updated.parent_id!]) {
              next[updated.parent_id!] = next[updated.parent_id!].map((c) => c.id === updated.id ? updated : c);
            }
            return next;
          });
        }
      } else {
        await api.post<MemoryEntry>('/memory', {
          content: editContent.trim(),
          title: editTitle.trim() || undefined,
          summary: editSummary.trim() || undefined,
          category: editCategory,
          parent_id: editorParent?.id ?? undefined,
          importance: editImportance,
          pinned: editPinned,
        });
        toast.success('Memory preserved');
        setIsEditorOpen(false);
        await refreshAll();
        if (editorParent) {
          await ensureChildren(editorParent.id);
          setExpanded((current) => new Set(current).add(editorParent.id));
        }
      }
    } catch { toast.error(editorMode === 'edit' ? 'Failed to update memory' : 'Failed to preserve memory'); }
  }

  async function togglePin(node: MemoryEntry) {
    if (togglingPin) return;
    setTogglingPin(true);
    try {
      const updated = await api.patch<MemoryEntry>(`/memory/nodes/${node.id}`, { pinned: !node.pinned });
      toast.success(updated.pinned ? 'Memory pinned' : 'Memory unpinned');
      setNodesById((current) => ({ ...current, [updated.id]: updated }));
      setRoots((current) => current.map((r) => r.id === updated.id ? updated : r));
      if (updated.parent_id) {
        setChildrenByParent((current) => {
          const next = { ...current };
          if (next[updated.parent_id!]) {
            next[updated.parent_id!] = next[updated.parent_id!].map((c) => c.id === updated.id ? updated : c);
          }
          return next;
        });
      }
    } catch { toast.error('Failed to update pin'); }
    finally { setTogglingPin(false); }
  }

  async function deleteNode(node: MemoryEntry) {
    try {
      await api.delete(`/memory/${node.id}`);
      toast.success('Memory purged');
      if (selectedId === node.id) setSelectedId(null);
      // Optimistically remove from local state
      setRoots((current) => current.filter((r) => r.id !== node.id));
      if (node.parent_id) {
        setChildrenByParent((current) => {
          const next = { ...current };
          if (next[node.parent_id!]) {
            next[node.parent_id!] = next[node.parent_id!].filter((c) => c.id !== node.id);
          }
          return next;
        });
      }
      setNodesById((current) => {
        const next = { ...current };
        delete next[node.id];
        return next;
      });
      void loadStats();
    } catch { toast.error('Purge failed'); }
  }

  return (
    <AppShell
      title="Memory"
      subtitle="Hierarchical DURABLE Knowledge Base"
      contentClassName="h-full !p-0 overflow-hidden"
      actions={
        <div className="flex items-center gap-2">
          <button onClick={() => void refreshAll()} className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
            <RefreshCw size={18} className={loadingRoots ? 'animate-spin' : ''} />
          </button>
          <div className="h-6 w-px bg-[color:var(--border-subtle)] mx-1" />
          <button onClick={() => openEditor(null)} className="btn-primary h-9 px-3 text-xs gap-2">
            <Plus size={14} />
            Add Memory
          </button>
        </div>
      }
    >
      <div className="flex h-full w-full overflow-hidden bg-[color:var(--surface-0)]">
        {/* Left Explorer */}
        <aside className="w-80 flex flex-col border-r border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
          <div className="p-4 border-b border-[color:var(--border-subtle)] space-y-3">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
              <input
                className="input-field pl-9 h-9 text-xs"
                placeholder="Search knowledge..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <select 
                className="bg-[color:var(--surface-2)] text-[10px] font-bold uppercase tracking-wider px-2 py-1.5 rounded outline-none border border-transparent focus:border-[color:var(--border-strong)] transition-all flex-1"
                value={category}
                onChange={(e) => { setCategory(e.target.value); void loadRoots(e.target.value); }}
              >
                <option value="all">ALL CATEGORIES</option>
                {categories.map(c => <option key={c} value={c}>{c.toUpperCase()}</option>)}
              </select>
            </div>
            <div className="flex flex-wrap gap-1.5 pt-1">
               <StatusChip label={`TOTAL: ${stats.total_memories}`} tone="info" className="scale-90 origin-left" />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
            {query.trim() ? (
              searchResults.length === 0 ? (
                <div className="h-20 flex flex-col items-center justify-center opacity-40 text-center px-4">
                  <Search size={20} className="mb-2" />
                  <p className="text-[10px] font-bold uppercase tracking-widest">No matching nodes</p>
                </div>
              ) : (
                searchResults.map(entry => (
                  <TreeRow 
                    key={entry.id}
                    entry={entry}
                    depth={0}
                    isExpanded={false}
                    isSelected={selectedId === entry.id}
                    isLoading={false}
                    hasChildren={false}
                    onToggle={() => {}}
                    onSelect={setSelectedId}
                  />
                ))
              )
            ) : loadingRoots ? (
              <div className="flex justify-center p-8">
                <Loader2 size={20} className="animate-spin text-[color:var(--text-muted)]" />
              </div>
            ) : (
              treeRows.map(({ entry, depth }) => (
                <TreeRow 
                  key={entry.id}
                  entry={entry}
                  depth={depth}
                  isExpanded={expanded.has(entry.id)}
                  isSelected={selectedId === entry.id}
                  isLoading={loadingNodeIds.has(entry.id)}
                  hasChildren={true}
                  onToggle={toggleNode}
                  onSelect={setSelectedId}
                />
              ))
            )}
          </div>
        </aside>

        {/* Right Inspector */}
        <main className="flex-1 overflow-y-auto bg-[color:var(--surface-0)]">
          {!selectedNode ? (
            <div className="h-full flex flex-col items-center justify-center opacity-30 gap-4">
              <Brain size={64} strokeWidth={1} />
              <p className="text-xs font-bold uppercase tracking-[0.2em]">Select a memory node to inspect</p>
            </div>
          ) : (
            <div className="max-w-4xl mx-auto p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-2 duration-300">
              <div className="flex items-start justify-between gap-6 mb-8">
                <div className="space-y-2">
                  <div className="flex items-center gap-3">
                    <StatusChip label={selectedNode.category} tone="info" />
                    {selectedNode.pinned && <StatusChip label="pinned" tone="warn" />}
                    <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">
                      Importance: {selectedNode.importance}
                    </span>
                  </div>
                  <h1 className="text-2xl font-bold tracking-tight leading-tight">
                    {selectedNode.title || 'Untitled Knowledge Node'}
                  </h1>
                  <p className="text-[11px] font-medium text-[color:var(--text-muted)] uppercase tracking-widest flex items-center gap-2">
                    <Info size={12} />
                    Established {formatCompactDate(selectedNode.created_at)}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button onClick={() => openEditor(selectedNode)} className="btn-secondary h-9 px-3 text-xs gap-2">
                    <Plus size={14} />
                    Add Child
                  </button>
                  <button onClick={() => openEditorForEdit(selectedNode)} className="btn-secondary h-9 px-3 text-xs gap-2">
                    <Pencil size={14} />
                    Edit
                  </button>
                  {isSystemMemory(selectedNode) ? (
                    <div className="relative group/sys">
                      <button disabled className="btn-secondary h-9 px-3 text-xs gap-2 opacity-40 cursor-not-allowed">
                        <Pin size={14} />
                        {selectedNode.pinned ? 'Pinned' : 'Pin'}
                      </button>
                      <div className="absolute bottom-full mb-2 right-0 bg-[color:var(--surface-2)] text-[color:var(--text-muted)] text-[10px] px-2 py-1 rounded whitespace-nowrap pointer-events-none opacity-0 group-hover/sys:opacity-100 transition-opacity z-10 border border-[color:var(--border-subtle)]">
                        System memory — cannot unpin
                      </div>
                    </div>
                  ) : (
                    <button
                      onClick={() => void togglePin(selectedNode)}
                      disabled={togglingPin}
                      className={`btn-secondary h-9 px-3 text-xs gap-2 ${selectedNode.pinned ? 'text-amber-500 hover:text-amber-600' : ''}`}
                    >
                      {togglingPin ? (
                        <Loader2 size={14} className="animate-spin" />
                      ) : selectedNode.pinned ? (
                        <PinOff size={14} />
                      ) : (
                        <Pin size={14} />
                      )}
                      {selectedNode.pinned ? 'Unpin' : 'Pin'}
                    </button>
                  )}
                  {isSystemMemory(selectedNode) ? (
                    <div className="relative group/sysdel">
                      <button disabled className="btn-secondary h-9 px-3 text-xs gap-2 text-rose-300 cursor-not-allowed opacity-40">
                        <Trash2 size={14} />
                        Purge
                      </button>
                      <div className="absolute bottom-full mb-2 right-0 bg-[color:var(--surface-2)] text-[color:var(--text-muted)] text-[10px] px-2 py-1 rounded whitespace-nowrap pointer-events-none opacity-0 group-hover/sysdel:opacity-100 transition-opacity z-10 border border-[color:var(--border-subtle)]">
                        System memory — cannot purge
                      </div>
                    </div>
                  ) : (
                    <button onClick={() => deleteNode(selectedNode)} className="btn-secondary h-9 px-3 text-xs gap-2 text-rose-500 hover:text-rose-600">
                      <Trash2 size={14} />
                      Purge
                    </button>
                  )}
                </div>
              </div>

              <div className="space-y-8">
                {selectedNode.summary && (
                  <section className="space-y-3">
                    <h3 className="text-[10px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">Abstract</h3>
                    <div className="p-5 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)]">
                      <Markdown content={selectedNode.summary} className="italic" muted />
                    </div>
                  </section>
                )}

                <section className="space-y-3">
                  <h3 className="text-[10px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">Raw Content</h3>
                  <div className="p-6 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)]">
                    <Markdown content={selectedNode.content} className="font-medium" />
                  </div>
                </section>

                <section className="space-y-3">
                  <h3 className="text-[10px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">Sub-Nodes</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {(childrenByParent[selectedNode.id] ?? []).map(child => (
                      <button 
                        key={child.id}
                        onClick={() => setSelectedId(child.id)}
                        className="p-4 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] hover:bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)] transition-all text-left group"
                      >
                        <span className="text-[13px] font-semibold block truncate group-hover:text-[color:var(--accent-solid)]">{child.title || truncate(child.content, 40)}</span>
                        <span className="text-[10px] text-[color:var(--text-muted)] mt-1 block uppercase font-bold tracking-widest">{child.category}</span>
                      </button>
                    ))}
                    {(childrenByParent[selectedNode.id] ?? []).length === 0 && (
                      <div className="col-span-full py-12 flex flex-col items-center justify-center opacity-30 border-2 border-dashed border-[color:var(--border-subtle)] rounded-2xl">
                        <Filter size={24} className="mb-2" />
                        <span className="text-[10px] font-bold uppercase tracking-widest">No dependent nodes</span>
                      </div>
                    )}
                  </div>
                </section>
              </div>
            </div>
          )}
        </main>
      </div>

      {/* Editor Modal */}
      {isEditorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setIsEditorOpen(false)} />
          <Panel className="relative w-full max-w-2xl bg-[color:var(--surface-0)] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <form onSubmit={handleSubmit}>
              <div className="px-6 py-4 border-b border-[color:var(--border-subtle)] flex items-center justify-between bg-[color:var(--surface-1)]">
                <div className="flex items-center gap-3">
                  <Brain size={18} className="text-[color:var(--accent-solid)]" />
                  <h2 className="font-bold text-sm uppercase tracking-widest">
                    {editorMode === 'edit' ? 'Edit Memory Node' : editorParent ? 'Add Child Memory' : 'Preserve Root Knowledge'}
                  </h2>
                </div>
                <button type="button" onClick={() => setIsEditorOpen(false)} className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                  <X size={20} />
                </button>
              </div>

              <div className="p-6 space-y-5 overflow-y-auto max-h-[70vh]">
                {editorMode === 'create' && editorParent && (
                  <div className="p-3 rounded-lg bg-blue-500/5 border border-blue-500/10 text-xs flex items-center gap-2">
                    <Info size={14} className="text-blue-500" />
                    <span className="text-[color:var(--text-muted)]">Parent: </span>
                    <span className="font-bold truncate text-blue-500">{editorParent.title || truncate(editorParent.content, 50)}</span>
                  </div>
                )}
                {editorMode === 'edit' && editingNode && (
                  <div className="p-3 rounded-lg bg-amber-500/5 border border-amber-500/10 text-xs flex items-center gap-2">
                    <Pencil size={14} className="text-amber-500" />
                    <span className="text-[color:var(--text-muted)]">Editing: </span>
                    <span className="font-bold truncate text-amber-500">{editingNode.title || truncate(editingNode.content, 50)}</span>
                  </div>
                )}

                <div className="space-y-2">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Subject Header</label>
                  <input 
                    className="input-field h-11"
                    placeholder="Enter node title..."
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Abstract / Summary</label>
                  <textarea 
                    className="input-field min-h-[80px] py-3 resize-none"
                    placeholder="Brief overview..."
                    value={editSummary}
                    onChange={(e) => setEditSummary(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] text-[color:var(--accent-solid)]">Durable Content</label>
                  <textarea 
                    className="input-field min-h-[160px] py-3 resize-none font-medium"
                    placeholder="The core memory content..."
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    required
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Category</label>
                    <select 
                      className="input-field h-11 uppercase font-bold text-[10px] tracking-wider"
                      value={editCategory}
                      onChange={(e) => setEditCategory(e.target.value)}
                    >
                      {categories.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </div>
                  <div className="space-y-2">
                    <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Priority Level: {editImportance}%</label>
                    <input 
                      type="range"
                      className="w-full mt-4 accent-[color:var(--accent-solid)]"
                      value={editImportance}
                      onChange={(e) => setEditImportance(parseInt(e.target.value))}
                    />
                  </div>
                </div>

                <label className="flex items-center gap-3 p-4 rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] cursor-pointer hover:border-[color:var(--border-strong)] transition-all">
                  <input 
                    type="checkbox"
                    className="w-4 h-4 accent-[color:var(--accent-solid)]"
                    checked={editPinned}
                    onChange={(e) => setEditPinned(e.target.checked)}
                  />
                  <div className="flex flex-col">
                    <span className="text-xs font-bold uppercase tracking-widest">Pin to context</span>
                    <span className="text-[10px] text-[color:var(--text-muted)]">Pinned nodes are prioritized during LLM retrieval</span>
                  </div>
                </label>
              </div>

              <div className="p-6 bg-[color:var(--surface-1)] border-t border-[color:var(--border-subtle)] flex items-center justify-end gap-3">
                <button type="button" onClick={() => setIsEditorOpen(false)} className="btn-secondary h-11 px-6">Cancel</button>
                <button type="submit" className="btn-primary h-11 px-8 gap-2">
                  <Save size={18} />
                  {editorMode === 'edit' ? 'Save Changes' : 'Preserve Node'}
                </button>
              </div>
            </form>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
