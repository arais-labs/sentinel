interface StatusChipProps {
  label: string;
  tone?: 'default' | 'good' | 'warn' | 'danger' | 'info';
  className?: string;
}

const toneStyles: Record<NonNullable<StatusChipProps['tone']>, string> = {
  default: 'bg-[color:var(--surface-2)] text-[color:var(--text-secondary)]',
  good: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  warn: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  danger: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
  info: 'bg-sky-500/10 text-sky-600 dark:text-sky-400',
};

export function StatusChip({ label, tone = 'default', className = '' }: StatusChipProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${toneStyles[tone]} ${className}`}
    >
      {label}
    </span>
  );
}
