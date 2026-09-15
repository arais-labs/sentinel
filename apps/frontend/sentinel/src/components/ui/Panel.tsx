import type { PropsWithChildren, HTMLAttributes } from 'react';

interface PanelProps extends PropsWithChildren, HTMLAttributes<HTMLDivElement> {}

export function Panel({ className = '', children, ...rest }: PanelProps) {
  return (
    <div
      className={`rounded-lg border border-(--border-subtle) bg-(--surface-0) ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
