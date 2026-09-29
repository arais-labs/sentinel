import { createContext, useCallback, useContext, useId, useSyncExternalStore, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDown, ArrowUp, Cpu, HardDrive, MemoryStick, Network, Monitor, ChevronDown } from 'lucide-react';
import { ReasoningFluid } from './ReasoningFluid';
import { HISTORY_MS, metricsSnapshot, subscribeMetrics, type MetricSample, type MetricsSnapshot, type WorkspaceMetrics } from '../../lib/workspace-metrics';
import type { Workspace } from '../../types/api';
import './workspace-runtime-stats.css';
import './run-settings.css';

const MetricsContext = createContext<{ workspace: Workspace | null; sample: MetricsSnapshot } | null>(null);
const EMPTY: MetricsSnapshot = { history: [], unavailable: false };

/** Both headers share the cached sampler; opening the panel never starts another request. */
export function WorkspacePerformanceChip({ workspace, instanceName, onChangeWorkspace, onOpen, onClose, workspaceMenu, disabled = false, compact = false, className }: {
  workspace: Workspace | null; instanceName: string | null; onChangeWorkspace?: () => void; onOpen?: () => void; onClose?: () => void; workspaceMenu?: ReactNode; disabled?: boolean; compact?: boolean; className?: string;
}) {
  const id = workspace?.id;
  const subscribe = useCallback((notify: () => void) => id && instanceName
    ? subscribeMetrics(instanceName, id, notify) : () => {}, [id, instanceName]);
  const snapshot = useCallback(() => id && instanceName ? metricsSnapshot(instanceName, id) : EMPTY, [id, instanceName]);
  const sample = useSyncExternalStore(subscribe, snapshot);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const panelId = useId();
  const close = () => { clearTimeout(closeTimer.current); setRect(null); onClose?.(); };
  const show = () => { if (disabled) return; clearTimeout(closeTimer.current); setRect(button.current?.getBoundingClientRect() ?? null); onOpen?.(); };
  const hide = () => { clearTimeout(closeTimer.current); closeTimer.current = setTimeout(() => setRect(null), 150); };
  useEffect(() => {
    if (!rect) return;
    const outside = (event: PointerEvent) => {
      if (!button.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) close();
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
    window.addEventListener('pointerdown', outside);
    window.addEventListener('keydown', escape);
    window.addEventListener('resize', close);
    return () => { window.removeEventListener('pointerdown', outside); window.removeEventListener('keydown', escape); window.removeEventListener('resize', close); };
  }, [rect]);
  useEffect(() => () => clearTimeout(closeTimer.current), []);
  const data = sample.data;
  const cpu = data?.cpu_percent ?? 0;
  const memory = data?.memory_total_bytes ? (data.memory_used_bytes ?? 0) / data.memory_total_bytes * 100 : 0;
  const metric = memory > cpu ? 'RAM' : 'CPU';
  const usage = Math.max(0, Math.min(100, Math.max(cpu, memory)));
  const active = data?.state === 'running' && !sample.unavailable && (data.cpu_percent != null || data.memory_total_bytes != null);
  const color = metric === 'RAM' ? '#ad91ed' : '#63b8ef';
  return <MetricsContext.Provider value={{ workspace, sample }}>
    <button ref={button} type="button" disabled={disabled} data-tour="workspace-attachment" data-attached={workspace ? 'true' : undefined}
      className={`chat-header-pill workspace-performance-chip ${className ?? ''}`}
      aria-label={workspace ? `${workspace.name}${active ? ` · ${metric} ${Math.round(usage)}%` : ''}, workspace performance` : 'Attach a workspace'}
      aria-expanded={!!rect} aria-haspopup="dialog" aria-controls={rect ? panelId : undefined}
      onMouseEnter={show} onMouseLeave={hide} onFocus={show}
      onBlur={event => { if (!panel.current?.contains(event.relatedTarget)) hide(); }} onClick={show}>
      {active && <span className="workspace-performance-fill" aria-hidden="true" style={{ width: `${usage}%`, color }}><ReasoningFluid amount={.96} color={color} waveWidth={9} /></span>}
      <Monitor size={14} /><span className={compact ? 'sr-only' : 'workspace-performance-name'}>{workspace?.name ?? 'Attach'}</span>
    </button>
    {rect && createPortal(<div ref={panel} id={panelId} role="dialog" aria-label={workspace ? `${workspace.name} performance` : 'Attach a workspace'} data-pane-menu
      className="session-telemetry-panel workspace-performance-panel" onMouseEnter={show} onMouseLeave={workspaceMenu ? undefined : hide}
      onFocus={() => clearTimeout(closeTimer.current)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) hide(); }}
      style={{ position: 'fixed', top: Math.min(rect.bottom + 8, Math.max(8, window.innerHeight - 540)), left: Math.max(8, Math.min(rect.left, window.innerWidth - 328)), zIndex: 10000 }}>
      {onChangeWorkspace && <section className="workspace-performance-selector">
        <button type="button" className="run-settings-section-toggle" aria-expanded={!!workspaceMenu} onClick={onChangeWorkspace}>
          <Monitor size={14} /><span className="run-settings-section-label">{workspace ? 'Change workspace' : 'Attach a workspace'}</span><span className="run-settings-section-value">{workspace?.name}</span><ChevronDown size={14} style={{ transform: workspaceMenu ? 'rotate(180deg)' : undefined }} />
        </button>
        {workspaceMenu}
      </section>}
      <WorkspaceRuntimeStats />
    </div>, document.body)}
  </MetricsContext.Provider>;
}

