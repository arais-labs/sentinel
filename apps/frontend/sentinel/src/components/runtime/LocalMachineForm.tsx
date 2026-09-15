import { X, RefreshCw, Loader2, Plus, Check } from 'lucide-react';

export interface LocalMachineFormProps {
  /** 'create' registers a new runtime; 'edit' updates an existing one. */
  mode?: 'create' | 'edit';
  /** Create-mode gating: when false, show the guide + Recheck instead of the fields. */
  available?: boolean;
  detail?: string | null;
  name: string;
  onNameChange: (value: string) => void;
  isBusy: boolean;
  jobMessage?: string | null;
  inputClass: string;
  cancelLabel: string;
  onCancel: () => void;
  onRecheck?: () => void;
  onSubmit: () => void;
  /** card = sits on surface-0 (Workspaces); panel = sits on surface-1 (Onboarding). */
  surface?: 'card' | 'panel';
}

/**
 * The "Local (this Mac)" runtime form, shared by Workspaces and Onboarding. Create
 * mode shows the availability guide when unavailable, else a Name + Workspace
 * form; edit mode updates an existing runtime. Behaviour is passed in via props.
 */
export function LocalMachineForm({
  mode = 'create',
  available = true,
  detail,
  name,
  onNameChange,
  isBusy,
  jobMessage,
  inputClass,
  cancelLabel,
  onCancel,
  onRecheck,
  onSubmit,
  surface = 'card',
}: LocalMachineFormProps) {
  const isEdit = mode === 'edit';
  const containerBg = surface === 'panel' ? 'bg-(--surface-1)' : 'bg-(--surface-0)';
  const progressBg = surface === 'panel' ? 'bg-(--surface-0)/60' : 'bg-(--surface-1)/40';
  const showGuide = !isEdit && !available;
  const submitDisabled = isBusy || !name.trim();

  return (
    <div className={`rounded-xl border border-(--accent-solid)/40 ${containerBg} p-4 space-y-4 animate-in fade-in slide-in-from-top-1 duration-200`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-bold uppercase tracking-widest text-(--accent-solid)">
          {isEdit ? 'Edit local machine' : 'New local machine'}
        </span>
        <button type="button" onClick={onCancel} disabled={isBusy} className="p-1 rounded-md text-(--text-muted) hover:text-(--text-primary) transition-colors disabled:opacity-40" title="Cancel">
          <X size={14} />
        </button>
      </div>

      {showGuide ? (
        <div className="space-y-3">
          <div className="text-[11px] text-amber-400 leading-relaxed">
            {detail ?? 'This Mac is not ready to host a local machine yet.'}
          </div>
          <div className="flex items-center gap-2 pt-2 border-t border-(--border-subtle)">
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
          <p className="text-[11px] text-(--text-muted) leading-relaxed">
            {isEdit
              ? 'The agent runs directly on this Mac. Sentinel manages its supporting files automatically.'
              : 'The agent runs directly on this Mac as you — no SSH or Remote Login — sandboxed to each attached workspace.'}
          </p>

          <label className="block space-y-1">
            <span className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">Name</span>
            <input
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              disabled={isBusy}
              className={`${inputClass} w-full disabled:opacity-50`}
            />
          </label>



          {isBusy && (
            <div className={`rounded-md border-l-2 border-amber-500/50 ${progressBg} pl-2 pr-1.5 py-1.5`}>
              <div className="flex items-center gap-2">
                <Loader2 size={12} className="animate-spin text-amber-400 shrink-0" />
                <span className="text-[10px] text-(--text-secondary) leading-snug">{jobMessage ?? 'Working…'}</span>
              </div>
            </div>
          )}

          <div className="flex items-center gap-2 pt-2 border-t border-(--border-subtle)">
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
