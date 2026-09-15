import { Code2, Copy, Terminal, Wrench, X } from 'lucide-react';
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { extractCriticalToolFields, parsePayloadJson } from '../../lib/toolPayloadPreview';
import './tool-card.css';
import { summarizeToolField, summarizeToolResult } from '../../lib/toolResultSummary';
import { FlowingToolTail, useToolInputRate } from './StreamingToolPreview';

type Props = {
  name: string;
  inputRaw: string;
  outputRaw: string;
  failed?: boolean;
  pending?: boolean;
  active?: boolean;
  expanded: boolean;
  onExpand: (expanded: boolean) => void;
  input: ReactNode;
  result: ReactNode;
  resultCopyControl?: ReactNode;
  preview?: ReactNode;
  actions?: ReactNode;
  headerAction?: ReactNode;
};

function record(raw: string): Record<string, unknown> {
  const value = parsePayloadJson(raw);
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function pretty(raw: string) {
  const parsed = parsePayloadJson(raw);
  return parsed === null ? raw : JSON.stringify(parsed, null, 2);
}

/** Shared presentation for live calls and persisted results. Payloads stay untouched. */
export function ToolCard(props: Props) {
  const { name, inputRaw, outputRaw, expanded, onExpand } = props;
  const id = useId();
  const inspectorContentRef = useRef<HTMLDivElement>(null);
  const [inspectorHeight, setInspectorHeight] = useState(0);
  useLayoutEffect(() => {
    if (!expanded || !inspectorContentRef.current) return;
    const content = inspectorContentRef.current;
    const measure = () => {
      const height = Math.ceil(content.getBoundingClientRect().height) + 2;
      setInspectorHeight(previous => previous === height ? previous : height);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    return () => observer.disconnect();
  }, [expanded]);
  const inspectRef = useRef<HTMLButtonElement>(null);
  function closeInspect() { onExpand(false); inspectRef.current?.focus(); }
  const [view, setView] = useState<'result' | 'input' | 'raw'>('result');
  const [copyStatus, setCopyStatus] = useState('Copy');
  const [statusOpen, setStatusOpen] = useState(false);
  const output = record(outputRaw);
  const status = String(output.status ?? '').toLowerCase();
  const cancelled = ['cancelled', 'canceled', 'aborted', 'stopped'].includes(status);
  const failed = props.failed || output.ok === false || output.success === false || ['error', 'failed', 'failure'].includes(status);
  const state = props.pending ? 'approval' : cancelled ? 'cancelled' : failed ? 'error' : props.active ? 'running' : 'complete';
  const inputRate = useToolInputRate(inputRaw, state === 'running');
  const label = { approval: 'Needs approval', cancelled: 'Cancelled', error: 'Failed', running: 'Running', complete: 'Completed' }[state];
  const fields = extractCriticalToolFields({ toolName: name, raw: inputRaw, kind: 'input', maxFields: 8 });
  const action = fields.find(field => field.key === 'action');
  const main = fields.find(field => ['objective', 'shell_command', 'cli_command', 'command', 'path', 'query', 'url', 'title'].includes(field.key))
    ?? fields.find(field => field.key !== 'action');
  // Incomplete JSON has no fields yet. Use the incoming buffer rather than
  // the generic preview, which only retains its first 180 characters.
  const streamingText = state === 'running'
    ? (parsePayloadJson(inputRaw) === null ? inputRaw : main?.fullText ?? main?.text ?? '').replace(/\s+/g, ' ').trim()
    : '';
  const showStreamingTail = streamingText.length > 120;
  const errorValue = output.error ?? output.stderr ?? output.message ?? output.detail ?? output.reason;
  const errorRecord = errorValue && typeof errorValue === 'object' ? errorValue as Record<string, unknown> : null;
  const errorDetail = errorRecord?.message ?? errorRecord?.detail ?? errorRecord?.reason ?? errorValue;
  const operation = main?.fullText ?? main?.text ?? action?.text ?? name;
  const resultText = summarizeToolResult(outputRaw, Boolean(failed));
  const mainText = main ? summarizeToolField(main.fullText ?? main.text) : '';
  const summaryText = state === 'error' ? resultText || 'Tool failed'
    : mainText || resultText || (props.active ? 'Executing tool…' : 'No additional details returned');
  const statusDetail = state === 'error'
    ? (typeof errorDetail === 'string' ? errorDetail : errorDetail != null ? JSON.stringify(errorDetail, null, 2) : outputRaw).trim() || 'The tool failed without an error description.'
    : state === 'running' ? `Running ${operation}${resultText ? `\n\n${resultText}` : ''}`
    : state === 'approval' ? `Approval required for ${operation}${resultText ? `\n\n${resultText}` : ''}`
    : state === 'cancelled' ? `Stopped: ${operation}${resultText ? `\n\n${resultText}` : ''}`
    : resultText || `${(action?.text ?? name).replace(/[_-]+/g, ' ')} finished successfully.`;
  const raw = `INPUT\n${pretty(inputRaw) || '(empty)'}\n\nRESULT\n${pretty(outputRaw) || '(empty)'}`;
  async function copy() {
    try {
      await navigator.clipboard.writeText(view === 'raw' ? raw : view === 'input' ? inputRaw : outputRaw);
      setCopyStatus('Copied');
    } catch { setCopyStatus('Copy unavailable'); }
  }
  return (
    <article className="sentinel-tool-card" data-state={state} data-status-open={statusOpen || undefined} data-inspect-open={expanded || undefined}
      style={{ minHeight: expanded ? inspectorHeight : 0 }}
      onMouseLeave={() => setStatusOpen(false)}
      onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setStatusOpen(false); }}
      onKeyDown={(event) => { if (event.key === 'Escape') { if (statusOpen) { event.stopPropagation(); setStatusOpen(false); } else if (expanded) { event.stopPropagation(); closeInspect(); } } }}>

      <div className="tool-card-heading">
        <span className="tool-card-suit" aria-hidden="true">{ /runtime|shell|exec|terminal/.test(name) ? <Terminal size={15} /> : <Wrench size={15} /> }</span>
        <span className="tool-card-name">{name}</span>
        {action && <span className="tool-card-action">{action.text}</span>}
        <button type="button" className="tool-card-status" aria-label={statusOpen ? 'Close status details' : `${label}: show status details`}
          aria-expanded={statusOpen} aria-controls={`${id}-status`}
          onPointerEnter={(event) => { if (event.pointerType === 'mouse') setStatusOpen(true); }}
          onClick={() => setStatusOpen(open => !open)}>
          <span className="tool-card-status-mark" aria-hidden="true" />
        </button>
        {props.headerAction}
      </div>
      <div id={`${id}-status`} className="tool-card-status-reveal" aria-hidden={!statusOpen}>
        <div className="tool-card-status-detail" tabIndex={statusOpen ? 0 : -1}>
          <span className="tool-card-status-label">{label}</span>
          <p>{statusDetail.replace(/\bpane_id\b/g, 'pane ID').replace(/\bterminal_list\b/g, 'the terminal list')}</p>
        </div>
      </div>
      <button ref={inspectRef} className="tool-card-disclosure" tabIndex={statusOpen || expanded ? -1 : undefined} type="button" aria-expanded={expanded} aria-controls={id} onClick={() => onExpand(!expanded)}>
        <span className="tool-card-summary">
          {showStreamingTail ? (
            <span className="tool-card-stream-preview">
              <span className="tool-card-stream-start">{streamingText.slice(0, 55)}</span>
              <span className="tool-card-stream-gap">…</span>
              <FlowingToolTail text={streamingText} />
            </span>
          ) : <span className="tool-card-subject">{summaryText}</span>}
          {mainText && resultText && resultText !== summaryText && <span className="tool-card-detail">{resultText}</span>}
        </span>
        <span className="tool-card-inspect-controls">
          {inputRate !== null && <span className="tool-card-token-rate" title="Estimated input tokens per second over the last 3 seconds (approximately 4 characters per token).">≈{inputRate} tok/s</span>}
        <span className="tool-card-inspect">Inspect</span>
        </span>
      </button>
      {props.preview && <div className="tool-card-preview">{props.preview}</div>}
      <div id={id} className="tool-card-inspector" inert={statusOpen || !expanded} aria-hidden={!expanded}>
        <div ref={inspectorContentRef} className="tool-card-inspector-content">
        <div className="tool-card-toolbar">
          <div className="tool-card-views" aria-label="Payload view">
            {(['result', 'input', 'raw'] as const).map(item => <button type="button" key={item} aria-pressed={view === item} onClick={() => { setView(item); setCopyStatus('Copy'); }}>{item === 'raw' && <Code2 size={12} />}{item}</button>)}
          </div>
          {view === 'result' && props.resultCopyControl
            ? <div className="tool-card-image-copy">{props.resultCopyControl}</div>
            : <button type="button" className="tool-card-copy" onClick={copy}><Copy size={12} /><span aria-live="polite">{copyStatus}</span></button>}
          <button type="button" className="tool-card-inspect-close" onClick={closeInspect} aria-label="Close inspector"><X size={14} /></button>
        </div>
        <div className="tool-card-payload">
          {expanded && (view === 'raw' ? <pre>{raw}</pre> : view === 'input' ? props.input : props.result)}
        </div>
        </div>
      </div>
      {props.actions && <div className="tool-card-actions" inert={statusOpen}>{props.actions}</div>}
    </article>
  );
}
