import { useEffect, useState } from 'react';
import { ServiceLogs } from './ServiceLogs';
import type { LogEntry, PayloadProgress } from '../../../../desktop/sentinel/src/shared/ipc';
import { ChevronDown, ChevronUp, Loader2, RefreshCw } from 'lucide-react';
import { Logo } from './ui/Logo';
import './workspace-preparation.css';

export function WorkspacePreparation({ preparing = false, progress, error, onRetry }: {
  preparing?: boolean; progress?: PayloadProgress; error?: string; onRetry?: () => void;
}) {
  const [logs, setLogs] = useState<LogEntry[]>();
  const api = window.sentinelDesktop;
  const [devMode, setDevMode] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  useEffect(() => {
    if (!api) return;
    let active = true;
    let changed = false;
    const off = api.onDevModeChanged(value => { changed = true; setDevMode(value); });
    void api.getDevMode().then(value => { if (active && !changed) setDevMode(value); }).catch(() => {});
    return () => { active = false; off(); };
  }, [api]);
  useEffect(() => { if (!devMode) { setDetailsOpen(false); setLogs(undefined); } }, [devMode]);
  return <div className="desktop-frame workspace-preparation-frame">
    <div className="desktop-titlebar" />
    <main className="workspace-preparation" aria-busy={!error}>
      <section className="workspace-preparation-content">
        <div className="workspace-preparation-logo"><Logo size={64} /></div>
        <p className="workspace-preparation-brand">Sentinel</p>
        <h1>{error ? 'Unable to start Sentinel' : preparing ? 'Preparing Sentinel' : 'Starting Sentinel'}</h1>
        <p className="workspace-preparation-description">Your conversations, ideas, and projects.</p>
        <div className="workspace-preparation-progress" role="status" aria-live="polite">
          {!error && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
          <span className="workspace-preparation-step" key={error || progress?.phase || 'starting'} role={error ? 'alert' : undefined}>{error || progress?.message || (preparing ? 'Getting your app ready…' : 'Opening your app…')}</span>
        </div>
        {!error && progress?.fractionComplete !== undefined && <progress className="workspace-preparation-bar" aria-label="Download progress" max={1} value={progress.fractionComplete} />}
        {error && <button className="btn-primary workspace-preparation-action" onClick={onRetry}><RefreshCw size={14} aria-hidden="true" />Retry</button>}
        {devMode && <button className="btn-secondary workspace-preparation-action" aria-expanded={detailsOpen} aria-controls="preparation-diagnostics" onClick={() => {
          setDetailsOpen(value => !value);
          if (!detailsOpen) void api?.getLogs().then(setLogs).catch(() => setLogs([]));
        }}>{detailsOpen ? <ChevronUp size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}{detailsOpen ? 'Hide details' : 'Show details'}</button>}
      </section>
      {devMode && <div id="preparation-diagnostics" className={`workspace-preparation-details${detailsOpen ? ' is-open' : ''}`} inert={!detailsOpen} aria-hidden={!detailsOpen}>
        <div>{logs && <ServiceLogs entries={logs} openFolder={() => { void api?.openLogFolder(); }} />}</div>
      </div>}
    </main>
  </div>;
}
