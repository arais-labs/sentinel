import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, ListTree, Pencil, Send, X } from 'lucide-react';
import type { Message } from '../../types/api';

type Question = { id: string; parent_id: string | null; question: string; options: { id: string; label: string; description?: string }[] };
type Form = { kind: 'form'; form_id: string; title: string; questions: Question[] };
type Answer = { question_id: string; option_id: string | null; text: string };
export type FormResponse = { form_id: string; status: 'answered' | 'dismissed'; answers: Answer[] };

export function pendingForm(messages: Message[]): Form | null {
  const answered = new Set(messages.filter(m => m.role === 'user').map(m => (m.metadata.form_response as FormResponse | undefined)?.form_id));
  for (const message of [...messages].reverse()) {
    if (message.role !== 'tool_result' || message.tool_name !== 'form') continue;
    try {
      const form = JSON.parse(message.content) as Form;
      if (form.kind === 'form' && form.form_id && Array.isArray(form.questions) ) return answered.has(form.form_id) ? null : form;
    } catch { /* Tool errors are rendered in the conversation. */ }
  }
  return null;
}

function orderedQuestions(questions: Question[]): Question[] {
  const result: Question[] = [];
  const stack = questions.filter(q => !q.parent_id).reverse();
  const seen = new Set<string>();
  while (stack.length) {
    const question = stack.pop()!;
    if (seen.has(question.id)) continue;
    seen.add(question.id); result.push(question);
    stack.push(...questions.filter(q => q.parent_id === question.id).reverse());
  }
  return result;
}

function FormReviewBranch({ question, children, answers, onEdit, disabled }: {
  question: Question;
  children: Map<string | null, Question[]>;
  answers: Record<string, Answer>;
  onEdit: (id: string) => void;
  disabled: boolean;
}) {
  const answer = answers[question.id];
  const choice = question.options.find(option => option.id === answer?.option_id);
  const descendants = children.get(question.id) ?? [];
  return <li className="agent-form-review-branch">
    <button type="button" className="agent-form-review-answer" disabled={disabled} onClick={() => onEdit(question.id)} aria-label={`Edit answer: ${question.question}`}>
      <span className="agent-form-review-heading">
        <span className="agent-form-review-id">{question.id}</span>
        <span className="flex-1 min-w-0">{question.question}</span>
        <Pencil size={13} className="shrink-0 text-(--text-muted)" />
      </span>
      {choice && <span className="agent-form-review-choice"><Check size={12} className="shrink-0" />{choice.label}</span>}
      {answer?.text.trim() && <span className="agent-form-review-text">{answer.text}</span>}
    </button>
    {descendants.length > 0 && <ul className="agent-form-review-children">
      {descendants.map(child => <FormReviewBranch key={child.id} question={child} children={children} answers={answers} onEdit={onEdit} disabled={disabled} />)}
    </ul>}
  </li>;
}

