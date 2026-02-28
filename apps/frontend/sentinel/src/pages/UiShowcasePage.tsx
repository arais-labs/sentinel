import type { ReactNode } from 'react';
import {
  Globe,
  Trash2,
  Play,
  Save,
  ChevronRight,
  Shield,
  Activity,
  Terminal,
  Cpu,
  User,
  Info,
  ExternalLink,
  Plus,
} from 'lucide-react';

import { AppShell } from '../components/AppShell';
import { JsonBlock } from '../components/ui/JsonBlock';
import { Logo } from '../components/ui/Logo';
import { Panel } from '../components/ui/Panel';
import { StatusChip } from '../components/ui/StatusChip';

function ShowcaseSection({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <Panel className="p-6 space-y-4">
      <div className="space-y-1 border-b border-[color:var(--border-subtle)] pb-4">
        <h2 className="text-[10px] font-bold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">
          {title}
        </h2>
        <p className="text-[11px] text-[color:var(--text-secondary)] font-medium uppercase tracking-tight opacity-60">
          {subtitle}
        </p>
      </div>
      <div className="pt-2">
        {children}
      </div>
    </Panel>
  );
}

export function UiShowcasePage() {
  return (
    <AppShell title="UI Design System" subtitle="Operator Console Component Registry">
      <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 gap-6 animate-in fade-in duration-500">
        
        {/* Brand Elements */}
        <ShowcaseSection title="Brand & Identity" subtitle="Primary visual identifiers and system icons.">
          <div className="flex items-center gap-6">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-xl shadow-black/10">
              <Logo size={32} />
            </div>
            <div className="space-y-1">
              <h3 className="text-xl font-bold tracking-tighter">SENTINEL OPERATOR</h3>
              <p className="text-[10px] font-mono uppercase tracking-widest text-[color:var(--text-muted)]">Revision 0.1.0-alpha</p>
            </div>
          </div>
        </ShowcaseSection>

        {/* Semantic Status */}
        <ShowcaseSection title="Semantic Status" subtitle="Standardized tonal feedback for system events.">
          <div className="flex flex-wrap gap-2">
            <StatusChip label="OPERATIONAL" tone="good" />
            <StatusChip label="ESTOP_READY" tone="warn" />
            <StatusChip label="CRITICAL_FAILURE" tone="danger" />
            <StatusChip label="TELEMETRY_SYNC" tone="info" />
            <StatusChip label="DEFAULT_NODE" />
          </div>
        </ShowcaseSection>

        {/* Action Protocols */}
        <ShowcaseSection title="Action Protocols" subtitle="Standardized button variants for operator interaction.">
          <div className="grid grid-cols-2 gap-3">
            <button className="btn-primary h-11 text-[10px] uppercase tracking-widest">
              <Save size={14} /> Commit Changes
            </button>
            <button className="btn-secondary h-11 text-[10px] uppercase tracking-widest">
              <Shield size={14} /> Authorize
            </button>
            <button className="btn-secondary h-11 text-[10px] uppercase tracking-widest text-rose-500 hover:bg-rose-500/10">
              <Trash2 size={14} /> Purge Registry
            </button>
            <button className="btn-primary h-11 text-[10px] uppercase tracking-widest bg-emerald-600 hover:bg-emerald-700">
              <Play size={14} fill="currentColor" /> Run Sequence
            </button>
          </div>
        </ShowcaseSection>

        {/* Form Controls */}
        <ShowcaseSection title="Operator Inputs" subtitle="High-density data entry surfaces.">
          <div className="space-y-3">
            <input className="input-field h-10 text-xs font-bold" placeholder="Registry Identifier..." />
            <div className="relative">
              <Terminal size={14} className="absolute left-3 top-3 text-[color:var(--text-muted)]" />
              <textarea 
                className="input-field min-h-[100px] pl-9 py-3 resize-none font-mono text-[12px]" 
                placeholder="Root prompt definition..."
              />
            </div>
          </div>
        </ShowcaseSection>

        {/* Data Surfaces */}
        <ShowcaseSection title="Durable Content" subtitle="Syntax-highlighted telemetry and knowledge blocks.">
          <JsonBlock value={JSON.stringify({
            status: "synchronized",
            latency: "42ms",
            protocol: "SENTINEL_WS_v1",
            nodes: ["auth", "playwright", "llm_router"]
          }, null, 2)} />
        </ShowcaseSection>

        {/* Browser Target */}
        <ShowcaseSection title="Browser Geometry" subtitle="Standard 16:9 projection for remote sessions.">
          <div className="relative aspect-video w-full rounded-xl bg-zinc-950 border border-[color:var(--border-subtle)] overflow-hidden">
            <div className="absolute inset-0 flex items-center justify-center opacity-20">
              <Globe size={48} strokeWidth={1} />
            </div>
            <div className="absolute top-3 right-3 flex gap-2">
              <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
              <span className="text-[10px] font-bold text-white uppercase tracking-widest opacity-60">LIVE_LINK_OK</span>
            </div>
          </div>
        </ShowcaseSection>

        {/* Runtime Execution */}
        <ShowcaseSection title="Runtime Execution" subtitle="Visual cards for active agent threads.">
          <Panel className="p-4 bg-[color:var(--surface-1)] border-dashed">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Cpu size={16} className="text-[color:var(--accent-solid)]" />
                <span className="text-xs font-bold">Workspace Maintenance</span>
              </div>
              <StatusChip label="running" tone="warn" className="scale-75" />
            </div>
            <p className="text-[11px] text-[color:var(--text-secondary)] leading-relaxed mb-4">
              Collecting orphan process identifiers and clearing temporary registry keys...
            </p>
            <div className="flex items-center gap-2">
              <StatusChip label="step 14/20" className="scale-75 origin-left opacity-60" />
              <div className="h-3 w-px bg-[color:var(--border-subtle)]" />
              <span className="text-[9px] font-mono text-[color:var(--text-muted)]">PID: 88291</span>
            </div>
          </Panel>
        </ShowcaseSection>

        {/* Message Tones */}
        <ShowcaseSection title="Communication Tones" subtitle="Differentiated palettes for identity roles.">
          <div className="space-y-3">
            <div className="flex flex-col items-end gap-1.5">
              <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] px-1">user</span>
              <div className="bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] px-4 py-2 rounded-2xl rounded-tr-none text-xs font-medium shadow-sm">
                Scale all active instances to zero.
              </div>
            </div>
            <div className="flex flex-col items-start gap-1.5">
              <span className="text-[9px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] px-1">assistant</span>
              <div className="bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] px-4 py-2 rounded-2xl rounded-tl-none text-xs font-medium shadow-sm leading-relaxed">
                Acknowledged. Initiating system-wide shutdown protocol for all ephemeral nodes.
              </div>
            </div>
          </div>
        </ShowcaseSection>

      </div>
    </AppShell>
  );
}
