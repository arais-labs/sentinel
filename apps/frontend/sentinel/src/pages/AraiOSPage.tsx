import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import {
  Loader2,
  CheckCircle,
  Lock,
  FileCode,
  GitBranch,
  MessageCircle,
  LayoutGrid,
  Search,
  Plus,
  ArrowLeft,
  Trash2,
  Pencil,
  Play,
  Copy,
  X,
  Send,
  ChevronDown,
  ChevronRight,
  FileText,
  AlertTriangle,
  icons as lucideIcons,
  type LucideIcon,
} from 'lucide-react';
import { toast } from 'sonner';

function resolveIcon(name: string | null | undefined): LucideIcon {
  if (!name) return LayoutGrid;
  // lucide icons object uses PascalCase keys (e.g. "Users", "FileText")
  // but module icons are stored lowercase (e.g. "users", "file-text")
  // Convert: "file-text" → "FileText", "users" → "Users"
  const pascal = name.replace(/(^|[-_])([a-z])/g, (_, __, c) => c.toUpperCase());
  return (lucideIcons as Record<string, LucideIcon>)[pascal] || LayoutGrid;
}

import { AppShell } from '../components/AppShell';

/* ═══════════════════════════════════════════════════════════════════════════
   API helper — bypasses Sentinel's /api/v1 prefix
   ═══════════════════════════════════════════════════════════════════════════ */

async function araiosApi<T = any>(path: string, opts?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(path, {
    method: opts?.method ?? 'GET',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: opts?.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `Request failed (${res.status})`);
  }
  return res.json();
}

/* ═══════════════════════════════════════════════════════════════════════════
   Utility functions
   ═══════════════════════════════════════════════════════════════════════════ */

function initials(name: string): string {
  if (!name) return '?';
  return name.trim().split(/\s+/).slice(0, 2).map(w => w[0].toUpperCase()).join('');
}

function avatarHue(name: string): number {
  let h = 0;
  for (const c of name || 'X') h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return h % 360;
}

function shortDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const abs = Math.abs(Date.now() - d.getTime());
  if (abs < 60000) return 'Now';
  if (abs < 3600000) return `${Math.round(abs / 60000)}m ago`;
  if (abs < 86400000) return `${Math.round(abs / 3600000)}h ago`;
  return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
}

function fmtDate(iso: string | null): string {
  if (!iso) return '\u2014';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '\u2014' : d.toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' });
}

/* ═══════════════════════════════════════════════════════════════════════════
   Constants
   ═══════════════════════════════════════════════════════════════════════════ */

const TASK_STATUS: Record<string, { label: string }> = {
  backlog: { label: 'Backlog' }, todo: { label: 'To Do' }, in_progress: { label: 'In Progress' },
  in_review: { label: 'In Review' }, blocked: { label: 'Blocked' }, handoff: { label: 'Handoff' },
  done: { label: 'Done' }, cancelled: { label: 'Cancelled' },
  open: { label: 'Open' }, detected: { label: 'Detected' }, queued: { label: 'Queued' },
  in_analysis: { label: 'In Analysis' }, work_ready: { label: 'Work Ready' },
  handed_off: { label: 'Handed Off' }, closed: { label: 'Closed' },
};

