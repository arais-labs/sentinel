import { useState, FormEvent } from 'react';
import { X, Plus, Target, Info, Loader2 } from 'lucide-react';

import { Panel } from './ui/Panel';

interface SpawnSubAgentModalProps {
  onClose: () => void;
  onSpawn: (name: string, scope: string) => Promise<void>;
  isSpawning: boolean;
}

export function SpawnSubAgentModal({ onClose, onSpawn, isSpawning }: SpawnSubAgentModalProps) {
  const [name, setName] = useState('');
  const [scope, setScope] = useState('');

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await onSpawn(name.trim(), scope.trim());
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-xs" onClick={onClose} />
      <Panel className="relative w-full max-w-xl bg-(--surface-0) shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
        <form onSubmit={handleSubmit}>
          <div className="px-6 py-4 border-b border-(--border-subtle) flex items-center justify-between bg-(--surface-1)">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-(--accent-solid) text-(--app-bg)">
                <Plus size={18} />
              </div>
              <div className="flex flex-col">
                <h2 className="font-bold text-sm uppercase tracking-widest">Spawn Sub-Agent</h2>
                <span className="text-[9px] text-(--text-muted) font-mono uppercase tracking-tighter">Independent work · shared conversation</span>
              </div>
            </div>
            <button type="button" onClick={onClose} className="text-(--text-muted) hover:text-(--text-primary)">
              <X size={20} />
            </button>
          </div>

          <div className="p-6 space-y-5">
            <div className="space-y-2">
              <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted) flex items-center gap-2">
                <Target size={12} /> Specific Objective
              </label>
              <input
                className="input-field h-11 font-bold"
                placeholder="e.g. Scrape technical specs from the provided URL"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                disabled={isSpawning}
                autoFocus
              />
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted) flex items-center gap-2">
                <Info size={12} /> Context & Constraints
              </label>
              <textarea
                className="input-field min-h-[120px] py-3 resize-none text-[13px] leading-relaxed"
                placeholder="Provide URLs, API documentation snippets, or specific formatting requirements..."
                value={scope}
                onChange={(e) => setScope(e.target.value)}
                disabled={isSpawning}
              />
            </div>


          </div>

          <div className="p-6 bg-(--surface-1) border-t border-(--border-subtle) flex items-center justify-end gap-3">
            <button type="button" onClick={onClose} className="btn-secondary h-11 px-6" disabled={isSpawning}>Cancel</button>
            <button type="submit" className="btn-primary h-11 px-8 gap-2" disabled={isSpawning || !name.trim()}>
              {isSpawning ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
              SPAWN SUB-AGENT
            </button>
          </div>
        </form>
      </Panel>
    </div>
  );
}
