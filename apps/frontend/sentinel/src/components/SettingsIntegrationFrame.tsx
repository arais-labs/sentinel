import type { PropsWithChildren, ReactNode } from 'react';

export function SettingsIntegrationFrame({ title, actions, contentClassName, children }: PropsWithChildren<{ title: string; actions?: ReactNode; contentClassName?: string }>) {
  return <section className={`settings-integration ${contentClassName ?? ''}`}>
    <header className="settings-section-heading flex flex-wrap items-center justify-between gap-3 mb-5"><h2 className="text-sm font-semibold">{title}</h2>{actions}</header>
    {children}
  </section>;
}