function bytes(value: number | null | undefined, rate = false) {
  if (value == null) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  const unit = Math.min(4, Math.floor(Math.log2(Math.max(1, value)) / 10));
  return `${(value / 1024 ** unit).toFixed(unit > 0 ? 1 : 0)} ${units[unit]}${rate ? '/s' : ''}`;
}
const percent = (value?: number, total?: number) => value != null && total ? value / total * 100 : null;
type Series = { value: (data: WorkspaceMetrics) => number | null | undefined; className?: string };

function HistoryChart({ history, series, maximum = 100, label, ceiling = '100%' }: {
  history: MetricSample[]; series: Series[]; maximum?: number; label: string; ceiling?: string;
}) {
  const gradient = useId().replace(/:/g, '');
  const end = history.at(-1)?.at ?? Date.now();
  const span = Math.min(HISTORY_MS, Math.max(30_000, end - (history[0]?.at ?? end)));
  const start = end - span;
  const x = (at: number) => ((at - start) / span * 280).toFixed(2);
  const y = (value: number) => (48 - Math.max(0, Math.min(1, value / maximum)) * 44).toFixed(2);
  const paths = series.map(({ value }) => {
    const segments: { at: number; value: number }[][] = [];
    let segment: { at: number; value: number }[] = [];
    for (const point of history) {
      const v = value(point.data);
      if (point.at < start) continue;
      if (v == null || !Number.isFinite(v) || (segment.length && point.at - segment.at(-1)!.at > 9000)) {
        if (segment.length) segments.push(segment);
        segment = [];
      }
      if (v != null && Number.isFinite(v)) segment.push({ at: point.at, value: v });
    }
    if (segment.length) segments.push(segment);
    return segments;
  });
  return <div className="workspace-stat-chart">
    <svg viewBox="0 0 280 54" preserveAspectRatio="none" role="img" aria-label={`${label} history`}>
      <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".22" /><stop offset="100%" stopColor="currentColor" stopOpacity=".02" /></linearGradient></defs>
      {[4, 26, 48].map(row => <line className="workspace-stat-grid" key={row} x1="0" x2="280" y1={row} y2={row} />)}
      {paths.map((segments, index) => <g key={index} className={series[index].className}>
        {segments.map((points, i) => {
          const first = points[0], last = points.at(-1)!;
          const line = points.map((p, n) => `${n ? 'L' : 'M'}${x(p.at)},${y(p.value)}`).join(' ');
          return <g key={i}>
            {index === 0 && points.length > 1 && <path className="workspace-stat-area" d={`${line} L${x(last.at)},48 L${x(first.at)},48 Z`} fill={`url(#${gradient})`} />}
            <path className="workspace-stat-trace" d={line} />
            <circle cx={x(last.at)} cy={y(last.value)} r="1.8" fill="currentColor" />
          </g>;
        })}
      </g>)}
    </svg>
    <span className="workspace-stat-ceiling">{ceiling}</span>
    <div className="workspace-stat-axis"><span>{span >= 60_000 ? `${Math.round(span / 60_000)} min` : `${Math.round(span / 1000)} sec`} ago</span><span>Latest</span></div>
  </div>;
}

