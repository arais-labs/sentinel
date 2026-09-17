import { useEffect, useState } from 'react';
import { Activity, ChevronDown, ChevronLeft, ChevronRight, Download, RefreshCw } from 'lucide-react';
import { requestJson } from '../lib/api';
import { elapsed, type TraceSummary, type TraceEvent, type TraceDetail } from '../lib/voice-trace-view';
import { VoiceTraceTimeline } from './VoiceTraceTimeline';
import '../pages/logs-page.css';
import { StatusChip } from './ui/StatusChip';
import './voice-trace-log.css';

const json = (value: unknown) => JSON.stringify(value, null, 2);
const label = (kind: string) => ({ turn: 'Conversation', report: 'Agent report', transcription: 'Recognition', speech: 'Speech' })[kind] ?? kind;
const tone = (status: string) => status === 'completed' ? 'good' as const : status === 'error' ? 'danger' as const : status === 'running' ? 'info' as const : 'default' as const;
async function fetchDetail(path: string, id: string, signal?: AbortSignal) {
  let cursor = 0;
  let events: TraceEvent[] = [];
  let page: TraceDetail;
  do {
    page = await requestJson<TraceDetail>(`${path}/traces/${id}?after=${cursor}`, { signal });
    events = [...events, ...page.events]; cursor = events.at(-1)?.id ?? cursor;
  } while (page.next_after !== null && !signal?.aborted);
  return { ...page, events };
}

export function VoiceTraceLog({ path }: { path: string }) {
  const [expanded, setExpanded] = useState(false);
  const [items, setItems] = useState<TraceSummary[]>([]);
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState('audio');
  const [next, setNext] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  useEffect(() => { setSelected(null); setDetail(null); setOffset(0); setItems([]); }, [path]);
  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    let pending = false;
    setLoading(true);
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await requestJson<{ items: TraceSummary[]; next_offset: number | null }>(`${path}/traces?offset=${offset}&kind=${kind}`, { signal: controller.signal });
        if (!controller.signal.aborted) { setItems(data.items); setNext(data.next_offset); setError(''); setSelected(current => current ?? data.items[0]?.id ?? null); }
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load Voice traces.'); }
      finally { pending = false; if (!controller.signal.aborted) setLoading(false); }
    };
    void load();
    const timer = setInterval(() => { void load(); }, 3000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [path, offset, kind, expanded, refresh]);
  useEffect(() => {
    setDetail(null); setDetailError('');
    if (!expanded || !selected) return;
    const controller = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await fetchDetail(path, selected, controller.signal);
        if (!controller.signal.aborted) { setDetail(data); setDetailError(''); }
      } catch (err) { if (!controller.signal.aborted) setDetailError(err instanceof Error ? err.message : 'Could not load trace details.'); }
      finally { pending = false; }
    };
    void load();
    const timer = setInterval(() => { void load(); }, 3000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [path, selected, expanded, refresh]);
  const download = async () => {
    if (!selected) return;
    setExporting(true);
    try {
      const data = await fetchDetail(path, selected);
      const url = URL.createObjectURL(new Blob([json(data)], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = `sentinel-voice-${selected}.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setDetailError(err instanceof Error ? err.message : 'Export failed.'); }
    finally { setExporting(false); }
  };
  return <section className="app-appearance-panel logs-page voice-trace-log" aria-label="Voice activity logs">
    <button className="voice-trace-heading" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
      <Activity size={16} /><strong>Activity &amp; traces</strong><ChevronDown size={14} className={expanded ? 'rotate-180' : ''} />
    </button>
    {expanded && <>
      <div className="voice-trace-toolbar">
        <div className="logs-lenses" role="group" aria-label="Trace filter">{[['audio', 'Speech'], ['all', 'All']].map(([value, title]) => <button key={value} aria-pressed={kind === value} onClick={() => { setKind(value); setOffset(0); setSelected(null); setDetail(null); }}>{title}</button>)}</div>
        <span className="voice-trace-live">Live · This instance</span><button className="btn-secondary" aria-label="Refresh traces" title="Refresh traces" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={13} /></button>
      </div>
      {error && <p role="alert" className="voice-settings-danger">{error}</p>}
      <div className="voice-trace-workspace">
        <aside className="voice-trace-sidebar" aria-label="Voice requests">
          <div className="voice-trace-list">
            {!items.length && !error && <p className="voice-trace-empty">{loading ? 'Loading traces…' : 'No Voice activity yet.'}</p>}
            {items.map(item => <button key={item.id} className="voice-trace-row" aria-pressed={selected === item.id} onClick={() => setSelected(item.id)}>
              <span className="voice-trace-row-title"><span>{label(item.kind)}</span><span className="voice-trace-status-dot" data-status={item.status} title={item.status} aria-label={item.status} /></span>
              <span className="voice-trace-preview">{item.preview || 'No text recorded'}</span>
              <span className="voice-trace-meta"><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time> · {elapsed(item.duration_ms)}{item.selection.tier ? ` · ${item.selection.tier}` : ''}</span>
            </button>)}
          </div>
          <div className="voice-trace-pagination">
            <button className="btn-secondary" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 25)); setSelected(null); }}><ChevronLeft size={12} />Newer</button>
            <button className="btn-secondary" disabled={next === null} onClick={() => { setOffset(next!); setSelected(null); }}>Older<ChevronRight size={12} /></button>
          </div>
        </aside>
        <div className="voice-trace-detail" key={selected}>
          {detailError && <p role="alert" className="voice-settings-danger">{detailError}</p>}
          {(!detail || detail.id !== selected) && !detailError && <p className="voice-trace-empty">{selected ? 'Loading trace…' : 'Select a request to inspect its activity.'}</p>}
          {detail && detail.id === selected && <>
            <header className="voice-trace-detail-heading"><strong>{label(detail.kind)}</strong><StatusChip label={detail.status} tone={tone(detail.status)} /><span>{elapsed(detail.duration_ms)}</span>
              <button className="btn-secondary" disabled={exporting} onClick={() => void download()}><Download size={13} />{exporting ? 'Exporting…' : 'Export'}</button>
            </header>
            <div className="voice-trace-meta"><time dateTime={detail.created_at}>{new Date(detail.created_at).toLocaleString()}</time></div>
            <div className="voice-trace-related">
              {typeof detail.input.transcription_trace_id === 'string' && <button onClick={() => setSelected(detail.input.transcription_trace_id as string)}>Recognition<ChevronRight size={11} /></button>}
              {typeof detail.input.parent_trace_id === 'string' && <button onClick={() => setSelected(detail.input.parent_trace_id as string)}>Conversation<ChevronRight size={11} /></button>}
              {detail.related.map((item, index) => <button key={item.id} onClick={() => setSelected(item.id)}>Speech {index + 1}<ChevronRight size={11} /></button>)}
            </div>
            {detail.status === 'running' && <p className="voice-trace-meta">No terminal event recorded yet. An interrupted backend can leave a partial trace.</p>}
            <VoiceTraceTimeline key={detail.id} trace={detail} />
          </>}
        </div>
      </div>
      <p className="voice-trace-footer">Traces include private conversation context. Exports omit audio and credential fields.</p>
    </>}
  </section>;
}
