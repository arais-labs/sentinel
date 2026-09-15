import { Loader2 } from 'lucide-react';
import { Logo } from './ui/Logo';
import './workspace-preparation.css';

export function WorkspacePreparation() {
  return <div className="desktop-frame workspace-preparation-frame">
    <div className="desktop-titlebar" />
    <main className="workspace-preparation" aria-busy="true">
      <section className="workspace-preparation-content">
        <div className="workspace-preparation-logo"><Logo size={64} /></div>
        <p className="workspace-preparation-brand">Sentinel</p>
        <h1>Starting Sentinel</h1>
        <p className="workspace-preparation-description">Your conversations, ideas, and projects.</p>
        <div className="workspace-preparation-progress" role="status" aria-live="polite">
          <Loader2 size={16} className="animate-spin" aria-hidden="true" />
          <span>Opening your app…</span>
        </div>
      </section>
    </main>
  </div>;
}
