import { X, RefreshCw, Loader2, Plus, Check } from 'lucide-react';

export interface LocalRuntimeFormProps {
  /** 'create' generates a key and registers a runtime; 'edit' updates an existing one. */
  mode?: 'create' | 'edit';
  /** Create-mode gating: when false, show the guide + Recheck instead of the fields. */
  available?: boolean;
  detail?: string | null;
  name: string;
  onNameChange: (value: string) => void;
  workspacesDir: string;
  onWorkspacesDirChange: (value: string) => void;
  isBusy: boolean;
  jobMessage?: string | null;
  inputClass: string;
  cancelLabel: string;
  onCancel: () => void;
  onRecheck?: () => void;
  onSubmit: () => void;
  /** card = sits on surface-0 (Settings); panel = sits on surface-1 (Onboarding). */
  surface?: 'card' | 'panel';
}

/**
 * The "Local (this Mac)" runtime form, shared by Settings and Onboarding. Create
 * mode shows the availability guide when unavailable, else a Name + Workspace
 * form; edit mode updates an existing runtime. Behaviour is passed in via props.
 */
export function LocalRuntimeForm({
  mode = 'create',
  available = true,
  detail,
  name,
  onNameChange,
  workspacesDir,
  onWorkspacesDirChange,
  isBusy,
  jobMessage,
  inputClass,
  cancelLabel,
  onCancel,
  onRecheck,
  onSubmit,
  surface = 'card',
}: LocalRuntimeFormProps) {
  const isEdit = mode === 'edit';
  const containerBg = surface === 'panel' ? 'bg-[color:var(--surface-1)]' : 'bg-[color:var(--surface-0)]';
  const progressBg = surface === 'panel' ? 'bg-[color:var(--surface-0)]/60' : 'bg-[color:var(--surface-1)]/40';
  const showGuide = !isEdit && !available;
  // In edit mode the workspace folder must stay set; in create it may be blank (backend defaults it).
  const submitDisabled = isBusy || !name.trim() || (isEdit && !workspacesDir.trim());

  return (
    <div className={`rounded-xl border border-[color:var(--accent-solid)]/40 ${containerBg} p-4 space-y-4 animate-in fade-in slide-in-from-top-1 duration-200`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--accent-solid)]">
          {isEdit ? 'Edit Local runtime' : 'New Local runtime'}
        </span>
        <button type="button" onClick={onCancel} disabled={isBusy} className="p-1 rounded-md text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors disabled:opacity-40" title="Cancel">
          <X size={14} />
        </button>
      </div>

      {showGuide ? (
        <div className="space-y-3">
          <div className="text-[11px] text-amber-400 leading-relaxed">
            {detail ?? 'This Mac is not ready to host a local runtime yet.'}
          </div>
          <div className="flex items-center gap-2 pt-2 border-t border-[color:var(--border-subtle)]">
            <button type="button" onClick={onCancel} className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest">{cancelLabel}</button>
            <div className="flex-1" />
            {onRecheck && (
              <button type="button" onClick={onRecheck} className="btn-secondary h-10 px-3 gap-2 text-[10px] font-bold uppercase tracking-widest">
                <RefreshCw size={14} /> Recheck
              </button>
            )}
          </div>
        </div>
      ) : (
        <>
          <p className="text-[11px] text-[color:var(--text-muted)] leading-relaxed">
            {isEdit
              ? 'The agent connects to this Mac at 127.0.0.1. Change where session workspaces live below.'
              : 'Sentinel generates an SSH key, authorizes it for your macOS user (loopback-only), verifies it can log in, and connects to this machine at 127.0.0.1.'}
          </p>

          <label className="block space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Name</span>
            <input
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              disabled={isBusy}
              className={`${inputClass} w-full disabled:opacity-50`}
            />
          </label>

          <label className="block space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">Workspace folder</span>
            <input
              value={workspacesDir}
              onChange={(e) => onWorkspacesDirChange(e.target.value)}
              disabled={isBusy}
              placeholder="~/sentinel/workspaces"
              className={`${inputClass} w-full font-mono disabled:opacity-50`}
            />
            <span className="block text-[9px] text-[color:var(--text-muted)] leading-relaxed">
              Absolute path where session workspaces are created.{isEdit ? '' : ' Leave blank for the default.'}
            </span>
          </label>

          {isBusy && (
            <div className={`rounded-md border-l-2 border-amber-500/50 ${progressBg} pl-2 pr-1.5 py-1.5`}>
              <div className="flex items-center gap-2">
                <Loader2 size={12} className="animate-spin text-amber-400 shrink-0" />
                <span className="text-[10px] text-[color:var(--text-secondary)] leading-snug">{jobMessage ?? 'Working…'}</span>
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 pt-2 border-t border-[color:var(--border-subtle)]">
            <button type="button" onClick={onCancel} disabled={isBusy} className="btn-secondary h-10 px-3 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40">{cancelLabel}</button>
            <div className="flex-1" />
            <button
              type="button"
              onClick={onSubmit}
              disabled={submitDisabled}
              className="btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isBusy ? <Loader2 size={14} className="animate-spin" /> : isEdit ? <Check size={14} /> : <Plus size={14} />}
              {isEdit ? 'Save changes' : 'Create runtime'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
