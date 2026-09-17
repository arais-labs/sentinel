import { useEffect, useId, useRef, useState } from 'react';
import { Loader2, Mic, Square, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { requestJson } from '../../lib/api';
import { captureVoice, deadInputMessage, ensureMicrophonePermission, MicrophoneAccessError } from '../../lib/voice-audio';
import { DictationBuffer } from '../../lib/dictation-audio';
import { notificationPublisher } from '../../lib/notifications';
import { useDictationStore } from '../../store/dictation-store';
import { useVoicePreferences } from '../../store/voice-preferences-store';
import { openVoiceSettings } from '../../lib/workspace-navigation';

type Recording = {
  controller: AbortController; buffer?: DictationBuffer; samples: number; inserted: boolean;
  pending: Promise<void>; accepting: boolean; meterAt: number;
  close?: () => void; lease?: string; heartbeat?: ReturnType<typeof setInterval>; timer?: ReturnType<typeof setTimeout>;
};
const notify = notificationPublisher('Dictation');

export function ComposerDictation({ instance, disabled, onText, onBusy }: {
  instance: string; disabled: boolean; onText: (text: string) => void; onBusy: (busy: boolean) => void;
}) {
  const id = useId();
  const navigate = useNavigate();
  const owner = useDictationStore(state => state.owner);
  const [phase, setPhase] = useState<'idle' | 'starting' | 'recording' | 'transcribing'>('idle');
  const [error, setError] = useState('');
  const [permission, setPermission] = useState(false);
  const [level, setLevel] = useState(0);
  const recording = useRef<Recording | null>(null);
  const callbacks = useRef({ onText, onBusy });
  callbacks.current = { onText, onBusy };
  const path = `/instances/${encodeURIComponent(instance)}/voice`;

  function showError(message: string) { setError(message); notify.error(message); }

  function dispose(run: Recording) {
    run.accepting = false;
    run.controller.abort(); run.close?.(); clearInterval(run.heartbeat); clearTimeout(run.timer);
    if (run.lease) {
      void requestJson(`${path}/runtime/leases/${run.lease}`, { method: 'DELETE' }).catch(() => {});
      run.lease = undefined;
    }
    if (recording.current === run) {
      recording.current = null;
      if (useDictationStore.getState().owner === id) useDictationStore.setState({ owner: null });
      callbacks.current.onBusy(false);
    }
  }
  function cancel() {
    if (recording.current) dispose(recording.current);
    setPhase('idle'); setLevel(0);
  }
  useEffect(() => () => { if (recording.current) dispose(recording.current); }, [instance]);
  useEffect(() => { if (disabled) cancel(); }, [disabled]);

  function transcribe(run: Recording, audio: Blob) {
    // Serialize chunks so words are appended in speaking order, never arrival order.
    run.pending = run.pending.then(async () => {
      if (run.controller.signal.aborted || recording.current !== run) return;
      const result = await requestJson<{ text: string }>(`${path}/transcribe?lease_id=${run.lease}`, {
        method: 'POST', rawBody: audio, signal: run.controller.signal, timeoutMs: 50_000,
      });
      if (run.controller.signal.aborted || recording.current !== run) return;
      if (result.text.trim()) {
        run.inserted = true;
        callbacks.current.onText(result.text.trim());
      }
    }).catch(err => {
      if (run.controller.signal.aborted || recording.current !== run) return;
      showError(err instanceof Error ? err.message : 'Could not transcribe speech.');
      dispose(run); setPhase('idle'); setLevel(0);
    });
  }
  async function finish(run: Recording) {
    if (recording.current !== run || !run.close) return;
    run.accepting = false;
    const close = run.close; run.close = undefined; close(); clearTimeout(run.timer);
    setPhase('transcribing'); setLevel(0);
    try {
      if (!run.samples) throw new Error('No microphone audio received. Check your input device and try again.');
      run.buffer?.flush();
      await run.pending;
      if (run.controller.signal.aborted || recording.current !== run) return;
      if (!run.inserted) throw new Error('No speech recognized. Check your microphone input and try again.');
    } catch (err) {
      if (!run.controller.signal.aborted) showError(err instanceof Error ? err.message : 'Could not transcribe speech.');
    } finally {
      if (recording.current === run) { dispose(run); setPhase('idle'); }
    }
  }
  async function start() {
    if (disabled || recording.current || useDictationStore.getState().owner) return;
    const run: Recording = { controller: new AbortController(), samples: 0, inserted: false, pending: Promise.resolve(), accepting: true, meterAt: 0 };
    recording.current = run;
    useDictationStore.setState({ owner: id });
    callbacks.current.onBusy(true); setPhase('starting'); setError(''); setPermission(false);
    const signal = run.controller.signal;
    try {
      await ensureMicrophonePermission(signal);
      const connection = await requestJson<{ lease_id: string }>(`${path}/runtime/connect`, { method: 'POST', signal, timeoutMs: 50_000 });
      run.lease = connection.lease_id;
      if (signal.aborted) { dispose(run); return; }
      run.heartbeat = setInterval(() => {
        void requestJson(`${path}/runtime/leases/${run.lease}`, { method: 'POST', signal }).catch(() => {
          if (!signal.aborted) { showError('Speech connection lost. Try again.'); cancel(); }
        });
      }, 20_000);
      run.close = await captureVoice(signal, (frame, rate) => {
        if (!run.accepting) return;
        // Bound memory and stay below the recognition endpoint's 30-second limit.
        const remaining = Math.max(0, Math.floor(25 * rate - run.samples));
        const samples = frame.slice(0, remaining);
        if (!samples.length) return;
        run.samples += samples.length;
        run.buffer ??= new DictationBuffer(rate, audio => transcribe(run, audio));
        const rms = run.buffer.push(samples);
        if (performance.now() - run.meterAt > 100) {
          run.meterAt = performance.now(); setLevel(Math.min(1, rms * 35));
        }
      }, {
        deviceId: useVoicePreferences.getState().microphone || undefined,
        onInputHealth: (state, label) => { if (state === 'dead') showError(deadInputMessage(label)); else setError(''); },
      });
      if (signal.aborted) { dispose(run); return; }
      setPhase('recording');
      run.timer = setTimeout(() => void finish(run), 25_000);
    } catch (err) {
      if (!signal.aborted) {
        setPermission(err instanceof MicrophoneAccessError);
        showError(err instanceof MicrophoneAccessError
          ? 'Allow microphone access in your system or browser privacy settings, then try again.'
          : err instanceof Error ? err.message : 'Could not start dictation.');
      }
      if (recording.current === run) { dispose(run); setPhase('idle'); }
    }
  }
  const busy = phase !== 'idle';
  const label = phase === 'recording' ? 'Stop dictation (25-second limit)' : phase === 'starting' ? 'Starting microphone…' : phase === 'transcribing' ? 'Transcribing…' : 'Dictate into message';
  return <div className="composer-dictation">
    <button type="button" className="chat-composer-attach composer-dictation-button" data-recording={phase === 'recording' || undefined}
      title={label} aria-label={label} aria-pressed={phase === 'recording'}
      disabled={disabled || (owner !== null && owner !== id) || phase === 'starting' || phase === 'transcribing'}
      onClick={() => phase === 'recording' && recording.current ? void finish(recording.current) : void start()}>
      {phase === 'starting' || phase === 'transcribing' ? <Loader2 size={16} className="animate-spin" /> : phase === 'recording' ? <Square size={12} fill="currentColor" /> : <Mic size={16} />}
    </button>
    {busy && <>{phase === 'recording' && <span className="composer-dictation-meter" role="meter" aria-label="Microphone input level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(level * 100)}><i style={{ transform: `scaleX(${level})` }} /></span>}<span className="composer-dictation-status" role="status">{phase === 'recording' ? 'Listening…' : label}</span><button type="button" className="chat-composer-attach" title="Cancel dictation" aria-label="Cancel dictation" onClick={cancel}><X size={13} /></button></>}
    {error && <div className="composer-dictation-error" role="alert"><span>{error}</span>
      {permission && window.sentinelDesktop?.openMicrophoneSettings && <button type="button" onClick={() => void window.sentinelDesktop!.openMicrophoneSettings!().catch(() => setError('Open system privacy settings → Microphone to allow access.'))}>Microphone settings</button>}
      {!permission && <button type="button" onClick={() => openVoiceSettings(navigate, instance)}>Voice setup</button>}
      <button type="button" aria-label="Dismiss dictation error" onClick={() => setError('')}><X size={13} /></button>
    </div>}
  </div>;
}
