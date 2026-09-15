import { useEffect, useMemo, useRef, useState } from 'react';
import { MenuItem, Select } from '@mui/material';
import { ArrowDownToLine, Check, ChevronRight, Copy, FolderOpen, Search, ScrollText, WrapText, X } from 'lucide-react';
import type { LogEntry } from '../../../../desktop/sentinel/src/shared/ipc';

const services: Record<LogEntry['service'], string> = {
  backend: 'Sentinel service', frontend: 'Interface', manager: 'App',
};
type Level = 'error' | 'warning' | 'info' | 'debug';
type LogRecord = { entry: LogEntry; level: Level; lines: string[]; summary: string; trace: boolean; index: number };
const colors: Record<Level, string> = {
  error: 'text-rose-500 dark:text-rose-400', warning: 'text-amber-600 dark:text-amber-400',
  info: 'text-sky-600 dark:text-sky-400', debug: 'text-(--text-muted)',
};
const time = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
function levelOf(line: string): Level {
  if (/\b(ERROR|CRITICAL|FATAL|Exception|\w+Error)\b|HTTP\/\d[^\n]*\s5\d\d\b/.test(line)) return 'error';
  if (/\b(WARNING|WARN)\b/.test(line)) return 'warning';
  return /\bDEBUG\b/.test(line) ? 'debug' : 'info';
}
function recordsFrom(entries: LogEntry[]): LogRecord[] {
  const result: LogRecord[] = [];
  for (const [index, entry] of entries.entries()) {
    const line = entry.line.replace(/\x1b\[[0-9;]*m/g, '');
    const startsTrace = /Traceback \(most recent call last\)|Exception Group Traceback/.test(line);
    const previous = result.at(-1);
    const freshLine = /^(?:\d{4}-\d\d-\d\d\b|\[|(?:INFO|DEBUG|WARNING|WARN|ERROR|CRITICAL|FATAL):?\s)/.test(line);
    if (previous?.trace && previous.entry.service === entry.service && !freshLine) {
      previous.lines.push(line);
      const exception = line.match(/(?:^|[|\s])((?:[\w.]*Error|[\w.]*Exception|InstanceRuntimeNotConfigured):\s*.+)/);
      if (exception) previous.summary = exception[1];
      continue;
    }
    result.push({ entry, index, level: startsTrace ? 'error' : levelOf(line), trace: startsTrace,
      lines: [line], summary: startsTrace ? 'Exception traceback' : line });
  }
  return result;
}

export function ServiceLogs({ entries, openFolder }: { entries: LogEntry[]; openFolder: () => void }) {
  const [service, setService] = useState('all');
  const [level, setLevel] = useState('all');
  const [query, setQuery] = useState('');
  const [follow, setFollow] = useState(true);
  const [wrap, setWrap] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState('');
  const viewport = useRef<HTMLDivElement>(null);
  const records = useMemo(() => recordsFrom(entries), [entries]);
  const visible = useMemo(() => records.filter(record =>
    (service === 'all' || record.entry.service === service)
    && (level === 'all' || record.level === level)
    && record.lines.join('\n').toLowerCase().includes(query.toLowerCase())), [records, service, level, query]);
  const errors = records.filter(record => record.level === 'error').length;
  const warnings = records.filter(record => record.level === 'warning').length;
  useEffect(() => {
    if (follow && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [visible, follow]);
  useEffect(() => {
    if (!copied) return;
    const timeout = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(timeout);
  }, [copied]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(visible.map(record => `[${record.entry.at}] ${services[record.entry.service]}: ${record.lines.join('\n')}`).join('\n'));
      setCopied(true); setCopyError('');
    } catch { setCopyError('Could not copy logs. Select the text to copy it manually.'); }
  }
  const control = 'service-log-control';
  const selectStyle = { height: 34, fontSize: 12, minWidth: 130, borderRadius: '6px', '& .MuiOutlinedInput-notchedOutline': { borderColor: 'var(--border-subtle)' } };
  return <section className="service-log-view" aria-label="Service logs">
    <header className="service-log-heading">
      <div className="flex items-center gap-3"><div className="p-2 rounded-lg bg-(--surface-2)"><ScrollText size={18} /></div><div><h2 className="text-sm font-bold uppercase tracking-widest">Service logs</h2><p className="mt-1 text-[11px] text-(--text-muted)">Live activity and diagnostics</p></div></div>
    </header>
    <div className="service-log-toolbar">
      <label className="service-log-search"><Search size={14} /><input aria-label="Search logs" placeholder="Search messages, errors, paths…" value={query} onChange={event => setQuery(event.target.value)} />{query && <button aria-label="Clear search" onClick={() => setQuery('')}><X size={13} /></button>}</label>
      <Select value={service} onChange={event => setService(event.target.value)} inputProps={{ 'aria-label': 'Filter by service' }} sx={selectStyle}>
        <MenuItem value="all">All services</MenuItem>{Object.entries(services).map(([value,label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
      </Select>
      <Select value={level} onChange={event => setLevel(event.target.value)} inputProps={{ 'aria-label': 'Filter by severity' }} sx={selectStyle}>
        <MenuItem value="all">All levels</MenuItem>{['error','warning','info','debug'].map(value => <MenuItem key={value} value={value}>{value[0].toUpperCase() + value.slice(1)}</MenuItem>)}
      </Select>
      <div className="flex gap-1">
        <button className={control} aria-label="Open log folder" title="Open log folder" onClick={openFolder}><FolderOpen size={15} /></button>
        <button className={control} aria-label="Wrap lines" title="Wrap lines" aria-pressed={wrap} onClick={() => setWrap(!wrap)}><WrapText size={15} /></button>
        <button className={control} aria-label="Copy filtered logs" title="Copy filtered logs" disabled={!visible.length} onClick={() => void copy()}>{copied ? <Check size={15} /> : <Copy size={15} />}</button>
        <button className={control} aria-pressed={follow} onClick={() => setFollow(!follow)}><ArrowDownToLine size={14} />Follow</button>
      </div>
    </div>
    <div className="service-log-summary"><span>{visible.length} of {records.length} events <span className="text-(--text-muted)">· latest {entries.length} lines</span></span><div className="flex items-center gap-3"><button className={errors ? colors.error : ''} onClick={() => setLevel(level === 'error' ? 'all' : 'error')}>{errors} {errors === 1 ? 'error' : 'errors'}</button><button className={warnings ? colors.warning : ''} onClick={() => setLevel(level === 'warning' ? 'all' : 'warning')}>{warnings} {warnings === 1 ? 'warning' : 'warnings'}</button><span className="service-log-live"><span />Live</span></div></div>
    {copyError && <p role="alert" className="text-xs text-rose-400">{copyError}</p>}
    <div className="service-log-table">
      <div className="service-log-columns" aria-hidden="true"><span>Time</span><span>Service</span><span>Level</span><span>Message</span></div>
      <div className="service-log-scroll" ref={viewport} onWheel={event => { if (event.deltaY < 0) setFollow(false); }} onScroll={event => { const el = event.currentTarget; if (follow && el.scrollHeight - el.scrollTop - el.clientHeight > 60) setFollow(false); }}>
        {!visible.length ? <div className="service-log-empty"><Search size={22} /><strong>{entries.length ? 'No matching log entries' : 'Waiting for activity'}</strong><span>{entries.length ? 'Try another service, severity, or search term.' : 'New service messages will appear here.'}</span>{entries.length > 0 && <button className={control} onClick={() => { setService('all'); setLevel('all'); setQuery(''); }}>Reset filters</button>}</div> : visible.map(record => {
          const key = `${record.entry.at}:${record.entry.service}:${record.index}`;
          const date = new Date(record.entry.at);
          const timestamp = Number.isNaN(date.valueOf()) ? record.entry.at : time.format(date);
          return <div key={key} className={`service-log-row ${record.level === 'error' ? 'is-error' : ''}`}>
            <time dateTime={record.entry.at} title={record.entry.at}>{timestamp}</time><span className="service-log-source">{services[record.entry.service]}</span><span className={`service-log-level ${colors[record.level]}`}>{record.level}</span>
            <div className="service-log-message">
              {record.trace ? <><button className="service-log-trace" aria-expanded={expanded.has(key)} onClick={() => { setFollow(false); setExpanded(current => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; }); }}><ChevronRight size={13} className={expanded.has(key) ? 'rotate-90' : ''} /><span>{record.summary}</span><small>{record.lines.length} lines</small></button>{expanded.has(key) && <pre className="service-log-stack">{record.lines.join('\n')}</pre>}</> : <span className={wrap ? 'service-log-wrapped' : 'service-log-nowrap'}>{record.summary || ' '}</span>}
            </div>
          </div>;
        })}
      </div>
    </div>
    <footer className="service-log-footer"><span>{follow ? 'Following newest activity' : 'Scroll paused · new logs are still collected'}</span>{!follow && <button onClick={() => setFollow(true)}>Jump to latest ↓</button>}<span role="status">{copied ? 'Copied to clipboard' : 'Times shown in your local timezone'}</span></footer>
  </section>;
}