export function AgentForm({ form, disabled, onSubmit, floating = false }: { floating?: boolean; form: Form; disabled: boolean; onSubmit: (response: FormResponse) => boolean }) {
  const ordered = useMemo(() => orderedQuestions(form.questions), [form]);
  const children = useMemo(() => {
    const branches = new Map<string | null, Question[]>();
    for (const question of ordered) {
      const parent = question.parent_id ?? null;
      branches.set(parent, [...(branches.get(parent) ?? []), question]);
    }
    return branches;
  }, [ordered]);
  const storageKey = `sentinel.form.${form.form_id}`;
  const [answers, setAnswers] = useState<Record<string, Answer>>(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch { return {}; }
  });
  const [step, setStep] = useState(0);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!sent) return;
    const timeout = window.setTimeout(() => {
      setSent(false);
      setError('No confirmation received. You can retry; your answers are saved.');
    }, 10000);
    return () => window.clearTimeout(timeout);
  }, [sent]);
  function submit(status: FormResponse['status']) {
    const accepted = onSubmit({ form_id: form.form_id, status, answers: status === 'dismissed' ? [] : ordered.map(q => answers[q.id]) });
    if (accepted) { setSent(true); setError(''); }
    else setError('Reconnect to send your response. Your answers are saved.');
  }
  const question = ordered[step];
  const answer = question ? answers[question.id] : null;
  const ready = Boolean(answer?.option_id || answer?.text.trim());
  const trail: Question[] = [];
  let ancestor = question;
  while (ancestor && trail.length < ordered.length) {
    trail.unshift(ancestor);
    ancestor = ordered.find(q => q.id === ancestor.parent_id)!;
  }
  function update(patch: Partial<Answer>) {
    if (!question) return;
    const next = { ...answers, [question.id]: { question_id: question.id, option_id: null, text: '', ...answer, ...patch } };
    setAnswers(next); localStorage.setItem(storageKey, JSON.stringify(next));
  }
  const button = 'inline-flex items-center justify-center gap-2 rounded-full border border-(--border-subtle) px-4 h-8 text-xs font-semibold text-(--text-primary) hover:bg-(--surface-2) disabled:opacity-40';
  return <section className="agent-form" data-form-id={form.form_id} aria-label={form.title}>
    <header className="flex items-center gap-3 mb-3"><ListTree size={18} className="text-(--accent-solid)" /><h2 className="flex-1 text-sm font-semibold">{form.title}</h2><span className="text-xs text-(--text-muted)">{Math.min(step + 1, ordered.length)} / {ordered.length}</span><button type="button" className={button} aria-label="Dismiss form" title="Close form" disabled={disabled || sent} onClick={() => submit('dismissed')}><X size={15} />{!floating && 'Dismiss'}</button></header>
    <div className="h-1 rounded-full bg-(--surface-2) mb-4 overflow-hidden"><div className="h-full bg-(--accent-solid) transition-[width]" style={{ width: `${step / ordered.length * 100}%` }} /></div>
    {question ? <>
      <nav className="flex flex-wrap gap-1 text-[10px] text-(--text-muted) mb-2" aria-label="Question path">{trail.map((q, i) => <span key={q.id}>{i > 0 ? ' / ' : ''}{q.id}</span>)}</nav>
      <h3 className="text-sm font-semibold mb-3">{question.question}</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {question.options.map(option => <button key={option.id} type="button" aria-pressed={answer?.option_id === option.id} onClick={() => { update({ option_id: option.id }); setStep(value => value + 1); }} className={`rounded-xl border p-3 text-left transition-colors ${answer?.option_id === option.id ? 'border-(--accent-solid) bg-(--surface-2)' : 'border-(--border-subtle) hover:bg-(--surface-2)'}`}>
          <span className="flex items-center justify-between gap-2 text-xs font-semibold">{option.label}{answer?.option_id === option.id && <Check size={14} />}</span>
          {option.description && <span className="block mt-1 text-xs text-(--text-muted)">{option.description}</span>}
        </button>)}
      </div>
      <textarea aria-label="Your answer or additional detail" placeholder={question.options.length ? 'Or write your answer / add detail…' : 'Your answer…'} value={answer?.text ?? ''} onChange={event => update({ text: event.target.value })} className="input-field mt-3 text-xs resize-y min-h-16" />
    </> : <div>
      <div className="mb-3">
        <h3 className="text-sm font-semibold">Review your answers</h3>
        <p className="mt-1 text-xs text-(--text-muted)">Follow each branch below. Select an answer to edit it.</p>
      </div>
      <div className="agent-form-review-scroll">
        <ul className="agent-form-review-tree" aria-label="Answers by question branch">
          {(children.get(null) ?? []).map(root => <FormReviewBranch key={root.id} question={root} children={children} answers={answers} disabled={sent} onEdit={id => setStep(ordered.findIndex(q => q.id === id))} />)}
        </ul>
      </div>
    </div>}
    {error && <p role="alert" className="text-xs text-rose-400 mt-2">{error}</p>}
    <footer className="flex justify-between gap-3 mt-4">
      <button type="button" className={button} disabled={step === 0 || sent} onClick={() => setStep(value => value - 1)}><ArrowLeft size={13} />Back</button>
      {question ? <button type="button" className={button} disabled={!ready} onClick={() => setStep(value => value + 1)}>{step + 1 === ordered.length ? 'Review answers' : 'Next'}<ArrowRight size={13} /></button> : <button type="button" className="btn-primary rounded-full h-8 px-4 gap-2 text-xs" disabled={disabled || sent} onClick={() => {
        submit('answered');
      }}><Send size={13} />{sent ? 'Answers sent' : 'Send answers'}</button>}
    </footer>
  </section>;
}
