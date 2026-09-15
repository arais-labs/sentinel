import { useEffect, useRef, useState } from 'react';
import { AgentForm, pendingForm, type FormResponse } from './AgentForm';
import { DesktopSocket } from '../../lib/desktop-socket';
import { wsSessionsBaseUrl } from '../../lib/env';
import { useThemeStore } from '../../store/theme-store';
import type { Message } from '../../types/api';

export function FormWindow() {
  const params = new URLSearchParams(window.location.search);
  const instance = params.get('instance') ?? '';
  const session = params.get('session') ?? '';
  const formId = params.get('form') ?? '';
  const [form, setForm] = useState<ReturnType<typeof pendingForm>>(null);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const content = useRef<HTMLElement>(null);
  const socket = useRef<DesktopSocket | null>(null);
  const submitted = useRef(false);
  const initializeTheme = useThemeStore(s => s.initializeTheme);

  useEffect(() => { initializeTheme(); }, [initializeTheme]);
  useEffect(() => {
    document.documentElement.classList.add('floating-form-root');
    const resize = () => {
      if (content.current) void window.sentinelDesktop?.resizeFormWindow(Math.ceil(content.current.getBoundingClientRect().height));
    };
    const observer = new ResizeObserver(resize);
    if (content.current) observer.observe(content.current);
    window.addEventListener('resize', resize);
    resize();
    return () => { window.removeEventListener('resize', resize); observer.disconnect(); document.documentElement.classList.remove('floating-form-root'); };
  }, []);
  useEffect(() => {
    let mounted = true;
    setError('');
    const channel = new DesktopSocket(`${wsSessionsBaseUrl(instance)}/${encodeURIComponent(session)}/stream`);
    socket.current = channel;
    channel.onmessage = event => {
      if (!mounted) return;
      const data = JSON.parse(String(event.data));
      if (data.type === 'connected') {
        setConnected(true);
        const pending = pendingForm((data.history ?? []) as Message[]);
        if (pending?.form_id === formId) setForm(pending);
        else void window.sentinelDesktop?.finishFormWindow(false);
      } else if (data.type === 'message_ack' && data.metadata?.form_response?.form_id === formId) {
        const answered = data.metadata.form_response.status === 'answered';
        submitted.current = answered;
        localStorage.removeItem(`sentinel.form.${formId}`);
        void window.sentinelDesktop?.finishFormWindow(answered);
      } else if (data.type === 'done' && submitted.current) {
        void window.sentinelDesktop?.finishFormWindow(false);
      } else if (data.type === 'error') {
        if (submitted.current) void window.sentinelDesktop?.finishFormWindow(false);
        else setError(data.error || data.message || 'Could not send your response.');
      }
    };
    channel.onclose = () => {
      if (!mounted) return;
      setConnected(false);
      if (submitted.current) void window.sentinelDesktop?.finishFormWindow(false);
      else setError('Connection lost. Your answers are saved.');
    };
    return () => { mounted = false; socket.current = null; channel.close(); };
  }, [instance, session, formId, attempt]);

  function submit(response: FormResponse) {
    if (socket.current?.readyState !== 1) {
      setError('Reconnect before sending your response.');
      return false;
    }
    socket.current.send(JSON.stringify({
      type: 'message', content: 'Form response', form_response: response,
      tier: localStorage.getItem('sentinel-selected-tier') || 'normal',
    }));
    return true;
  }

  useEffect(() => window.sentinelDesktop?.onFormCloseRequested(() => {
    submit({ form_id: formId, status: 'dismissed', answers: [] });
  }), [formId]);

  return <div className="floating-form-viewport"><main ref={content} className="floating-form-window">
    {error && <div role="alert" className="px-5 pt-3 text-sm text-rose-400">
      {error}
      {!connected && <button className="ml-3 underline" onClick={() => setAttempt(value => value + 1)}>Reconnect</button>}
    </div>}
    {form ? <AgentForm floating key={form.form_id} form={form} disabled={!connected} onSubmit={submit} /> :
      <p className="p-5 text-sm text-(--text-muted)">Loading form…</p>}
  </main></div>;
}
