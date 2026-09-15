import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { streamReview } from './reviewTransport';
import { Markdown } from '../ui/Markdown';
type Check = { id: number; name?: string; context?: string; status?: string; conclusion?: string; state?: string; output?: { summary?: string; text?: string } };
type Step = { number: number; name: string; status: string; conclusion?: string };
export function ReviewChecks({ instance, url, account, checks }: { instance: string; url: string; account: string; checks: Check[] }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [logs, setLogs] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [live, setLive] = useState(false);
  const [output, setOutput] = useState<Check['output']>();
  const [status, setStatus] = useState('');
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  const open = async (check: Check) => {
    abort.current?.abort();
    if (selected === check.id) { setSelected(null); setLive(false); return; }
    const controller = new AbortController(); abort.current = controller;
    setSelected(check.id); setSteps([]); setLogs(''); setError(''); setNotice(''); setOutput(check.output); setStatus(check.conclusion ?? check.status ?? check.state ?? '');
    if (!check.name) { setNotice('This commit status does not provide check-run logs.'); setLive(false); return; }
    setLive(true);
    try { await streamReview(instance, 'checks', { url, account_id: account || undefined, check_id: check.id }, event => {
      if (controller.signal.aborted) return;
      if (event.event === 'job') setSteps((event.data as { steps: Step[] }).steps ?? []);
      if (event.event === 'check') { const value = event.data as Check; setOutput(value.output); setStatus(value.conclusion ?? value.status ?? ''); }
      if (event.event === 'log') setLogs(value => value + String(event.data));
      if (event.event === 'notice') setNotice(event.message ?? '');
    }, controller.signal); } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Unable to load logs'); }
    finally { if (!controller.signal.aborted) setLive(false); }
  };
  return <div className="pr-check-list">{checks.length ? checks.map(check => <section key={`${check.name ?? check.context}:${check.id}`}><button className="pr-check" aria-expanded={selected === check.id} onClick={() => void open(check)}>{selected === check.id ? <ChevronDown size={13} /> : <ChevronRight size={13} />}<span className={`pr-check-dot ${selected === check.id ? status : check.conclusion ?? check.state ?? check.status}`} /><strong>{check.name ?? check.context}</strong><span>{(selected === check.id ? status : check.conclusion ?? check.state ?? check.status ?? 'Unknown').replaceAll('_', ' ')}</span></button>{selected === check.id && <div className="pr-check-output">{live && <p role="status"><Loader2 size={12} className="animate-spin" />Following job progress</p>}{error && <p role="alert" className="pr-error">{error}</p>}{steps.map(step => <div className="pr-job-step" key={step.number}><span className={`pr-check-dot ${step.conclusion ?? step.status}`} /><span>{step.name}</span><small>{(step.conclusion ?? step.status).replaceAll('_', ' ')}</small></div>)}{output?.summary && <Markdown content={output.summary} />}{output?.text && <Markdown content={output.text} />}{notice && <p className="pr-note">{notice}</p>}{logs && <pre className="pr-job-logs" aria-label="Job logs">{logs}</pre>}</div>}</section>) : <p className="pr-note">No checks reported.</p>}</div>;
}