const TASK_STATUS_ORDER = ['backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'handoff', 'done', 'cancelled'];

const TASK_TYPE: Record<string, { label: string }> = {
  task: { label: 'Task' }, feature: { label: 'Feature' }, bug: { label: 'Bug' },
  research: { label: 'Research' }, ops: { label: 'Ops' }, integration: { label: 'Integration' },
  docs: { label: 'Docs' }, pr_review: { label: 'PR Review' },
};

const APPROVAL_STATUS: Record<string, { label: string; tone: string }> = {
  pending: { label: 'Pending', tone: 'warn' },
  approved: { label: 'Approved', tone: 'success' },
  rejected: { label: 'Rejected', tone: 'neutral' },
};

const WORK_PACKAGE_FIELDS = [
  { key: 'objective', label: 'Objective' },
  { key: 'plan', label: 'Execution Plan' },
  { key: 'deliverable', label: 'Deliverable' },
  { key: 'links', label: 'Links / References' },
  { key: 'prDescription', label: 'PR Description (GitHub)' },
  { key: 'diff', label: 'Diff Summary (GitHub)' },
  { key: 'review', label: 'Review Notes (GitHub)' },
];

const AGENT_COLORS: Record<string, string> = {
  esprit: '#5b7bf7',
  ronnor: '#a78bfa',
  admin: '#f59e0b',
  agent: '#34d399',
};

const STATUS_COLORS: Record<string, { bg: string; icon: string }> = {
  backlog: { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  todo: { bg: '#eff6ff', icon: '#3b82f6' },
  in_progress: { bg: '#fefce8', icon: '#f59e0b' },
  in_review: { bg: '#eef2ff', icon: '#6366f1' },
  handoff: { bg: '#f5f3ff', icon: '#8b5cf6' },
  done: { bg: '#ecfdf5', icon: '#10b981' },
  cancelled: { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  open: { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  work_ready: { bg: '#ecfdf5', icon: '#10b981' },
  in_analysis: { bg: '#eff6ff', icon: '#3b82f6' },
  queued: { bg: '#fefce8', icon: '#f59e0b' },
  detected: { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  handed_off: { bg: '#f5f3ff', icon: '#8b5cf6' },
  blocked: { bg: '#fff1f2', icon: '#ef4444' },
  closed: { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
};

/* ═══════════════════════════════════════════════════════════════════════════
   Shared tiny components
   ═══════════════════════════════════════════════════════════════════════════ */

function Spinner({ className = '' }: { className?: string }) {
  return <Loader2 className={`animate-spin text-[color:var(--text-muted)] ${className}`} size={20} />;
}

function EmptyState({ icon: Icon, label }: { icon: typeof LayoutGrid; label: string }) {
  return (
    <div className="py-16 flex flex-col items-center justify-center text-[color:var(--text-muted)] opacity-40 gap-3">
      <Icon size={32} strokeWidth={1} />
      <p className="text-[10px] font-medium uppercase tracking-widest">{label}</p>
    </div>
  );
}

function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  const colors: Record<string, string> = {
    neutral: 'bg-[color:var(--surface-2)] text-[color:var(--text-secondary)] border-[color:var(--border-subtle)]',
    success: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    warn: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    danger: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
    info: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-widest border ${colors[tone] || colors.neutral}`}>
      {children}
    </span>
  );
}

/** Portal-based modal */
function Modal({ children, onClose, title, maxWidth = '560px' }: { children: ReactNode; onClose: () => void; title: string; maxWidth?: string }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-150">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] shadow-2xl animate-in zoom-in-95 duration-150 overflow-hidden" style={{ maxWidth }}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
          <h2 className="text-sm font-bold">{title}</h2>
          <button onClick={onClose} className="p-1 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ConfirmDialog
   ═══════════════════════════════════════════════════════════════════════════ */

function ConfirmDialog({ title = 'Confirm', message, confirmLabel = 'Delete', onConfirm, onCancel }: {
  title?: string; message: string; confirmLabel?: string; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Modal title={title} onClose={onCancel} maxWidth="400px">
      <div className="p-5">
        <p className="text-sm leading-relaxed text-[color:var(--text-secondary)]">{message}</p>
      </div>
      <div className="flex items-center gap-3 px-5 py-3 border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
        <button className="flex-1 h-9 rounded-lg text-xs font-bold border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors" onClick={onCancel}>Cancel</button>
        <button className="flex-1 h-9 rounded-lg text-xs font-bold bg-rose-600 text-white hover:bg-rose-700 transition-colors" onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </Modal>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ListCard
   ═══════════════════════════════════════════════════════════════════════════ */

function ListCard({ active, onClick, avatarStyle, avatarContent, title, subtitle, meta, badge }: {
  active: boolean;
  onClick: () => void;
  avatarStyle?: React.CSSProperties;
  avatarContent: ReactNode;
  title: string;
  subtitle?: string;
  meta?: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <article
      className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-colors rounded-lg ${
        active
          ? 'bg-[color:var(--surface-accent)] border border-[color:var(--accent-solid)]/20'
          : 'hover:bg-[color:var(--surface-1)] border border-transparent'
      }`}
      onClick={onClick}
    >
      <div
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-xs font-bold"
        style={avatarStyle}
      >
        {avatarContent}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold text-[color:var(--text-primary)] truncate">{title}</span>
          {meta != null && <span className="text-[10px] text-[color:var(--text-muted)] font-mono shrink-0">{meta}</span>}
        </div>
        <div className="flex items-center justify-between gap-2 mt-0.5">
          <span className="text-[11px] text-[color:var(--text-muted)] truncate">{subtitle}</span>
          {badge}
        </div>
      </div>
    </article>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   DynamicForm — modal form for create / edit
   ═══════════════════════════════════════════════════════════════════════════ */

function DynamicForm({ title, fields, initial = {}, saving, onSubmit, onClose }: {
  title: string;
  fields: any[];
  initial?: Record<string, any>;
  saving: boolean;
  onSubmit: (data: Record<string, any>) => void;
  onClose: () => void;
}) {
  const formFields = fields.filter((f: any) => f.type !== 'readonly');
  const [form, setForm] = useState<Record<string, any>>(() =>
    Object.fromEntries(formFields.map((f: any) => [f.key, initial[f.key] ?? '']))
  );

  const set = (key: string, val: any) => setForm(prev => ({ ...prev, [key]: val }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(form);
  };

  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={handleSubmit} className="p-5 space-y-4">
        {formFields.map((field: any, i: number) => (
          <div key={field.key} className="space-y-1.5">
            <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
              {field.label}
              {field.required && <span className="text-rose-400 ml-1">*</span>}
            </label>
            <FormFieldInput field={field} value={form[field.key]} onChange={(val: any) => set(field.key, val)} autoFocus={i === 0} />
          </div>
        ))}
        <div className="flex items-center gap-3 pt-3 border-t border-[color:var(--border-subtle)]">
          <button type="button" className="flex-1 h-9 rounded-lg text-xs font-bold border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors" onClick={onClose}>Cancel</button>
          <button type="submit" className="flex-1 h-9 rounded-lg text-xs font-bold bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity" disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function FormFieldInput({ field, value, onChange, autoFocus = false }: { field: any; value: any; onChange: (v: any) => void; autoFocus?: boolean }) {
  const inputCls = 'w-full h-9 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';

  if (field.type === 'textarea') {
    return (
      <textarea
        className={`${inputCls} min-h-[80px] py-2 resize-y`}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        autoFocus={autoFocus}
        rows={3}
      />
    );
  }
  if (field.type === 'select') {
    return (
      <select className={inputCls} value={value ?? ''} onChange={e => onChange(e.target.value)} autoFocus={autoFocus}>
        <option value="">{'\u2014'} select {'\u2014'}</option>
        {(field.options || []).map((opt: string) => <option key={opt} value={opt}>{opt}</option>)}
      </select>
    );
  }
  const typeMap: Record<string, string> = { email: 'email', url: 'url', number: 'number', date: 'date' };
  return (
    <input
      className={inputCls}
      type={typeMap[field.type] || 'text'}
      value={value ?? ''}
      required={field.required}
      onChange={e => onChange(e.target.value)}
      autoFocus={autoFocus}
    />
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   DynamicDetailPane — right-side record detail
   ═══════════════════════════════════════════════════════════════════════════ */

function DynamicDetailPane({ config, record, saving, onPatch, onDelete, onAction, onEdit }: {
  config: any; record: any; saving: boolean;
  onPatch: (patch: Record<string, any>) => void;
  onDelete: () => void;
  onAction: (actionId: string) => void;
  onEdit: () => void;
}) {
  if (!record) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-[color:var(--text-muted)] gap-2 opacity-50">
        <ArrowLeft size={24} strokeWidth={1} />
        <p className="text-xs font-medium">Select a record</p>
      </div>
    );
  }

  const fields = (config.fields || []).filter((f: any) => f.type !== 'readonly' || record[f.key]);
  const detailActions = (config.actions || []).filter((a: any) => (a.type || a.placement) === 'record' || (a.type || a.placement) === 'detail');
  const titleField = config.fields_config?.titleField || 'id';

  return (
    <div className="flex flex-col h-full">
      {/* Hero */}
      <div className="px-5 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-bold text-[color:var(--text-primary)] truncate">{record[titleField] || record.id}</h2>
            {config.fields_config?.subtitleField && (
              <p className="text-[11px] text-[color:var(--text-muted)] truncate mt-0.5">{record[config.fields_config.subtitleField]}</p>
            )}
          </div>
          {config.fields_config?.badgeField && record[config.fields_config.badgeField] && (
            <Badge>{record[config.fields_config.badgeField]}</Badge>
          )}
        </div>
      </div>

      {/* Fields */}
      <div className="flex-1 overflow-y-auto p-5 space-y-4">
        {fields.map((field: any) => (
          <DetailFieldView key={field.key} field={field} value={record[field.key]} onBlur={(val: any) => onPatch({ [field.key]: val })} />
        ))}

        {/* Actions */}
        <div className="flex items-center gap-2 pt-4 border-t border-[color:var(--border-subtle)] flex-wrap">
          <button
            className="h-8 px-4 rounded-lg text-[10px] font-bold uppercase tracking-widest border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors"
            onClick={onEdit}
            disabled={saving}
          >
            <Pencil size={11} className="inline mr-1.5 -mt-0.5" />Edit
          </button>
          {detailActions.map((action: any) => {
            if (action.type === 'delete') {
              return (
                <button key={action.id} className="h-8 px-4 rounded-lg text-[10px] font-bold uppercase tracking-widest bg-rose-600/10 text-rose-500 hover:bg-rose-600 hover:text-white transition-colors" onClick={onDelete} disabled={saving}>
                  {action.label}
                </button>
              );
            }
            return (
              <button key={action.id} className="h-8 px-4 rounded-lg text-[10px] font-bold uppercase tracking-widest border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors" onClick={() => onAction(action.id)} disabled={saving}>
                {action.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function DetailFieldView({ field, value, onBlur }: { field: any; value: any; onBlur: (v: any) => void }) {
  if (value == null || value === '') return null;

  const labelCls = 'text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1.5';
  const inputCls = 'w-full h-9 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 text-xs text-[color:var(--text-primary)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';

  if (field.type === 'textarea') {
    return (
      <div>
        <label className={labelCls}>{field.label}</label>
        <textarea
          className={`${inputCls} min-h-[80px] py-2 resize-y`}
          defaultValue={value}
          onBlur={e => onBlur(e.target.value)}
          rows={3}
        />
      </div>
    );
  }
  if (field.type === 'select') {
    return (
      <div>
        <label className={labelCls}>{field.label}</label>
        <select className={inputCls} defaultValue={value} onChange={e => onBlur(e.target.value)}>
          {(field.options || []).map((opt: string) => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      </div>
    );
  }
  if (field.type === 'readonly' || field.type === 'badge') {
    return (
      <div>
        <label className={labelCls}>{field.label}</label>
        <Badge>{value}</Badge>
      </div>
    );
  }
  if (field.type === 'url') {
    return (
      <div>
        <label className={labelCls}>{field.label}</label>
        <a href={value} target="_blank" rel="noopener noreferrer" className="text-[color:var(--accent-solid)] text-xs hover:underline break-all">{value}</a>
      </div>
    );
  }
  const typeMap: Record<string, string> = { email: 'email', url: 'url', number: 'number', date: 'date' };
  return (
    <div>
      <label className={labelCls}>{field.label}</label>
      <input className={inputCls} type={typeMap[field.type] || 'text'} defaultValue={value} onBlur={e => onBlur(e.target.value)} />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ApiModule — tool module view with actions, secrets, run
   ═══════════════════════════════════════════════════════════════════════════ */

function ApiModule({ config, hideHeader = false }: { config: any; hideHeader?: boolean }) {
  const [secretsStatus, setSecretsStatus] = useState<Record<string, boolean>>({});
  const [records, setRecords] = useState<any[]>([]);
  const [search, setSearch] = useState('');
  const [copied, setCopied] = useState(false);

  // Load records for record-type action pickers
  const hasRecordActions = (config.actions || []).some((a: any) => {
    const t = a.type || a.placement || 'standalone';
    return t === 'record' || t === 'detail';
  });

  useEffect(() => {
    if (!hasRecordActions || !(config.fields || []).length) return;
    araiosApi<{ records: any[] }>(`/api/modules/${config.name}/records`)
      .then(res => setRecords(res.records || []))
      .catch(() => {});
  }, [config.name, hasRecordActions]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSecrets = useCallback(async () => {
    if (!(config.secrets || []).length) return;
    try {
      const res = await araiosApi<{ secrets: Record<string, boolean> }>(`/api/modules/${config.name}/secrets-status`);
      setSecretsStatus(res.secrets || {});
    } catch { /* non-fatal */ }
  }, [config.name, config.secrets]);

  useEffect(() => { loadSecrets(); }, [loadSecrets]);

  const copyPrompt = () => {
    const BASE_URL = window.location.origin;
    const lines = [
      `Module: ${config.label} (${config.name}) \u2014 ${config.description || ''}`,
      `Type: tool \u2014 call actions via POST, no records stored.`,
      '', 'Actions:',
    ];
    for (const action of (config.actions || [])) {
      lines.push(`  ${action.id} \u2014 ${action.description || action.label}`);
      lines.push(`    POST ${BASE_URL}/api/modules/${config.name}/action/${action.id}`);
      lines.push(`    Body: { ${(action.params || []).map((p: any) => `"${p.key}"${p.required ? '*' : ''}: "${p.placeholder || p.type}"`).join(', ')} }`);
    }
    navigator.clipboard.writeText(lines.join('\n'));
    setCopied(true);
    toast.success('Prompt copied');
    setTimeout(() => setCopied(false), 2000);
  };

  const resetSecret = async (key: string, label: string) => {
    try {
      await araiosApi(`/api/modules/${config.name}/secrets/${key}`, { method: 'DELETE' });
      toast.success(`${label} cleared`);
      loadSecrets();
    } catch { toast.error('Could not clear secret'); }
  };

  const allRequired = (config.secrets || []).filter((s: any) => s.required);
  const missingRequired = allRequired.filter((s: any) => !secretsStatus[s.key]);

  return (
    <div className="flex flex-col h-full">
      {!hideHeader && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm font-medium text-[color:var(--text-secondary)]">{config.label}</span>
            {(config.secrets || []).map((s: any) =>
              secretsStatus[s.key] ? (
                <span key={s.key} className="flex items-center gap-1">
                  <Badge tone="success">{'\\u2713'} {s.label}</Badge>
                  <button className="text-xs text-[color:var(--text-muted)] hover:text-rose-400 transition-colors" onClick={() => resetSecret(s.key, s.label)} title="Clear">&times;</button>
                </span>
              ) : (
                <Badge key={s.key} tone="warn">{'\\u2717'} {s.label}</Badge>
              )
            )}
          </div>
          <div className="flex items-center gap-2">
            {(config.actions || []).length > 2 && (
              <div className="relative">
                <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
                <input
                  className="h-8 w-48 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] pl-8 pr-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)]"
                  placeholder="Search actions..."
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                />
              </div>
            )}
            <button
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors"
              onClick={copyPrompt}
            >
              <Copy size={12} />
              <span className="text-[10px] font-bold uppercase tracking-widest">{copied ? 'Copied!' : 'Prompt'}</span>
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-[720px] mx-auto space-y-4">
          {/* Secret config cards */}
          {(config.secrets || []).filter((s: any) => !secretsStatus[s.key]).map((s: any) => (
            <SecretCard key={s.key} moduleName={config.name} secret={s} onSaved={loadSecrets} />
          ))}
          {missingRequired.length > 0 && (config.actions || []).length > 0 && (
            <div className="text-xs text-[color:var(--text-muted)] py-2 border-t border-[color:var(--border-subtle)]">
              Configure required secrets above to run actions
            </div>
          )}

          {/* Action cards */}
          {(config.actions || []).filter((a: any) =>
            !search.trim() || a.label.toLowerCase().includes(search.toLowerCase()) || (a.description || '').toLowerCase().includes(search.toLowerCase())
          ).map((action: any) => (
            <ActionCard key={action.id} action={action} moduleName={config.name} secretsStatus={secretsStatus} requiredSecrets={config.secrets || []} records={records} fieldsConfig={config.fields_config} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SecretCard({ moduleName, secret, onSaved }: { moduleName: string; secret: any; onSaved: () => void }) {
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!value.trim()) return;
    try {
      setSaving(true);
      await araiosApi(`/api/modules/${moduleName}/secrets/${secret.key}`, { method: 'PUT', body: { value } });
      setValue('');
      toast.success(`${secret.label} saved`);
      onSaved();
    } catch { toast.error('Could not save secret'); }
    finally { setSaving(false); }
  };

  const inputCls = 'w-full h-9 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';

  return (
    <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-4 flex items-end gap-3">
      <div className="flex-1 space-y-1.5">
        <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
          <span className="text-rose-400 mr-1">{'\u2717'}</span>
          {secret.label}
          {secret.hint && <span className="text-[color:var(--text-muted)] ml-2 font-normal normal-case">{secret.hint}</span>}
        </label>
        <input className={inputCls} type="password" placeholder="Paste value..." value={value} onChange={e => setValue(e.target.value)} onKeyDown={e => e.key === 'Enter' && save()} />
      </div>
      <button className="h-9 px-4 rounded-lg text-xs font-bold bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity shrink-0" onClick={save} disabled={saving || !value.trim()}>
        {saving ? 'Saving...' : 'Save'}
      </button>
    </div>
  );
}

function ActionCard({ action, moduleName, secretsStatus, requiredSecrets, records, fieldsConfig }: {
  action: any; moduleName: string; secretsStatus: Record<string, boolean>; requiredSecrets: any[];
  records?: any[]; fieldsConfig?: any;
}) {
  const params = action.params || [];
  const [form, setForm] = useState<Record<string, string>>(() => Object.fromEntries(params.map((p: any) => [p.key, ''])));
  const [selectedRecordId, setSelectedRecordId] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; data?: any; error?: string } | null>(null);
  const [expanded, setExpanded] = useState(false);

  const actionType = action.type || action.placement || 'standalone';
  const isRecordAction = actionType === 'record' || actionType === 'detail';
  const missingSecrets = requiredSecrets.filter((s: any) => s.required && !secretsStatus[s.key]);
  const needsRecord = isRecordAction && !selectedRecordId;
  const disabled = missingSecrets.length > 0 || needsRecord;

  const titleField = fieldsConfig?.titleField || 'id';

  const inputCls = 'w-full h-9 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';

  const run = async () => {
    const missing = params.filter((p: any) => p.required && !String(form[p.key] ?? '').trim());
    if (missing.length) { toast.error(`Required: ${missing.map((p: any) => p.label).join(', ')}`); return; }
    if (isRecordAction && !selectedRecordId) { toast.error('Select a record first'); return; }
    try {
      setRunning(true);
      setResult(null);
      const url = isRecordAction && selectedRecordId
        ? `/api/modules/${moduleName}/records/${selectedRecordId}/action/${action.id}`
        : `/api/modules/${moduleName}/action/${action.id}`;
      const res = await araiosApi(url, { method: 'POST', body: form });
      const ok = res?.ok !== false;
      setResult({ ok, data: res });
      if (!ok) toast.error(res?.error || 'Action returned an error');
    } catch (err: any) {
      setResult({ ok: false, error: err.message });
      toast.error(err.message || 'Action failed');
    } finally {
      setRunning(false);
      setExpanded(true);
    }
  };

  return (
    <div className={`rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] overflow-hidden ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-start justify-between p-4 gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-[color:var(--text-primary)]">{action.label}</h3>
          {action.description && <p className="text-xs text-[color:var(--text-muted)] mt-0.5">{action.description}</p>}
        </div>
        <button className="h-8 px-4 rounded-lg text-[10px] font-bold uppercase tracking-widest bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity shrink-0 flex items-center gap-1.5" onClick={run} disabled={running || disabled}>
          {running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {running ? 'Running...' : 'Run'}
        </button>
      </div>

      {(isRecordAction || params.length > 0) && (
        <div className="px-4 pb-4 space-y-3 border-t border-[color:var(--border-subtle)] pt-3">
          {isRecordAction && (
            <div className="space-y-1.5">
              <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                Record <span className="text-rose-400 ml-1">*</span>
              </label>
              <select className={inputCls} value={selectedRecordId} onChange={e => setSelectedRecordId(e.target.value)}>
                <option value="">— select a record —</option>
                {(records || []).map((rec: any) => (
                  <option key={rec.id} value={rec.id}>{rec[titleField] || rec.id}</option>
                ))}
              </select>
            </div>
          )}
          {params.map((param: any) => (
            <div key={param.key} className="space-y-1.5">
              <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">
                {param.label}
                {param.required && <span className="text-rose-400 ml-1">*</span>}
              </label>
              {param.type === 'textarea' ? (
                <textarea className={`${inputCls} min-h-[80px] py-2 resize-y`} rows={3}
                  value={form[param.key]} onChange={e => setForm(prev => ({ ...prev, [param.key]: e.target.value }))}
                  placeholder={param.placeholder || ''} />
              ) : (
                <input className={inputCls}
                  type={param.type === 'number' ? 'number' : 'text'}
                  value={form[param.key]} onChange={e => setForm(prev => ({ ...prev, [param.key]: e.target.value }))}
                  placeholder={param.placeholder || ''} />
              )}
            </div>
          ))}
        </div>
      )}

      {result && (
        <div className="border-t border-[color:var(--border-subtle)]">
          <button className="flex w-full items-center justify-between px-4 py-2 text-xs hover:bg-[color:var(--surface-2)] transition-colors" onClick={() => setExpanded(e => !e)}>
            <span className={result.ok ? 'text-emerald-400 font-bold' : 'text-rose-400 font-bold'}>
              {result.ok ? '\u2713 Success' : '\u2717 Error'}
            </span>
            <span className="text-[color:var(--text-muted)]">{expanded ? '\u25B2 hide' : '\u25BC show'}</span>
          </button>
          {expanded && (
            <pre className="px-4 pb-4 text-[11px] text-[color:var(--text-secondary)] overflow-x-auto whitespace-pre-wrap break-all font-mono">
              {JSON.stringify(result.ok ? result.data : { error: result.error }, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   PageModule — single editable document page
   ═══════════════════════════════════════════════════════════════════════════ */

function PageModule({ config }: { config: any }) {
  const [content, setContent] = useState(config.page_content || '');
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const save = async () => {
    try {
      setSaving(true);
      await araiosApi(`/api/modules/${config.name}`, { method: 'PATCH', body: { page_content: content } });
      toast.success('Page saved');
      setDirty(false);
      setEditing(false);
    } catch { toast.error('Could not save'); }
    finally { setSaving(false); }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
        <span className="text-sm font-medium text-[color:var(--text-secondary)]">{config.page_title}</span>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <button className="h-7 px-3 rounded-lg text-[10px] font-bold text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)] transition-colors" onClick={() => { setEditing(false); setContent(config.page_content || ''); setDirty(false); }}>
                Cancel
              </button>
              <button className="h-7 px-3 rounded-lg text-[10px] font-bold bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity disabled:opacity-40" onClick={save} disabled={saving || !dirty}>
                {saving ? 'Saving...' : 'Save'}
              </button>
            </>
          ) : (
            <button className="h-7 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors flex items-center gap-1.5" onClick={() => setEditing(true)}>
              <Pencil size={11} /> Edit
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-[720px] mx-auto">
          {editing ? (
            <textarea
              className="w-full min-h-[400px] rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 text-sm font-mono text-[color:var(--text-primary)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors resize-y"
              value={content}
              onChange={e => { setContent(e.target.value); setDirty(true); }}
              placeholder="Write markdown content..."
            />
          ) : content ? (
            <div className="prose prose-invert max-w-none text-sm text-[color:var(--text-primary)] leading-relaxed">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          ) : (
            <EmptyState icon={FileText} label="No page content yet" />
          )}
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ModulePage — full triage view for a single module
   ═══════════════════════════════════════════════════════════════════════════ */

function ModulePage({ moduleName, onBack }: { moduleName: string; onBack: () => void }) {
  const [config, setConfig] = useState<any>(null);
  const [records, setRecords] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<'records' | 'actions'>('records');

  const loadAll = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const [cfgRes, recRes] = await Promise.all([
        araiosApi(`/api/modules/${moduleName}`),
        araiosApi<{ records: any[] }>(`/api/modules/${moduleName}/records`),
      ]);
      setConfig(cfgRes);
      const recs = Array.isArray(recRes?.records) ? recRes.records : [];
      setRecords(recs);
      setSelectedId(prev => {
        if (prev && recs.some((r: any) => r.id === prev)) return prev;
        return recs[0]?.id || null;
      });
    } catch { toast.error(`Failed to load ${moduleName}`); }
    finally { setLoading(false); }
  }, [moduleName]);

  useEffect(() => {
    setSelectedId(null);
    setFilter('all');
    setSearch('');
    setActiveTab('records');
    loadAll();
    const timer = setInterval(() => loadAll(true), 30000);
    return () => clearInterval(timer);
  }, [moduleName]); // eslint-disable-line react-hooks/exhaustive-deps

  const filterField = config?.fields_config?.filterField;
  const titleField = config?.fields_config?.titleField || 'id';
  const subtitleField = config?.fields_config?.subtitleField;
  const badgeField = config?.fields_config?.badgeField;
  const metaField = config?.fields_config?.metaField;

  const filterValues = useMemo(() => {
    if (!filterField) return [];
    return [...new Set(records.map((r: any) => r[filterField]).filter(Boolean))] as string[];
  }, [records, filterField]);

  const filtered = useMemo(() => {
    let list = records;
    if (filter !== 'all' && filterField) list = list.filter((r: any) => r[filterField] === filter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter((r: any) => Object.values(r).some(v => String(v ?? '').toLowerCase().includes(q)));
    }
    return list;
  }, [records, filter, filterField, search]);

  const selectedRecord = useMemo(() => records.find((r: any) => r.id === selectedId) || null, [records, selectedId]);

  const standaloneActions = useMemo(() => (config?.actions || []).filter((a: any) => {
    const t = a.type || a.placement || 'standalone';
    return t === 'standalone';
  }), [config]);
  const hasStandaloneActions = standaloneActions.length > 0;
  // Data modules always support record creation via the generic CRUD endpoint.
  // A custom 'create' action only overrides the button label.
  const createAction = (config?.actions || []).find((a: any) => a.type === 'create');

  // CRUD
  const handleCreate = async (data: Record<string, any>) => {
    try {
      setSaving(true);
      const rec = await araiosApi(`/api/modules/${moduleName}/records`, { method: 'POST', body: data });
      setCreateOpen(false);
      toast.success('Created');
      await loadAll(true);
      setSelectedId(rec.id);
    } catch { toast.error('Create failed'); }
    finally { setSaving(false); }
  };

  const handlePatch = async (patch: Record<string, any>) => {
    if (!selectedId) return;
    try {
      setSaving(true);
      await araiosApi(`/api/modules/${moduleName}/records/${selectedId}`, { method: 'PATCH', body: patch });
      toast.success('Saved');
      await loadAll(true);
    } catch { toast.error('Save failed'); }
    finally { setSaving(false); }
  };

  const handleDelete = async (id: string) => {
    try {
      setSaving(true);
      await araiosApi(`/api/modules/${moduleName}/records/${id}`, { method: 'DELETE' });
      setConfirmDeleteId(null);
      toast.success('Deleted');
      await loadAll(true);
    } catch { toast.error('Delete failed'); }
    finally { setSaving(false); }
  };

  const handleAction = async (actionId: string) => {
    if (!selectedId) return;
    try {
      setSaving(true);
      const res = await araiosApi(`/api/modules/${moduleName}/records/${selectedId}/action/${actionId}`, { method: 'POST', body: {} });
      if (res?.ok !== false) toast.success('Action completed');
      else toast.error(res?.error || 'Action returned an error');
      await loadAll(true);
    } catch { toast.error('Action failed'); }
    finally { setSaving(false); }
  };

  const copyPrompt = () => {
    if (!config) return;
    const BASE_URL = window.location.origin;
    const base = `${BASE_URL}/api/modules/${config.name}`;
    const flds = (config.fields || []).map((f: any) => `${f.key}${f.required ? '*' : ''} (${f.type}${f.options ? ': ' + f.options.join('|') : ''})`).join(', ');
    const lines = [`Module: ${config.label} (${config.name})`, `Type: data`, '', 'Endpoints:',
      `  GET    ${base}/records`, `  POST   ${base}/records`, `  PATCH  ${base}/records/:id`, `  DELETE ${base}/records/:id`, '',
      `Fields: ${flds || 'none'}`];
    navigator.clipboard.writeText(lines.join('\n'));
    setCopied(true);
    toast.success('Prompt copied');
    setTimeout(() => setCopied(false), 2000);
  };

  // Routing
  if (!config && loading) return <div className="flex items-center justify-center h-full"><Spinner /></div>;
  if (!config) return null;

  // Determine available tabs based on module capabilities
  const hasFields = (config.fields || []).length > 0;
  const hasActions = (config.actions || []).length > 0;
  const hasPage = Boolean(config.page_title);
  const tabs: string[] = [];
  if (hasFields) tabs.push('records');
  if (hasActions) tabs.push('actions');
  if (hasPage) tabs.push('page');
  // If no capabilities at all, show records tab (empty state)
  if (tabs.length === 0) tabs.push('records');

  // Default to first available tab, but respect user's choice if valid
  const effectiveTab = tabs.includes(activeTab) ? activeTab : tabs[0];

  const inputCls = 'h-8 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';

  return (
    <div className="flex flex-col h-full">
      {/* Header bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] gap-3">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="p-1.5 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors">
            <ArrowLeft size={16} />
          </button>
          <span className="text-sm font-medium text-[color:var(--text-secondary)]">{config.label}</span>
          {tabs.length > 1 && (
            <div className="flex items-center gap-1 ml-2">
              {tabs.map(tab => (
                <button key={tab} onClick={() => setActiveTab(tab)}
                  className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest transition-colors ${
                    effectiveTab === tab
                      ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                      : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)]'
                  }`}>{tab}</button>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {effectiveTab === 'records' && (
            <input className={`${inputCls} w-48`} placeholder="Search..." value={search} onChange={e => setSearch(e.target.value)} />
          )}
          <button className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)] transition-colors" onClick={copyPrompt}>
            <Copy size={12} />
            <span className="text-[10px] font-bold uppercase tracking-widest">{copied ? 'Copied!' : 'Prompt'}</span>
          </button>
          {effectiveTab === 'records' && (
            <button className="h-8 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity flex items-center gap-1.5" onClick={() => setCreateOpen(true)}>
              <Plus size={12} />{createAction?.label || `New`}
            </button>
          )}
        </div>
      </div>

      {/* Actions tab */}
      {effectiveTab === 'actions' && (
        <div className="flex-1 min-h-0">
          <ApiModule config={config} hideHeader />
        </div>
      )}

      {/* Page tab */}
      {effectiveTab === 'page' && (
        <div className="flex-1 min-h-0"><PageModule config={config} /></div>
      )}

      {/* Records tab */}
      {effectiveTab === 'records' && (
        <div className="flex flex-1 min-h-0">
          {/* Left list */}
          <div className="w-80 shrink-0 border-r border-[color:var(--border-subtle)] flex flex-col bg-[color:var(--surface-0)]">
            {filterValues.length > 0 && (
              <div className="flex items-center gap-1 px-3 py-2 border-b border-[color:var(--border-subtle)] flex-wrap">
                {['all', ...filterValues].map(v => (
                  <button key={v} onClick={() => setFilter(v)}
                    className={`px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest transition-colors ${
                      filter === v
                        ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                        : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
                    }`}>{v}</button>
                ))}
              </div>
            )}
            <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
              {loading && <div className="p-4 text-xs text-[color:var(--text-muted)]">Loading...</div>}
              {!loading && filtered.length === 0 && <div className="p-4 text-xs text-[color:var(--text-muted)]">No records found.</div>}
              {filtered.map((rec: any) => {
                const title = rec[titleField] || rec.id;
                const hue = avatarHue(title);
                const badge_ = badgeField && rec[badgeField] ? <Badge>{rec[badgeField]}</Badge> : null;
                const meta = metaField ? (rec[metaField] ? (shortDate(rec[metaField]) || rec[metaField]) : null) : null;
                return (
                  <ListCard
                    key={rec.id}
                    active={rec.id === selectedId}
                    onClick={() => setSelectedId(rec.id)}
                    avatarStyle={{ backgroundColor: `hsl(${hue}, 50%, 30%)`, color: '#fff' }}
                    avatarContent={initials(title)}
                    title={title}
                    subtitle={subtitleField ? (rec[subtitleField] || '\u2014') : '\u2014'}
                    meta={meta}
                    badge={badge_}
                  />
                );
              })}
            </div>
          </div>

          {/* Right detail */}
          <div className="flex-1 min-w-0">
            <DynamicDetailPane
              config={config}
              record={selectedRecord}
              saving={saving}
              onPatch={handlePatch}
              onDelete={() => setConfirmDeleteId(selectedId)}
              onAction={handleAction}
              onEdit={() => setEditOpen(true)}
            />
          </div>
        </div>
      )}

      {/* Modals */}
      {createOpen && (
        <DynamicForm
          fields={config.fields || []}
          onSubmit={handleCreate}
          onClose={() => setCreateOpen(false)}
          saving={saving}
          title={`New ${config.label}`}
        />
      )}
      {editOpen && selectedRecord && (
        <DynamicForm
          fields={config.fields || []}
          initial={selectedRecord}
          onSubmit={async (data) => { await handlePatch(data); setEditOpen(false); }}
          onClose={() => setEditOpen(false)}
          saving={saving}
          title={`Edit ${selectedRecord[titleField] || 'Record'}`}
        />
      )}
      {confirmDeleteId && (
        <ConfirmDialog
          message="Delete this record? This cannot be undone."
          onConfirm={() => handleDelete(confirmDeleteId)}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ModulesSection — grid of modules, drill-in to ModulePage
   ═══════════════════════════════════════════════════════════════════════════ */

function ModulesSection() {
  const [modules, setModules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeModule, setActiveModule] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await araiosApi<{ modules: any[] }>('/api/modules');
      setModules(data.modules || []);
    } catch { toast.error('Failed to load modules'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (activeModule) {
    return <ModulePage moduleName={activeModule} onBack={() => setActiveModule(null)} />;
  }

  if (loading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;

  return (
    <div className="space-y-4 p-4 md:p-6 overflow-y-auto h-full">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Registered Modules</h2>
        <span className="text-[10px] bg-[color:var(--surface-2)] text-[color:var(--text-muted)] px-2 py-0.5 rounded font-bold">{modules.length}</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {modules.map((mod) => (
          <button
            key={mod.name}
            onClick={() => setActiveModule(mod.name)}
            className="text-left rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 space-y-2 hover:border-[color:var(--border-strong)] transition-colors"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                {(() => { const Icon = resolveIcon(mod.icon); return <Icon size={16} className="text-[color:var(--text-muted)] shrink-0" />; })()}
                <span className="text-sm font-bold text-[color:var(--text-primary)]">{mod.label}</span>
              </div>
              <div className="flex items-center gap-1">
                {(mod.fields || []).length > 0 && <span className="text-[8px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400">records</span>}
                {(mod.actions || []).length > 0 && <span className="text-[8px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">actions</span>}
                {mod.page_title && <span className="text-[8px] font-bold uppercase tracking-widest px-1.5 py-0.5 rounded bg-purple-500/10 text-purple-400">page</span>}
              </div>
            </div>
            <p className="text-[11px] text-[color:var(--text-muted)] leading-relaxed line-clamp-2">{mod.description || 'No description'}</p>
            <div className="text-[10px] text-[color:var(--text-muted)]">
              {(mod.fields || []).length > 0 && <>{(mod.fields || []).length} fields</>}
              {(mod.fields || []).length > 0 && (mod.actions || []).length > 0 && <> · </>}
              {(mod.actions || []).length > 0 && <>{(mod.actions || []).length} actions</>}
              {(mod.fields || []).length === 0 && (mod.actions || []).length === 0 && !mod.page_title && <>empty module</>}
            </div>
          </button>
        ))}
        {modules.length === 0 && (
          <div className="col-span-full">
            <EmptyState icon={LayoutGrid} label="No modules registered" />
          </div>
        )}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   ApprovalsSection
   ═══════════════════════════════════════════════════════════════════════════ */

function ApprovalsSection() {
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<'pending' | 'approved' | 'rejected'>('pending');
  const [processingId, setProcessingId] = useState('');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await araiosApi<{ approvals: any[] }>('/api/approvals');
      setApprovals(data.approvals || []);
    } catch { toast.error('Failed to load approvals'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = approvals.filter(a => a.status === statusFilter);
  const counts = { pending: 0, approved: 0, rejected: 0 };
  approvals.forEach(a => { if (a.status in counts) counts[a.status as keyof typeof counts]++; });

  const handleResolve = async (id: string, action: 'approve' | 'reject') => {
    try {
      setProcessingId(id);
      await araiosApi(`/api/approvals/${id}/${action}`, { method: 'POST' });
      toast.success(action === 'approve' ? 'Approved' : 'Rejected');
      load();
    } catch { toast.error(`Failed to ${action}`); }
    finally { setProcessingId(''); }
  };

  if (loading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;

  return (
    <div className="space-y-4">
      {/* Status filter chips */}
      <div className="flex items-center gap-2">
        {(['pending', 'approved', 'rejected'] as const).map(s => {
          const info = APPROVAL_STATUS[s];
          return (
            <button key={s} onClick={() => setStatusFilter(s)}
              className={`px-3 py-1.5 rounded-full text-[10px] font-bold uppercase tracking-widest transition-colors flex items-center gap-1.5 ${
                statusFilter === s
                  ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                  : 'bg-[color:var(--surface-2)] text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'
              }`}>
              {info.label}
              <span className="text-[9px] opacity-70">({counts[s]})</span>
            </button>
          );
        })}
      </div>

      {filtered.length === 0 ? (
        <EmptyState icon={CheckCircle} label={statusFilter === 'pending' ? 'No pending approvals' : `No ${statusFilter} approvals`} />
      ) : (
        <div className="max-w-[900px] mx-auto space-y-4">
          {filtered.map(approval => {
            const statusInfo = APPROVAL_STATUS[approval.status] || { label: approval.status, tone: 'neutral' };
            const isProcessing = processingId === approval.id;

            return (
              <div key={approval.id} className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-5 space-y-4">
                {/* Header row */}
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 flex-1 min-w-0 flex-wrap">
                    <Badge tone={statusInfo.tone}>{statusInfo.label}</Badge>
                    <span className="text-xs font-mono font-bold text-[color:var(--text-primary)]">{approval.action}</span>
                    {approval.resource && (
                      <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest bg-[color:var(--surface-2)] px-2 py-0.5 rounded">
                        {approval.resource}{approval.resourceId ? `/${approval.resourceId}` : ''}
                      </span>
                    )}
                  </div>
                  <span className="text-[10px] font-mono text-[color:var(--text-muted)] shrink-0">{fmtDate(approval.createdAt || approval.created_at)}</span>
                </div>

                {/* Description */}
                {approval.description && (
                  <p className="text-sm leading-relaxed text-[color:var(--text-secondary)]">{approval.description}</p>
                )}

                {/* Payload JSON preview */}
                {approval.payload && (
                  <div className="rounded-lg bg-[color:var(--surface-1)] p-3 border border-[color:var(--border-subtle)]">
                    <pre className="text-[11px] font-mono text-[color:var(--text-secondary)] whitespace-pre-wrap break-all m-0">
                      {JSON.stringify(approval.payload, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Pending actions */}
                {approval.status === 'pending' && (
                  <div className="flex items-center gap-3">
                    <button
                      className="flex-1 h-9 rounded-lg text-[10px] font-bold uppercase tracking-widest bg-emerald-500/10 text-emerald-500 hover:bg-emerald-600 hover:text-white transition-colors disabled:opacity-40"
                      disabled={isProcessing}
                      onClick={() => handleResolve(approval.id, 'approve')}
                    >
                      {isProcessing ? 'Executing...' : 'Authorize Action'}
                    </button>
                    <button
                      className="flex-1 h-9 rounded-lg text-[10px] font-bold uppercase tracking-widest border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors disabled:opacity-40"
                      disabled={isProcessing}
                      onClick={() => handleResolve(approval.id, 'reject')}
                    >
                      Decline
                    </button>
                  </div>
                )}

                {/* Resolution info */}
                {(approval.resolvedAt || approval.resolved_at) && (
                  <div className="pt-3 border-t border-[color:var(--border-subtle)] flex items-center justify-between text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">
                    <span>Resolution complete</span>
                    <span>{fmtDate(approval.resolvedAt || approval.resolved_at)} {'\u2022'} {(approval.resolvedBy || approval.resolved_by || 'SYSTEM')}</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   PermissionsSection — grouped, 3-way toggle
   ═══════════════════════════════════════════════════════════════════════════ */

function PermissionsSection() {
  const [permissions, setPermissions] = useState<{ action: string; level: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [updatingAction, setUpdatingAction] = useState('');
  const [search, setSearch] = useState('');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await araiosApi<{ permissions: { action: string; level: string }[] }>('/api/permissions');
      setPermissions(data.permissions || []);
    } catch { toast.error('Failed to load permissions'); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const LEVELS = ['allow', 'approval', 'deny'] as const;

  const handleToggle = async (action: string, newLevel: string) => {
    try {
      setUpdatingAction(action);
      await araiosApi(`/api/permissions/${action}`, { method: 'PATCH', body: { level: newLevel } });
      setPermissions(prev => prev.map(p => p.action === action ? { ...p, level: newLevel } : p));
      toast.success(`${action} \u2192 ${newLevel}`);
    } catch { toast.error('Failed to update permission'); }
    finally { setUpdatingAction(''); }
  };

  const filtered = search.trim()
    ? permissions.filter(p => p.action.toLowerCase().includes(search.toLowerCase()))
    : permissions;

  // Group by resource
  const groups: Record<string, typeof filtered> = {};
  for (const p of filtered) {
    const dot = p.action.indexOf('.');
    const resource = dot > 0 ? p.action.slice(0, dot) : p.action;
    if (!groups[resource]) groups[resource] = [];
    groups[resource].push(p);
  }

  if (loading) return <div className="flex items-center justify-center h-64"><Spinner /></div>;

  const inputCls = 'h-8 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Permission Rules</h2>
          <Badge>{permissions.length} policies</Badge>
        </div>
        <div className="relative">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[color:var(--text-muted)]" />
          <input className={`${inputCls} w-56 pl-8`} placeholder="Search permissions..." value={search} onChange={e => setSearch(e.target.value)} />
        </div>
      </div>

      <div className="max-w-[800px] mx-auto space-y-8">
        {Object.entries(groups).map(([resource, perms]) => (
          <section key={resource} className="space-y-3">
            <h3 className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)] border-l-[3px] border-[color:var(--accent-solid)] pl-3">
              {resource.toUpperCase()} PROTOCOLS
            </h3>
            <div className="rounded-xl border border-[color:var(--border-subtle)] overflow-hidden">
              {perms.map((p, i) => (
                <div key={p.action} className={`flex items-center justify-between px-4 py-3 bg-[color:var(--surface-0)] ${i > 0 ? 'border-t border-[color:var(--border-subtle)]' : ''}`}>
                  <span className="text-xs font-mono font-bold text-[color:var(--text-primary)]">{p.action}</span>
                  <div className="flex items-center bg-[color:var(--surface-2)] p-0.5 rounded-lg border border-[color:var(--border-subtle)]">
                    {LEVELS.map(lvl => {
                      const active = p.level === lvl;
                      const isUpdating = updatingAction === p.action;
                      return (
                        <button
                          key={lvl}
                          disabled={isUpdating}
                          className={`px-3 py-1 text-[10px] font-bold uppercase tracking-widest rounded-md transition-all ${
                            active
                              ? lvl === 'allow' ? 'bg-emerald-600 text-white shadow-sm'
                                : lvl === 'approval' ? 'bg-white text-black shadow-sm'
                                : 'bg-rose-600 text-white shadow-sm'
                              : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'
                          }`}
                          onClick={() => !active && handleToggle(p.action, lvl)}
                        >
                          {lvl}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   DocumentsSection — split-pane with tag filter
   ═══════════════════════════════════════════════════════════════════════════ */

function DocumentsSection() {
  const [documents, setDocuments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [activeDoc, setActiveDoc] = useState<any>(null);
  const [tagFilter, setTagFilter] = useState('');

  const loadList = useCallback(async () => {
    try {
      setLoading(true);
      const url = tagFilter ? `/api/documents?tag=${encodeURIComponent(tagFilter)}` : '/api/documents';
      const data = await araiosApi<{ documents: any[] }>(url);
      setDocuments(data.documents || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [tagFilter]);

  useEffect(() => { loadList(); }, [loadList]);

  const loadDoc = async (slug: string) => {
    try {
      const data = await araiosApi(`/api/documents/${slug}`);
      setActiveDoc(data);
      setActiveSlug(slug);
    } catch { toast.error('Failed to load document'); }
  };

  const allTags = [...new Set(documents.flatMap((d: any) => d.tags || []))].sort();

  return (
    <div className="flex flex-col h-full -m-4 md:-m-6">
      {/* Tag filter bar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-[color:var(--border-subtle)] flex-wrap">
        <button
          onClick={() => setTagFilter('')}
          className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest transition-colors flex items-center gap-1.5 ${
            !tagFilter ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]' : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
          }`}>
          All <span className="opacity-70">({documents.length})</span>
        </button>
        {allTags.map(t => (
          <button key={t} onClick={() => setTagFilter(t)}
            className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-widest transition-colors ${
              tagFilter === t ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]' : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
            }`}>{t}</button>
        ))}
      </div>

      <div className="flex flex-1 min-h-0">
        {/* Left list */}
        <div className="w-80 shrink-0 border-r border-[color:var(--border-subtle)] flex flex-col bg-[color:var(--surface-0)]">
          <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
            {loading ? (
              <div className="flex items-center justify-center h-full"><Spinner /></div>
            ) : documents.length === 0 ? (
              <EmptyState icon={FileText} label="No documents" />
            ) : (
              documents.map((doc: any) => (
                <ListCard
                  key={doc.id}
                  active={activeSlug === doc.slug}
                  onClick={() => loadDoc(doc.slug)}
                  avatarStyle={{ backgroundColor: '#eff6ff', color: '#3b82f6' }}
                  avatarContent={<FileText size={14} />}
                  title={doc.title}
                  subtitle={`/${doc.slug}`}
                  meta={`v${doc.version}`}
                  badge={<span className="text-[10px] text-[color:var(--text-muted)]">{shortDate(doc.updatedAt)}</span>}
                />
              ))
            )}
          </div>
        </div>

        {/* Right content */}
        <div className="flex-1 min-w-0 overflow-y-auto">
          {!activeDoc ? (
            <div className="flex items-center justify-center h-full text-[color:var(--text-muted)] opacity-50">
              <p className="text-xs">Select a document</p>
            </div>
          ) : (
            <div className="flex flex-col h-full">
              {/* Doc hero */}
              <div className="px-5 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-500/10 text-blue-400">
                      <FileText size={20} />
                    </div>
                    <div>
                      <h2 className="text-sm font-bold text-[color:var(--text-primary)]">{activeDoc.title}</h2>
                      <p className="text-[11px] text-[color:var(--text-muted)] font-mono">/{activeDoc.slug}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase">Last Edit</span>
                    <p className="text-xs font-bold text-[color:var(--text-primary)]">{activeDoc.lastEditedBy || '\u2014'}</p>
                  </div>
                </div>
              </div>
              {/* Doc content — simple markdown render */}
              <div className="flex-1 overflow-y-auto p-8">
                <article className="prose prose-sm max-w-none text-[color:var(--text-primary)]">
                  <SimpleMarkdown content={activeDoc.content || ''} />
                </article>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Very simple markdown-ish renderer (no external dep needed for this page) */
function SimpleMarkdown({ content }: { content: string }) {
  // Split into paragraphs, handle basic markdown
  const lines = content.split('\n');
  const elements: ReactNode[] = [];
  let key = 0;

  for (const line of lines) {
    if (line.startsWith('### ')) {
      elements.push(<h3 key={key++} className="text-base font-bold mt-4 mb-2 text-[color:var(--text-primary)]">{line.slice(4)}</h3>);
    } else if (line.startsWith('## ')) {
      elements.push(<h2 key={key++} className="text-lg font-bold mt-4 mb-2 text-[color:var(--text-primary)]">{line.slice(3)}</h2>);
    } else if (line.startsWith('# ')) {
      elements.push(<h1 key={key++} className="text-xl font-bold mt-4 mb-2 text-[color:var(--text-primary)]">{line.slice(2)}</h1>);
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      elements.push(<li key={key++} className="text-sm text-[color:var(--text-secondary)] ml-4 list-disc">{line.slice(2)}</li>);
    } else if (line.startsWith('```')) {
      // skip code fences for now
    } else if (line.trim() === '') {
      elements.push(<div key={key++} className="h-2" />);
    } else {
      elements.push(<p key={key++} className="text-sm leading-relaxed text-[color:var(--text-secondary)]">{line}</p>);
    }
  }
  return <>{elements}</>;
}

/* ═══════════════════════════════════════════════════════════════════════════
   TasksSection — full task management
   ═══════════════════════════════════════════════════════════════════════════ */

function TasksSection() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [clientFilter, setClientFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [newTask, setNewTask] = useState({
    title: '', summary: '', client: '', repo: '', source: 'manual',
    type: 'task', status: 'todo', priority: 'medium', owner: '', handoffTo: '',
  });

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await araiosApi<{ tasks: any[] }>('/api/tasks');
      const list = Array.isArray(data.tasks) ? data.tasks : [];
      setTasks(list);
      if (list.length > 0 && !selectedId) setSelectedId(list[0].id);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [selectedId]);

  useEffect(() => { load(); }, [load]);

  const clientList = useMemo(() => Array.from(new Set(tasks.map(t => t.client).filter(Boolean))).sort(), [tasks]);

  const filtered = useMemo(() =>
    tasks.filter(t => clientFilter === 'all' || t.client === clientFilter)
         .filter(t => statusFilter === 'all' || t.status === statusFilter),
    [tasks, clientFilter, statusFilter]
  );

  const statusChips = useMemo(() => {
    const dynamic = Array.from(new Set(tasks.map(t => t.status).filter(Boolean))).filter(s => !TASK_STATUS_ORDER.includes(s));
    return ['all', ...TASK_STATUS_ORDER, ...dynamic];
  }, [tasks]);

  const statusOptions = useMemo(() => Array.from(new Set([
    ...TASK_STATUS_ORDER, 'open', 'detected', 'queued', 'in_analysis', 'work_ready', 'handed_off', 'closed',
  ])), []);

  const selected = tasks.find(t => t.id === selectedId) || null;

  const patchTask = async (taskId: string, patch: Record<string, any>, message = 'Saved') => {
    try {
      await araiosApi(`/api/tasks/${taskId}`, { method: 'PATCH', body: patch });
      toast.success(message);
      load();
    } catch { toast.error('Save failure'); }
  };

  const asNullable = (value: string) => { const t = (value || '').trim(); return t || null; };
  const statusLabel = (v: string) => TASK_STATUS[v]?.label || v || 'Unknown';
  const typeLabel = (v: string) => TASK_TYPE[v]?.label || v || 'Task';
  const ownerLabel = (t: any) => t.owner || t.handoffTo || t.updatedBy || 'unassigned';

  const createTask = async () => {
    const title = (newTask.title || '').trim();
    if (!title) { toast.error('Task title is required'); return; }
    try {
      const payload = {
        title, summary: asNullable(newTask.summary), client: asNullable(newTask.client),
        repo: asNullable(newTask.repo), source: asNullable(newTask.source), type: asNullable(newTask.type),
        status: newTask.status || 'todo', priority: newTask.priority || 'medium',
        owner: asNullable(newTask.owner), handoffTo: asNullable(newTask.handoffTo),
      };
      const created = await araiosApi('/api/tasks', { method: 'POST', body: payload });
      toast.success('Task created');
      setCreating(false);
      setNewTask({ title: '', summary: '', client: '', repo: '', source: 'manual', type: 'task', status: 'todo', priority: 'medium', owner: '', handoffTo: '' });
      setSelectedId(created?.id || null);
      load();
    } catch { toast.error('Failed to create task'); }
  };

  const deleteTask = async (id: string) => {
    try {
      await araiosApi(`/api/tasks/${id}`, { method: 'DELETE' });
      toast.success('Task deleted');
      setSelectedId(null);
      setConfirmDeleteId(null);
      load();
    } catch { toast.error('Failed to delete task'); }
  };

  const inputCls = 'w-full h-9 rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors';
  const labelCls = 'text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]';

  return (
    <div className="flex flex-col h-full -m-4 md:-m-6">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[color:var(--border-subtle)]">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-bold text-[color:var(--text-primary)]">Tasks</h2>
          <span className="text-[10px] text-[color:var(--text-muted)]">Workflow tasks with optional GitHub metadata</span>
        </div>
        <button
          className="h-8 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors"
          onClick={() => setCreating(v => !v)}
        >
          {creating ? 'Cancel' : 'New Task'}
        </button>
      </div>

      {/* Create form */}
      {creating && (
        <div className="px-4 py-3 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1"><label className={labelCls}>Title</label><input className={inputCls} value={newTask.title} onChange={e => setNewTask(p => ({ ...p, title: e.target.value }))} placeholder="Task title" /></div>
            <div className="space-y-1"><label className={labelCls}>Type</label>
              <select className={inputCls} value={newTask.type} onChange={e => setNewTask(p => ({ ...p, type: e.target.value }))}>
                {Object.keys(TASK_TYPE).map(v => <option key={v} value={v}>{TASK_TYPE[v].label}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1"><label className={labelCls}>Status</label>
              <select className={inputCls} value={newTask.status} onChange={e => setNewTask(p => ({ ...p, status: e.target.value }))}>
                {TASK_STATUS_ORDER.map(v => <option key={v} value={v}>{statusLabel(v)}</option>)}
              </select>
            </div>
            <div className="space-y-1"><label className={labelCls}>Priority</label>
              <select className={inputCls} value={newTask.priority} onChange={e => setNewTask(p => ({ ...p, priority: e.target.value }))}>
                {['low', 'medium', 'high', 'critical'].map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>
            <div className="space-y-1"><label className={labelCls}>Source</label><input className={inputCls} value={newTask.source} onChange={e => setNewTask(p => ({ ...p, source: e.target.value }))} /></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1"><label className={labelCls}>Client</label><input className={inputCls} value={newTask.client} onChange={e => setNewTask(p => ({ ...p, client: e.target.value }))} /></div>
            <div className="space-y-1"><label className={labelCls}>Repository</label><input className={inputCls} value={newTask.repo} onChange={e => setNewTask(p => ({ ...p, repo: e.target.value }))} placeholder="optional" /></div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1"><label className={labelCls}>Owner</label><input className={inputCls} value={newTask.owner} onChange={e => setNewTask(p => ({ ...p, owner: e.target.value }))} /></div>
            <div className="space-y-1"><label className={labelCls}>Handoff To</label><input className={inputCls} value={newTask.handoffTo} onChange={e => setNewTask(p => ({ ...p, handoffTo: e.target.value }))} /></div>
          </div>
          <div className="space-y-1">
            <label className={labelCls}>Summary</label>
            <textarea className={`${inputCls} min-h-[100px] py-2 resize-y`} value={newTask.summary} onChange={e => setNewTask(p => ({ ...p, summary: e.target.value }))} />
          </div>
          <div className="flex items-center gap-2">
            <button className="h-8 px-4 rounded-lg text-xs font-bold bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity" onClick={createTask}>Create Task</button>
            <button className="h-8 px-4 rounded-lg text-xs font-bold border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-2)] transition-colors" onClick={() => setCreating(false)}>Close</button>
          </div>
        </div>
      )}

      {/* Status filter bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[color:var(--border-subtle)] gap-2 overflow-x-auto">
        <div className="flex items-center gap-1 flex-wrap">
          {statusChips.map(s => (
            <button key={s} onClick={() => setStatusFilter(s)}
              className={`px-2 py-1 rounded-full text-[9px] font-bold uppercase tracking-widest transition-colors whitespace-nowrap ${
                statusFilter === s
                  ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                  : 'text-[color:var(--text-muted)] hover:bg-[color:var(--surface-2)]'
              }`}>
              {s === 'all' ? 'all' : statusLabel(s)}
              <span className="ml-1 opacity-70">
                {s === 'all' ? tasks.length : tasks.filter(t => t.status === s).length}
              </span>
            </button>
          ))}
        </div>
        <select className="h-7 rounded-lg bg-transparent border border-[color:var(--border-subtle)] text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] px-2 focus:outline-none" value={clientFilter} onChange={e => setClientFilter(e.target.value)}>
          <option value="all">All Clients</option>
          {clientList.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>

      {/* Split pane */}
      <div className="flex flex-1 min-h-0">
        {/* Left list */}
        <div className="w-80 shrink-0 border-r border-[color:var(--border-subtle)] flex flex-col bg-[color:var(--surface-0)]">
          <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
            {loading ? <div className="flex items-center justify-center h-full"><Spinner /></div> : (
              filtered.length === 0 ? <EmptyState icon={GitBranch} label="No tasks" /> :
              filtered.map(task => {
                const colors = STATUS_COLORS[task.status] || STATUS_COLORS.detected;
                return (
                  <ListCard
                    key={task.id}
                    active={selectedId === task.id}
                    onClick={() => setSelectedId(task.id)}
                    avatarStyle={{ backgroundColor: colors.bg, color: colors.icon }}
                    avatarContent={<GitBranch size={14} />}
                    title={task.title || `Task ${task.id}`}
                    subtitle={`${task.client || task.repo || 'general'} \u00B7 ${ownerLabel(task)}`}
                    meta={shortDate(task.updatedAt || task.detectedAt)}
                    badge={<Badge>{statusLabel(task.status)}</Badge>}
                  />
                );
              })
            )}
          </div>
        </div>

        {/* Right detail */}
        <div className="flex-1 min-w-0 overflow-y-auto">
          {!selected ? (
            <div className="flex items-center justify-center h-full text-[color:var(--text-muted)] opacity-50 text-xs">Select a task</div>
          ) : (
            <div className="flex flex-col h-full">
              {/* Task hero */}
              <div className="px-5 py-4 border-b border-[color:var(--border-subtle)] bg-[color:var(--surface-1)]">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg" style={{ backgroundColor: (STATUS_COLORS[selected.status] || STATUS_COLORS.detected).bg, color: (STATUS_COLORS[selected.status] || STATUS_COLORS.detected).icon }}>
                    <GitBranch size={18} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <input
                      key={`title-${selected.id}`}
                      className="w-full bg-transparent text-sm font-bold text-[color:var(--text-primary)] focus:outline-none border-b border-transparent focus:border-[color:var(--accent-solid)] transition-colors pb-0.5"
                      defaultValue={selected.title || `Task ${selected.id}`}
                      onBlur={e => patchTask(selected.id, { title: asNullable(e.target.value) }, 'Title updated')}
                    />
                    <p className="text-[10px] text-[color:var(--text-muted)] mt-0.5">
                      {selected.client || selected.repo || 'general'} &middot; owner: {ownerLabel(selected)} &middot; updated by: {selected.updatedBy || 'unknown'}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <Badge tone="info">{typeLabel(selected.type)}</Badge>
                    <Badge tone="success">{statusLabel(selected.status)}</Badge>
                    <Badge>{(selected.priority || 'medium')}</Badge>
                  </div>
                </div>
              </div>

              {/* Detail content */}
              <div className="flex-1 overflow-y-auto p-5 space-y-5">
                {/* Quick controls */}
                <section className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 space-y-3">
                  <label className={labelCls}>Quick Controls</label>
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    <div className="space-y-1">
                      <label className={labelCls}>Status</label>
                      <select key={`status-${selected.id}`} className={inputCls} defaultValue={selected.status || 'todo'} onChange={e => patchTask(selected.id, { status: e.target.value }, 'Status updated')}>
                        {statusOptions.map(v => <option key={v} value={v}>{statusLabel(v)}</option>)}
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className={labelCls}>Priority</label>
                      <select key={`priority-${selected.id}`} className={inputCls} defaultValue={selected.priority || 'medium'} onChange={e => patchTask(selected.id, { priority: e.target.value }, 'Priority updated')}>
                        {['low', 'medium', 'high', 'critical'].map(v => <option key={v} value={v}>{v}</option>)}
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className={labelCls}>Type</label>
                      <select key={`type-${selected.id}`} className={inputCls} defaultValue={selected.type || ''} onChange={e => patchTask(selected.id, { type: asNullable(e.target.value) }, 'Type updated')}>
                        <option value="">Unset</option>
                        {Object.keys(TASK_TYPE).map(v => <option key={v} value={v}>{TASK_TYPE[v].label}</option>)}
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className={labelCls}>Owner</label>
                      <input key={`owner-${selected.id}`} className={inputCls} defaultValue={selected.owner || ''} onBlur={e => patchTask(selected.id, { owner: asNullable(e.target.value) }, 'Owner updated')} />
                    </div>
                    <div className="space-y-1">
                      <label className={labelCls}>Handoff To</label>
                      <input key={`handoff-${selected.id}`} className={inputCls} defaultValue={selected.handoffTo || ''} onBlur={e => patchTask(selected.id, { handoffTo: asNullable(e.target.value) }, 'Handoff updated')} />
                    </div>
                  </div>
                </section>

                {/* Summary + Notes */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <section className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 space-y-2">
                    <label className={labelCls}>Task Summary</label>
                    <textarea key={`summary-${selected.id}`} className={`${inputCls} min-h-[140px] py-2 resize-y`} defaultValue={selected.summary || ''} onBlur={e => patchTask(selected.id, { summary: asNullable(e.target.value) }, 'Summary updated')} />
                  </section>
                  <section className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 space-y-2">
                    <label className={labelCls}>Handover Notes</label>
                    <textarea key={`notes-${selected.id}`} className={`${inputCls} min-h-[140px] py-2 resize-y`} defaultValue={selected.notes || ''} onBlur={e => patchTask(selected.id, { notes: asNullable(e.target.value) }, 'Notes saved')} />
                  </section>
                </div>

                {/* Extended metadata */}
                <details className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] overflow-hidden">
                  <summary className="px-4 py-3 cursor-pointer text-xs font-bold text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] transition-colors">
                    Extended Metadata
                  </summary>
                  <div className="px-4 pb-4 pt-2 grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="space-y-1"><label className={labelCls}>Source</label><input key={`src-${selected.id}`} className={inputCls} defaultValue={selected.source || ''} onBlur={e => patchTask(selected.id, { source: asNullable(e.target.value) }, 'Source updated')} /></div>
                    <div className="space-y-1"><label className={labelCls}>Client</label><input key={`cli-${selected.id}`} className={inputCls} defaultValue={selected.client || ''} onBlur={e => patchTask(selected.id, { client: asNullable(e.target.value) }, 'Client updated')} /></div>
                    <div className="space-y-1"><label className={labelCls}>Repository</label><input key={`repo-${selected.id}`} className={inputCls} defaultValue={selected.repo || ''} onBlur={e => patchTask(selected.id, { repo: asNullable(e.target.value) }, 'Repo updated')} /></div>
                  </div>
                </details>

                {/* Work package */}
                <details className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] overflow-hidden">
                  <summary className="px-4 py-3 cursor-pointer text-xs font-bold text-[color:var(--text-secondary)] hover:bg-[color:var(--surface-1)] transition-colors">
                    Work Package Fields (Optional)
                  </summary>
                  <div className="px-4 pb-4 pt-2 grid grid-cols-1 md:grid-cols-2 gap-4">
                    {WORK_PACKAGE_FIELDS.map(field => (
                      <div key={field.key} className="rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <label className={labelCls}>{field.label}</label>
                          <button
                            className="flex items-center gap-1 text-[10px] text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors"
                            onClick={() => { navigator.clipboard.writeText(selected.workPackage?.[field.key] || ''); toast.success('Copied'); }}
                          >
                            <Copy size={10} /> Copy
                          </button>
                        </div>
                        <textarea
                          key={`wp-${field.key}-${selected.id}`}
                          className={`${inputCls} min-h-[60px] py-2 resize-y font-mono text-[11px]`}
                          defaultValue={selected.workPackage?.[field.key] || ''}
                          onBlur={e => patchTask(selected.id, { workPackage: { [field.key]: e.target.value } }, `${field.label} updated`)}
                        />
                      </div>
                    ))}
                  </div>
                </details>

                {/* Delete */}
                <div className="flex justify-end pt-2">
                  <button className="h-8 px-4 rounded-lg text-[10px] font-bold uppercase tracking-widest bg-rose-600/10 text-rose-500 hover:bg-rose-600 hover:text-white transition-colors flex items-center gap-1.5" onClick={() => setConfirmDeleteId(selected.id)}>
                    <Trash2 size={12} /> Delete Task
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {confirmDeleteId && (
        <ConfirmDialog
          title="Purge Task"
          message="This action is irreversible. Permanently remove this task?"
          onConfirm={() => deleteTask(confirmDeleteId)}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   CoordinationSection — chat-style message list
   ═══════════════════════════════════════════════════════════════════════════ */

function CoordinationSection() {
  const [messages, setMessages] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const data = await araiosApi<{ messages: any[] }>('/api/coordination?limit=200');
      setMessages((data.messages || []).reverse());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const timer = setInterval(() => load(true), 5000);
    return () => clearInterval(timer);
  }, [load]);

  const agentColor = (agent: string) => AGENT_COLORS[agent] || '#6882a4';
  const agentDisplay = (msg: any) => {
    const label = msg?.context?.agent_label;
    if (typeof label === 'string' && label.trim()) return label.trim();
    return msg.agent;
  };

  const sendMessage = async () => {
    const message = draft.trim();
    if (!message || sending) return;
    try {
      setSending(true);
      const created = await araiosApi('/api/coordination', { method: 'POST', body: { message, context: { source: 'human_ui' } } });
      setMessages(prev => [created, ...prev]);
      setDraft('');
      toast.success('Message sent');
    } catch { toast.error('Could not send message'); }
    finally { setSending(false); }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const inputCls = 'w-full rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] px-3 py-2 text-xs text-[color:var(--text-primary)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:border-[color:var(--accent-solid)] transition-colors resize-y';

  return (
    <div className="flex flex-col h-full -m-4 md:-m-6">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[color:var(--border-subtle)]">
        <div className="flex items-center gap-3">
          <Badge>{messages.length} messages</Badge>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase tracking-widest">Live</span>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-6" ref={scrollRef}>
        {loading && messages.length === 0 ? (
          <div className="flex items-center justify-center h-full"><Spinner /></div>
        ) : messages.length === 0 ? (
          <EmptyState icon={MessageCircle} label="No coordination messages" />
        ) : (
          <div className="max-w-[800px] mx-auto space-y-3">
            {messages.map(msg => (
              <div key={msg.id} className="rounded-xl border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] p-4 flex gap-4">
                <div className="w-2 h-2 rounded-full shrink-0 mt-1.5" style={{ backgroundColor: agentColor(msg.agent), boxShadow: `0 0 8px ${agentColor(msg.agent)}` }} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[11px] font-extrabold uppercase tracking-wide" style={{ color: agentColor(msg.agent) }}>
                      {agentDisplay(msg)}
                    </span>
                    <span className="text-[10px] text-[color:var(--text-muted)] font-mono">
                      {shortDate(msg.createdAt)}
                    </span>
                  </div>
                  {agentDisplay(msg) !== msg.agent && (
                    <div className="text-[10px] text-[color:var(--text-muted)] font-mono mb-2">id: {msg.agent}</div>
                  )}
                  <div className="text-sm leading-relaxed text-[color:var(--text-primary)] whitespace-pre-wrap break-words">
                    {msg.message}
                  </div>
                  {msg.context && (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-[10px] font-bold uppercase text-[color:var(--text-muted)]">
                        Debug Context
                      </summary>
                      <pre className="mt-2 rounded-lg bg-[color:var(--surface-1)] p-3 text-[11px] font-mono text-[color:var(--text-secondary)] overflow-x-auto border border-[color:var(--border-subtle)]">
                        {JSON.stringify(msg.context, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Compose */}
      <div className="border-t border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-4 py-3">
        <div className="max-w-[800px] mx-auto">
          <label className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)] mb-1.5 block">Send Supervision Message</label>
          <div className="flex items-end gap-2">
            <textarea
              className={`${inputCls} min-h-[60px]`}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              placeholder="Write a message (Enter to send, Shift+Enter for newline)"
            />
            <button
              className="h-10 px-4 rounded-lg text-xs font-bold bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] hover:opacity-90 transition-opacity shrink-0 flex items-center gap-1.5 disabled:opacity-40"
              onClick={sendMessage}
              disabled={sending || !draft.trim()}
            >
              <Send size={14} />
              {sending ? 'Sending...' : 'Send'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   AraiOSPage — main exported page component
   ═══════════════════════════════════════════════════════════════════════════ */

type AraiOSSection = 'modules' | 'approvals' | 'permissions' | 'documents' | 'tasks' | 'coordination';

const SECTION_LABELS: Record<AraiOSSection, string> = {
  modules: 'Modules',
  approvals: 'Approvals',
  permissions: 'Permissions',
  documents: 'Documents',
  tasks: 'Tasks',
  coordination: 'Coordination',
};

export function AraiOSPage() {
  const { section } = useParams<{ section?: string }>();
  const activeSection = (section || 'modules') as AraiOSSection;

  return (
    <AppShell title={SECTION_LABELS[activeSection] || 'araiOS'} subtitle="Module Engine" contentClassName="!p-0 overflow-hidden">
      <div className="h-full overflow-hidden">
        {activeSection === 'modules' && <ModulesSection />}
        {activeSection === 'approvals' && <ApprovalsSection />}
        {activeSection === 'permissions' && <PermissionsSection />}
        {activeSection === 'documents' && <DocumentsSection />}
        {activeSection === 'tasks' && <TasksSection />}
        {activeSection === 'coordination' && <CoordinationSection />}
      </div>
    </AppShell>
  );
}
