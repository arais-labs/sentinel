import { useLayoutEffect, useRef, useState } from 'react';
import { Terminal, Wrench } from 'lucide-react';
import { extractCriticalToolFields, parsePayloadJson } from '../lib/toolPayloadPreview';
import { summarizeToolField, summarizeToolResult } from '../lib/toolResultSummary';
import './voice-activity.css';

export interface VoiceToolCall {
  id: string;
  name: string;
  argumentsJson: string;
  outputJson: string;
  isError: boolean;
  status: 'running' | 'done' | 'error';
  startedAt: number;
  elapsedMs?: number;
  leaving?: boolean;
}

const labels = { running: 'Running', done: 'Completed', error: 'Failed' } as const;

function describe(call: VoiceToolCall) {
  const fields = extractCriticalToolFields({ toolName: call.name, raw: call.argumentsJson, kind: 'input', maxFields: 8 });
  const action = fields.find(field => field.key === 'action')?.text;
  const main = fields.find(field => ['objective', 'shell_command', 'cli_command', 'command', 'path', 'query', 'url', 'title', 'message', 'target'].includes(field.key))
    ?? fields.find(field => field.key !== 'action');
  const subject = main ? summarizeToolField(main.fullText ?? main.text) : '';
  const streaming = call.status === 'running' && parsePayloadJson(call.argumentsJson) === null ? call.argumentsJson.replace(/\s+/g, ' ').trim() : '';
  const result = call.outputJson ? summarizeToolResult(call.outputJson, call.isError) : '';
  return { action, subject: subject || streaming, result };
}

/** Live tool calls of the current Voice run, newest first; the same language as chat tool cards. */
export function VoiceActivity({ calls }: { calls: VoiceToolCall[] }) {
  const list = useRef<HTMLUListElement>(null);
  const [clipped, setClipped] = useState(false);
  // The stack is capped at the window's bottom edge; it fades only when it actually reaches it.
  useLayoutEffect(() => {
    const node = list.current;
    if (!node) return;
    const measure = () => {
      const top = node.getBoundingClientRect().top;
      const available = Math.max(120, window.innerHeight - top - 12);
      node.style.maxHeight = `${available}px`;
      setClipped(node.scrollHeight > available + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    window.addEventListener('resize', measure);
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, [calls]);
  if (!calls.length) return null;
  return <ul ref={list} className="voice-activity" data-clipped={clipped || undefined} aria-label="Tool calls">{calls.map(call => {
    const { action, subject, result } = describe(call);
    const terminal = /runtime|shell|exec|terminal/.test(call.name);
    return <li key={call.id} data-status={call.status} className={call.leaving ? 'is-leaving' : undefined}>
      <div className="voice-activity-heading">
        <span className="voice-activity-suit" aria-hidden="true">{terminal ? <Terminal size={13} /> : <Wrench size={13} />}</span>
        <span className="voice-activity-name">{call.name}</span>
        {action && <span className="voice-activity-action">{action}</span>}
        <span className="voice-activity-meta">{call.status === 'running' ? labels.running : `${labels[call.status]}${call.elapsedMs !== undefined ? ` · ${(call.elapsedMs / 1000).toFixed(1)}s` : ''}`}</span>
        <span className="voice-activity-mark" aria-hidden="true" />
      </div>
      <div className="voice-activity-summary">
        <span className="voice-activity-subject">{subject || (call.status === 'running' ? 'Executing…' : result || 'No details returned')}</span>
        {subject && result && <span className="voice-activity-result">{result}</span>}
      </div>
    </li>;
  })}</ul>;
}
