import { useEffect, useMemo, useState } from 'react';
import {
  Wrench,
  Search,
  RefreshCw,
  Play,
  Shield,
  Info,
  Terminal,
  Filter,
  X,
  ChevronRight,
  Loader2,
  Settings,
  AlertTriangle,
} from 'lucide-react';
import { toast } from 'sonner';

import { AppShell } from '../components/AppShell';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';
import { api } from '../lib/api';
import { toPrettyJson } from '../lib/format';
import type { ToolDetail, ToolExecutionResponse, ToolListResponse, ToolSummary } from '../types/api';

const riskOptions = ['all', 'low', 'medium', 'high'];

function riskTone(risk: string): 'default' | 'good' | 'warn' | 'danger' | 'info' {
  if (risk === 'low') return 'good';
  if (risk === 'medium') return 'warn';
  if (risk === 'high') return 'danger';
  return 'default';
}

export function ToolsPage() {
  const [tools, setTools] = useState<ToolSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [risk, setRisk] = useState('all');
  const [selected, setSelected] = useState<ToolDetail | null>(null);
  const [inputText, setInputText] = useState('{}');
  const [outputText, setOutputText] = useState('');
  const [running, setRunning] = useState(false);

  const filtered = useMemo(
    () =>
      tools.filter((tool) => {
        if (risk !== 'all' && tool.risk_level !== risk) return false;
        if (!query.trim()) return true;
        const haystack = `${tool.name} ${tool.description}`.toLowerCase();
        return haystack.includes(query.trim().toLowerCase());
      }),
    [tools, query, risk],
  );

  useEffect(() => {
    void loadTools();
  }, []);

  async function loadTools() {
    setLoading(true);
    try {
      const payload = await api.get<ToolListResponse>('/tools');
      setTools(payload.items);
    } catch { toast.error('Failed to load tool registry'); }
    finally { setLoading(false); }
  }

  async function openDetail(name: string) {
    try {
      const payload = await api.get<ToolDetail>(`/tools/${name}`);
      setSelected(payload);
      setInputText('{}');
      setOutputText('');
    } catch { toast.error('Failed to load tool manifest'); }
  }

  async function executeSelected() {
    if (!selected || running) return;
    let input: Record<string, unknown>;
    try {
      input = JSON.parse(inputText) as Record<string, unknown>;
    } catch {
      toast.error('Payload must be valid JSON');
      return;
    }

    setRunning(true);
    try {
      const payload = await api.post<ToolExecutionResponse>(`/tools/${selected.name}/execute`, { input });
      setOutputText(toPrettyJson(payload));
      toast.success('Execution complete');
    } catch { toast.error('Runtime execution error'); }
    finally { setRunning(false); }
  }

  const lowCount = tools.filter((tool) => tool.risk_level === 'low').length;
  const mediumCount = tools.filter((tool) => tool.risk_level === 'medium').length;
  const highCount = tools.filter((tool) => tool.risk_level === 'high').length;

  return (
    <AppShell
      title="Tool Registry"
      subtitle="Operator Guardrails & Runtime Execution"
      actions={
        <div className="flex items-center gap-2">
          <button onClick={() => void loadTools()} className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
            <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      }
    >
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Filter Bar */}
        <Panel className="p-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4 flex-1">
            <div className="relative min-w-[240px]">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
              <input 
                className="input-field pl-9 h-10 text-xs"
                placeholder="Search capabilities..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            
            <div className="relative min-w-[180px]">
              <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
              <select 
                className="input-field pl-9 h-10 text-[10px] font-bold uppercase tracking-wider appearance-none"
                value={risk}
                onChange={(e) => setRisk(e.target.value)}
              >
                {riskOptions.map(r => <option key={r} value={r}>{r === 'all' ? 'All Risk Levels' : `${r} RISK`}</option>)}
              </select>
            </div>
          </div>
          
          <div className="flex items-center gap-3 bg-[color:var(--surface-1)] px-4 py-2 rounded-lg border border-[color:var(--border-subtle)]">
            <div className="flex items-center gap-1.5 border-r border-[color:var(--border-subtle)] pr-3 mr-1">
              <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">Total:</span>
              <span className="text-xs font-bold font-mono">{tools.length}</span>
            </div>
            <StatusChip label={`Low: ${lowCount}`} tone="good" className="scale-90" />
            <StatusChip label={`Med: ${mediumCount}`} tone="warn" className="scale-90" />
            <StatusChip label={`High: ${highCount}`} tone="danger" className="scale-90" />
          </div>
        </Panel>

        {loading ? (
          <div className="py-20 flex justify-center">
            <Loader2 size={32} className="animate-spin text-[color:var(--text-muted)]" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-20 flex flex-col items-center justify-center opacity-30 gap-4 border-2 border-dashed border-[color:var(--border-subtle)] rounded-2xl">
            <Wrench size={48} strokeWidth={1} />
            <p className="text-sm font-bold uppercase tracking-widest">No tools matching manifest criteria</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {filtered.map((tool) => (
              <Panel key={tool.name} className="p-5 flex flex-col justify-between group hover:border-[color:var(--border-strong)] transition-all bg-[color:var(--surface-0)]/80">
                <div>
                  <div className="flex items-start justify-between gap-4 mb-3">
                    <h3 className="text-sm font-bold text-[color:var(--text-primary)] group-hover:text-[color:var(--accent-solid)] transition-colors">
                      {tool.name}
                    </h3>
                    <StatusChip label={tool.risk_level} tone={riskTone(tool.risk_level)} className="scale-90 origin-right" />
                  </div>
                  <p className="text-[13px] text-[color:var(--text-secondary)] leading-relaxed line-clamp-3 mb-6">
                    {tool.description}
                  </p>
                </div>

                <div className="flex items-center justify-between pt-4 border-t border-[color:var(--border-subtle)]">
                  <StatusChip 
                    label={tool.enabled ? 'operational' : 'disabled'} 
                    tone={tool.enabled ? 'good' : 'default'} 
                    className="scale-75 origin-left"
                  />
                  <button 
                    onClick={() => void openDetail(tool.name)}
                    className="btn-secondary h-8 px-3 text-[10px] gap-2 uppercase tracking-widest"
                  >
                    <Settings size={12} />
                    Inspect
                  </button>
                </div>
              </Panel>
            ))}
          </div>
        )}
      </div>

      {/* Tool Lab Modal */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setSelected(null)} />
          <Panel className="relative w-full max-w-5xl bg-[color:var(--surface-0)] shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
            <div className="px-6 py-4 border-b border-[color:var(--border-subtle)] flex items-center justify-between bg-[color:var(--surface-1)]">
              <div className="flex items-center gap-3">
                <Terminal size={18} className="text-emerald-500" />
                <div className="flex flex-col">
                  <h2 className="font-bold text-sm uppercase tracking-widest">{selected.name}</h2>
                  <span className="text-[9px] text-[color:var(--text-muted)] font-mono uppercase tracking-tighter">Guardrail Laboratory</span>
                </div>
              </div>
              <div className="flex items-center gap-4">
                <StatusChip label={`${selected.risk_level} risk`} tone={riskTone(selected.risk_level)} />
                <button type="button" onClick={() => setSelected(null)} className="text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]">
                  <X size={20} />
                </button>
              </div>
            </div>

            <div className="p-0 flex flex-col lg:flex-row h-[70vh]">
              {/* Left: Metadata & Schema */}
              <div className="flex-1 overflow-y-auto p-6 border-r border-[color:var(--border-subtle)] space-y-6">
                <section className="space-y-2">
                  <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                    <Info size={12} /> Capability Definition
                  </h3>
                  <p className="text-[14px] text-[color:var(--text-secondary)] leading-relaxed">
                    {selected.description}
                  </p>
                </section>

                <section className="space-y-2">
                  <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                    <Terminal size={12} /> Parameters Schema
                  </h3>
                  <JsonBlock value={toPrettyJson(selected.parameters_schema)} className="max-h-[400px]" />
                </section>
              </div>

              {/* Right: Runtime Execution */}
              <div className="flex-1 overflow-y-auto p-6 bg-[color:var(--surface-1)]/30 space-y-6">
                <section className="space-y-2">
                  <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] flex items-center gap-2">
                    <Play size={12} /> Manual Invocation (JSON)
                  </h3>
                  <textarea 
                    className="input-field min-h-[200px] py-3 resize-none font-mono text-[13px] bg-[color:var(--surface-0)]"
                    placeholder="{}"
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                  />
                </section>

                {outputText && (
                  <section className="space-y-2 animate-in fade-in slide-in-from-top-2">
                    <h3 className="text-[10px] font-bold uppercase tracking-widest text-emerald-500 flex items-center gap-2">
                      <ChevronRight size={12} /> Execution Telemetry
                    </h3>
                    <JsonBlock value={outputText} className="max-h-[300px] border-emerald-500/20 bg-emerald-500/5" />
                  </section>
                )}
              </div>
            </div>

            <div className="p-6 bg-[color:var(--surface-1)] border-t border-[color:var(--border-subtle)] flex items-center justify-end gap-3">
              <button type="button" onClick={() => setSelected(null)} className="btn-secondary h-11 px-6">Cancel</button>
              <button 
                onClick={() => void executeSelected()} 
                disabled={running}
                className="btn-primary h-11 px-8 gap-2 bg-emerald-600 hover:bg-emerald-700"
              >
                {running ? <Loader2 size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
                Run Capability
              </button>
            </div>
          </Panel>
        </div>
      )}
    </AppShell>
  );
}
