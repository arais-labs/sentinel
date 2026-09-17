import { useMemo, useState } from 'react';
import { Brain, Database, MessageSquare, Search, TriangleAlert, Volume2, Wrench } from 'lucide-react';
import { elapsed, readable, record, text, traceSteps, type TraceDetail, type TraceLens, type TraceStep } from '../lib/voice-trace-view';
import { ToolCard } from './session/ToolCard';
import { JsonBlock } from './ui/JsonBlock';
import { Markdown } from './ui/Markdown';

const json = (value: unknown) => JSON.stringify(value ?? null, null, 2);
const lenses = {
  input: { label: 'Input', icon: MessageSquare }, context: { label: 'Context', icon: Database },
  reasoning: { label: 'Model', icon: Brain }, actions: { label: 'Actions', icon: Wrench },
  output: { label: 'Output', icon: Volume2 }, errors: { label: 'Errors', icon: TriangleAlert },
};

/** Readable payload inspection; the original, unabridged payload lives in Raw. */
function Fields({ value }: { value: unknown }) {
  if (value === undefined || value === null) return <span className="voice-trace-meta">Not recorded</span>;
  if (typeof value !== 'object') return <div className="voice-trace-value">{String(value)}</div>;
  const entries = Array.isArray(value) ? value.map((entry, index) => [String(index + 1), entry] as const) : Object.entries(value);
  if (!entries.length) return <span className="voice-trace-meta">None</span>;
  return <div className="voice-trace-fields">{entries.filter(([, entry]) => entry !== undefined).map(([key, entry]) => {
    const nested = entry !== null && typeof entry === 'object';
    const long = typeof entry === 'string' && entry.length > 240;
    const item = record(entry);
    const name = Array.isArray(value) ? text(item.title) || text(item.name) || text(item.role) || `Item ${key}` : readable(key);
    return nested || long ? <details key={key}>
      <summary>{name}{Array.isArray(entry) && <small>{entry.length}</small>}</summary>
      <Fields value={entry} />
    </details> : <div className="voice-trace-field" key={key}><span>{name}</span><Fields value={entry} /></div>;
  })}</div>;
}

function Step({ step }: { step: TraceStep }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = lenses[step.lens].icon;
  return <div className="voice-trace-step" data-lens={step.lens}>
    <div className="voice-trace-marker"><Icon size={14} /></div>
    <div className="voice-trace-step-content">
      {step.tool ? <ToolCard name={step.tool.name} inputRaw={json(step.tool.input)} outputRaw={json(step.tool.output)}
        failed={step.tool.failed} active={step.tool.active} expanded={expanded} onExpand={setExpanded}
        input={<Fields value={step.tool.input} />} result={<Fields value={step.tool.output} />}
        headerAction={<time className="voice-trace-meta">+{elapsed(step.elapsed)}</time>} />
        : <article className="logs-event voice-trace-card">
          <header><strong>{step.title}</strong><time>+{elapsed(step.elapsed)}</time></header>
          {step.model && <div className="voice-trace-model">{step.model}{step.provider && <span>{step.provider}</span>}{step.tokens !== undefined && <span>{step.tokens.toLocaleString()} tokens</span>}</div>}
          {step.text && (step.lens === 'reasoning' ? <details className="voice-trace-model-text"><summary>{step.text.length > 150 ? `${step.text.slice(0, 150)}…` : step.text}</summary><Markdown content={step.text} compact /></details> : <Markdown content={step.text} compact />)}
          {step.fields !== undefined && <details className="voice-trace-inspect"><summary>{step.lens === 'context' ? 'Inspect context & instructions' : 'Inspect details'}</summary><Fields value={step.fields} /></details>}
          {step.raw !== undefined && <details className="voice-trace-raw"><summary>Raw event</summary><JsonBlock value={json(step.raw)} /></details>}
        </article>}
    </div>
  </div>;
}

export function VoiceTraceTimeline({ trace }: { trace: TraceDetail }) {
  const [lens, setLens] = useState<TraceLens | 'all'>('all');
  const [view, setView] = useState('timeline');
  const [query, setQuery] = useState('');
  const steps = useMemo(() => traceSteps(trace), [trace]);
  const models = [...new Set(steps.flatMap(step => step.model ? [step.model] : []))];
  const modelSteps = steps.filter(step => step.lens === 'reasoning');
  const tokens = modelSteps.reduce((total, step) => total + (step.tokens ?? 0), 0);
  const matches = (value: unknown) => json(value).toLowerCase().includes(query.toLowerCase());
  const visible = steps.filter(step => (lens === 'all' || step.lens === lens) && matches(step));
  return <>
    <div className="voice-trace-run-meta"><span>{models.join(' → ') || (trace.kind === 'transcription' ? 'Whisper' : trace.kind === 'speech' ? 'Kokoro' : 'Voice runtime')}</span>
      {modelSteps.length > 0 && <span>{modelSteps.length} model steps</span>}{tokens > 0 && <span>{tokens.toLocaleString()} tokens</span>}
      <span>{steps.filter(step => step.tool).length} actions</span>
    </div>
    <div className="voice-trace-viewbar"><div className="logs-lenses" role="group" aria-label="Trace view">
      <button aria-pressed={view === 'timeline'} onClick={() => setView('timeline')}>Timeline</button>
      <button aria-pressed={view === 'raw'} onClick={() => setView('raw')}>Raw events · {trace.events.length}</button>
    </div><label className="voice-trace-search"><Search size={13} /><input aria-label="Search trace" placeholder="Search trace…" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
    {view === 'timeline' ? <>
      <div className="voice-trace-lenses" role="group" aria-label="Event type">
        <button aria-pressed={lens === 'all'} onClick={() => setLens('all')}>All</button>
        {(Object.entries(lenses) as [TraceLens, typeof lenses.input][]).filter(([key]) => steps.some(step => step.lens === key)).map(([key, { label, icon: Icon }]) => <button key={key} aria-pressed={lens === key} onClick={() => setLens(key)}><Icon size={12} />{label}</button>)}
      </div>
      <div className="voice-trace-events">{visible.map(step => <Step key={step.id} step={step} />)}{!visible.length && <p className="voice-trace-empty">No matching events.</p>}</div>
    </> : <div className="voice-trace-raw-events">
      <details><summary>Final output</summary><JsonBlock value={json(trace.output)} /></details>
      {[...trace.events].reverse().filter(matches).map(event => <details key={event.id}><summary><span>{readable(event.kind)}</span><time>+{elapsed(event.elapsed_ms)}</time></summary><JsonBlock value={json(event.payload)} /></details>)}
      <details><summary>Trace input & settings</summary><JsonBlock value={json({ id: trace.id, input: trace.input, selection: trace.selection })} /></details>
    </div>}
  </>;
}
