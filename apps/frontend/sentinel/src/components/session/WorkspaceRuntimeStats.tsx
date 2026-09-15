import { createContext, useCallback, useContext, useId, useSyncExternalStore, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, Cpu, HardDrive, MemoryStick, Network } from 'lucide-react';
import { useSessionWorkspace } from '../../hooks/useSessionWorkspace';
import { HISTORY_MS, metricsSnapshot, subscribeMetrics, type MetricSample, type MetricsSnapshot, type WorkspaceMetrics } from '../../lib/workspace-metrics';
import type { Workspace } from '../../types/api';
import './workspace-runtime-stats.css';

const MetricsContext = createContext<{ workspace: Workspace | null; sample: MetricsSnapshot } | null>(null);
const EMPTY: MetricsSnapshot = { history: [], unavailable: false };

/** Stays mounted with the chat header, so opening the popover only renders cached data. */
export function WorkspaceMetricsProvider({ sessionId, instanceName, children }: {
  sessionId: string | null; instanceName: string | null; children: ReactNode;
}) {
  const { workspace } = useSessionWorkspace(sessionId, instanceName);
  const id = workspace?.id;
  const subscribe = useCallback((notify: () => void) => id && instanceName
    ? subscribeMetrics(instanceName, id, notify) : () => {}, [id, instanceName]);
  const snapshot = useCallback(() => id && instanceName ? metricsSnapshot(instanceName, id) : EMPTY, [id, instanceName]);
  const sample = useSyncExternalStore(subscribe, snapshot);
  return <MetricsContext.Provider value={{ workspace, sample }}>{children}</MetricsContext.Provider>;
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
