import { clsx } from 'clsx';

interface ToggleProps {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  className?: string;
  label?: string;
}

export function Toggle({ enabled, onChange, className = '', label }: ToggleProps) {
  return (
    <div className={clsx("flex items-center gap-3", className)}>
      {label && <span className="text-[10px] font-bold uppercase tracking-widest text-[color:var(--text-muted)]">{label}</span>}
      <button
        type="button"
        onClick={() => onChange(!enabled)}
        className={clsx(
          "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none",
          enabled ? "bg-emerald-500" : "bg-[color:var(--surface-3)]"
        )}
      >
        <span
          aria-hidden="true"
          className={clsx(
            "pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out",
            enabled ? "translate-x-4" : "translate-x-0"
          )}
        />
      </button>
    </div>
  );
}