export function WorkspaceRuntimeStats() {
  const context = useContext(MetricsContext);
  if (!context?.workspace) return null;
  const { workspace, sample } = context;
  const data = sample.data;
  const history = sample.history;
  const measured = data?.memory_total_bytes != null;
  const networkMax = Math.max(1024, ...history.flatMap(p => [p.data.network_receive_bytes_per_second ?? 0, p.data.network_send_bytes_per_second ?? 0]));
  const state = !data ? (sample.unavailable ? 'Stats temporarily unavailable' : 'Collecting first sample…')
    : data.state === 'running' ? 'Waiting for a sample…' : `Workspace ${data.state}`;
  return <section className="workspace-runtime-stats" aria-label="Workspace runtime stats">
    <header><strong>{workspace.name}</strong><span>{sample.unavailable ? 'Last known usage' : 'Usage history'}</span></header>
    {measured ? <>
      <div className="workspace-stat-list">
        <div className="workspace-stat-row" data-metric="cpu">
          <div className="workspace-stat-line"><span><Cpu size={13} />CPU<small>{data.resources?.cpus ? `${data.resources.cpus} cores` : ''}</small></span><strong>{data.cpu_percent == null ? '—' : `${data.cpu_percent.toFixed(0)}%`}</strong></div>
          <HistoryChart history={history} series={[{ value: d => d.cpu_percent }]} label="CPU" />
        </div>
        <div className="workspace-stat-row" data-metric="memory">
          <div className="workspace-stat-line"><span><MemoryStick size={13} />Memory</span><strong>{bytes(data.memory_used_bytes)} <small>/ {bytes(data.memory_total_bytes)}</small></strong></div>
          <HistoryChart history={history} series={[{ value: d => percent(d.memory_used_bytes, d.memory_total_bytes) }]} label="Memory" />
        </div>
        <div className="workspace-stat-row" data-metric="network">
          <div className="workspace-stat-line"><span><Network size={13} />Network</span><div className="workspace-stat-network"><span aria-label="Download"><ArrowDown size={12} />{bytes(data.network_receive_bytes_per_second, true)}</span><span aria-label="Upload"><ArrowUp size={12} />{bytes(data.network_send_bytes_per_second, true)}</span></div></div>
          <HistoryChart history={history} series={[{ value: d => d.network_receive_bytes_per_second }, { value: d => d.network_send_bytes_per_second, className: 'workspace-stat-upload' }]} maximum={networkMax} ceiling={bytes(networkMax, true)} label="Network download and upload" />
        </div>
        <div className="workspace-stat-row" data-metric="disk">
          <div className="workspace-stat-line"><span><HardDrive size={13} />Disk</span><strong>{bytes(data.disk_used_bytes)} <small>/ {bytes(data.disk_total_bytes)}</small></strong></div>
          <div className="workspace-stat-meter" aria-hidden="true"><i style={{ width: `${Math.min(100, percent(data.disk_used_bytes, data.disk_total_bytes) ?? 0)}%` }} /></div>
        </div>
      </div>
      <p>{sample.unavailable ? 'Connection interrupted · showing the last recorded values' : 'Up to 5 minutes · sampled while this workspace is open'}</p>
    </> : <p>{state}</p>}
  </section>;
}
