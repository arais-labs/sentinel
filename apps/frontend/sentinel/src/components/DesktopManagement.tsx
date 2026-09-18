import { useEffect, useState } from 'react';
import { Download, Loader2, RefreshCw, Server, Power } from 'lucide-react';
import { ServiceLogs } from './ServiceLogs';
import { Panel } from './ui/Panel';
import { StatusChip } from './ui/StatusChip';
import type { DesktopStatus, LogEntry, PayloadProgress, PayloadUpdate, ReleaseChannel } from '../../../../desktop/sentinel/src/shared/ipc';
const labels: Record<string,string> = { backend:'Sentinel service', frontend:'Interface', manager:'App' };
function compareVersions(a: string, b: string): number {
  const parse = (value: string) => value.replace(/^v/, '').split('.').map(part => Number.parseInt(part, 10) || 0);
  const [x, y] = [parse(a), parse(b)];
  for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
    const diff = (x[i] ?? 0) - (y[i] ?? 0);
    if (diff !== 0) return diff < 0 ? -1 : 1;
  }
  return 0;
}

export function DesktopManagement({ sectionId = 'services' }: { sectionId?: 'services' | 'updates' }) {
  const api = window.sentinelDesktop;
  const [status, setStatus] = useState<DesktopStatus>();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<PayloadProgress>();
  const [update, setUpdate] = useState<PayloadUpdate | null>();
  const [channel, setChannel] = useState<ReleaseChannel>('stable');
  const [devMode, setDevMode] = useState(false);
  useEffect(() => {
    if (!api) return;
    let active = true;
    const fail = (error: unknown) => { if (active) setError(String(error)); };
    const off = [api.onStatus(setStatus), api.onDevModeChanged(setDevMode),
      api.onLog(entry => setLogs(current => [...current, entry].slice(-500))),
      api.onPayloadProgress(setProgress),
      api.onPayloadFailed(failure => setError(failure.reason))];
    void api.getStatus().then(value => { if (active) setStatus(value); }).catch(fail);
    void api.getLogs().then(value => { if (active) setLogs(value.slice(-500)); }).catch(fail);
    void api.getDevMode().then(value => { if (active) setDevMode(value); }).catch(fail);
    return () => { active = false; off.forEach(fn => fn()); };
  }, [api]);
  if (!api) return <p>Local services are managed in the desktop app.</p>;
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); setStatus(await api!.getStatus()); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const starting = !status || Boolean(status.operation) || status.services.some(s => s.state === 'starting' || s.state === 'stopping');
  const button = 'inline-flex items-center justify-center gap-2 h-10 px-4 rounded-lg border border-(--border-subtle) bg-(--surface-0) text-[10px] font-bold uppercase tracking-widest text-(--text-primary) hover:border-(--border-strong) hover:bg-(--surface-1) transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const primaryButton = 'btn-primary h-10 px-4 gap-2 text-[10px] font-bold uppercase tracking-widest shrink-0';
  const dangerButton = 'inline-flex items-center justify-center gap-2 h-10 px-3 rounded-lg text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const section = 'desktop-settings-panel';
  // Shells older than the gate never see minShellVersion, so their next update is always the DMG.
  const legacyShell = Boolean(api) && typeof (api as { openPaneWindow?: unknown }).openPaneWindow !== 'function';
  // A pre-gate app takes anything newer as a download; an older release on another channel is a plain install.
  const newerThanInstalled = update && (!status?.payload.version || compareVersions(update.version, status.payload.version) > 0);
  const shellUpdate = update?.shellUpdate ?? status?.shellUpdate
    ?? (legacyShell && update && newerThanInstalled ? { version: update.version, minShellVersion: '2.3.0', url: `https://github.com/arais-labs/sentinel/releases/tag/latest-${update.channel}` } : null);
  return <div className="settings-desktop-management">
    {(error || status?.error) && <p role="alert" className="text-rose-400">{error || status?.error}</p>}
      {sectionId === 'services' && <section className={section}>
        <Panel className="service-summary w-full p-6 space-y-6">
          <div className="flex items-center gap-3 border-b border-(--border-subtle) pb-4">
            <div className="p-2 rounded-lg bg-(--surface-2) text-(--accent-solid)"><Server size={20} /></div>
            <div className="flex-1"><h2 className="text-sm font-bold uppercase tracking-widest">Local services</h2><p className="text-[10px] text-(--text-muted) font-medium uppercase tracking-tighter">Instance availability</p></div>
            {starting && <Loader2 size={16} className="animate-spin text-(--text-muted)" aria-label="Updating services" />}
          </div>
          <div className="service-grid grid grid-cols-1 md:grid-cols-2 gap-4">
            {([{ name: 'backend', detail: 'Agents, sessions, and tools' }, { name: 'frontend', detail: 'Your Sentinel interface' }] as const).map(item => {
              const service = status?.services.find(s => s.name === item.name);
              const state = service?.state || (item.name === 'frontend' ? 'running' : 'stopped');
              const active = state === 'running';
              const pending = state === 'starting' || state === 'stopping';
              const tone = active ? 'good' : state === 'failed' ? 'danger' : pending ? 'warn' : 'default';
              return <div key={item.name} className="rounded-xl border border-(--border-subtle) bg-(--surface-0) overflow-hidden relative">
                <div className="px-4 py-3 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="h-2.5 w-2.5 rounded-full shrink-0" style={{ backgroundColor: active ? '#10B981' : state === 'failed' ? '#F43F5E' : pending ? '#F59E0B' : 'var(--text-muted)' }} />
                    <h3 className="text-xs font-bold uppercase tracking-widest">{labels[item.name]}</h3>
                    <div className="flex-1" />
                    <StatusChip label={state} tone={tone} className="scale-90" />
                  </div>
                  <p className="service-detail text-[11px] leading-relaxed text-(--text-muted)">{item.detail}</p>
                </div>
              </div>;
            })}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button className={primaryButton} disabled={busy || starting} onClick={() => void run(async () => { if (status?.ready) await api.stopServices(); await api.startServices(); })}><RefreshCw size={14} />{status?.ready ? 'Restart services' : 'Start services'}</button>
            <button className={dangerButton + ' border border-rose-500/40 hover:border-rose-500'} disabled={busy || starting || !status?.ready} onClick={() => void run(() => api.stopServices())}><Power size={14} />Stop services</button>
          </div>
        </Panel>
      </section>}
      {sectionId === 'updates' && <section className={section}>
        <h2 className="text-sm font-bold uppercase tracking-widest">Updates</h2>
        {status?.development ? <p className="text-sm text-(--text-muted)">Running local source with live reload. Release updates are available in the installed app.</p> : <>
        <p className="text-sm text-(--text-muted)">{status?.shellVersion ? `App ${status.shellVersion} · ` : legacyShell ? 'App older than 2.3.0 · ' : ''}{status?.payload.installed ? `Service ${status.payload.version}` : 'The app service is not installed yet. Check for an update or install a release file.'}</p>
        <div className="flex flex-wrap gap-3 items-center">
          <label className="text-sm">Channel <select aria-label="Update channel" className="ml-2 rounded border border-(--border-subtle) bg-(--app-bg) px-3 py-2" value={channel} onChange={e => { setChannel(e.target.value as ReleaseChannel); setUpdate(undefined); }}><option value="stable">Stable</option><option value="beta">Beta</option></select></label>
          <button className={button} disabled={busy} onClick={() => void run(async () => { setUpdate(await api.checkForUpdate(channel)); })}>Check for updates</button>
          {devMode && <button className={button} disabled={busy} onClick={() => void run(() => api.installPayloadFromFile())}>Install PR app bundle…</button>}
          {update && !shellUpdate && <button className="btn-primary px-4 py-2 text-sm" disabled={busy} onClick={() => void run(() => api.applyUpdate(update))}>Install {update.version}</button>}
          {shellUpdate && <a className="btn-primary px-4 py-2 text-sm" href={shellUpdate.url} target="_blank" rel="noopener noreferrer"><Download size={14} />Download Sentinel {shellUpdate.version}</a>}
        </div>
        {shellUpdate && <p role="status" className="text-sm">{legacyShell ? 'This app predates 2.3.0 and takes newer releases as a download.' : `Sentinel ${shellUpdate.version} needs app version ${shellUpdate.minShellVersion} or newer.`} Install the new app from the release page; it includes the matching service.</p>}
        {update === null && <p role="status" className="text-sm">No update available on this channel.</p>}
        {progress && <div role="status" className="space-y-2 text-sm"><p>{progress.message}</p>{progress.fractionComplete !== undefined && <progress className="w-full" max={1} value={progress.fractionComplete} />}</div>}
        {update?.hasNewMigrations && <p className="text-sm">This update changes your instance data. Export a backup before installing.</p>}
        </>}
      </section>}
      {sectionId === 'services' && <ServiceLogs entries={logs} openFolder={() => void run(() => api.openLogFolder())} />}
      {sectionId === 'services' && <section className={section}>
        <h2 className="text-sm font-bold uppercase tracking-widest">Recovery</h2>
        <p className="text-sm text-(--text-muted)">Find your app data and manage recovery options.</p>
        <button className={button} onClick={() => void run(() => api.revealAppSupport())}>Open app data</button>
      </section>}
  </div>;
}
