import type { PropsWithChildren, HTMLAttributes } from 'react';

interface PanelProps extends PropsWithChildren, HTMLAttributes<HTMLDivElement> {}

export function Panel({ className = '', children, ...rest }: PanelProps) {
  return (
    <div
      className={`rounded-lg border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
