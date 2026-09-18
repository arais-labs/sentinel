import { useContext, useEffect, useRef, useState } from 'react';
import { AudioLines, Mic, MicOff, Power, RefreshCw, ShieldCheck, Square, Volume2, VolumeX } from 'lucide-react';
import { createPortal } from 'react-dom';
import { InstanceClouds } from '../components/ui/InstanceClouds';
import { VoiceOrbitText } from '../components/VoiceOrbitText';
import { VoiceActivity, type VoiceToolCall } from '../components/VoiceActivity';
import { mergeStreamingToolArguments, serializeToolArguments } from '../lib/tool-arguments';
import { ApprovalActions } from '../components/session/ApprovalActions';
import { requestJson } from '../lib/api';
import { approvalRefFromMetadata, type ApprovalRef, type ApprovalScope } from '../lib/approvals';
import { instanceColor } from '../lib/instance-appearance';
import { useVoiceWorkspace } from '../hooks/useVoiceWorkspace';
import { useSessionStream } from '../hooks/useSessionStream';
import { sendSessionStreamMessage } from '../lib/session-stream';
import { useInstanceName } from '../lib/workspace-context';
import { RetainedSessionContext } from '../lib/workspace-context-values';
import { useDictationStore } from '../store/dictation-store';
import { useMicrophone, useVoicePreferences, useVoiceModelSelection, useVoiceSpeed } from '../store/voice-preferences-store';
import { useVoiceSessionStore } from '../store/voice-session-store';
import { captureVoice, deadInputMessage, ensureMicrophonePermission, MicrophoneAccessError, VoiceInterruptionDetector, VoiceSegmenter } from '../lib/voice-audio';
import { speakLocal, SpokenSentences, VoiceSpeechQueue } from '../lib/voice-speech';
import { fetchVoiceSession, resolveVoiceApproval, stopVoiceRun, voiceMessagePayload, VOICE_SESSION_RESET } from '../lib/voice-session';
import { playVoiceCue, startThinkingSound } from '../lib/voice-cues';
import type { MicrophonePermission } from '../../../../desktop/sentinel/src/shared/ipc';
import type { WsEvent } from '../types/api';
import './voice-page.css';
import { useNavigate } from 'react-router-dom';
import { openVoiceSettings } from '../lib/workspace-navigation';

export type VoicePhase = 'connecting' | 'permission' | 'listening' | 'thinking' | 'speaking' | 'approval' | 'muted' | 'offline' | 'setup' | 'error';
type Phase = VoicePhase;
export interface VoiceStatus { ready: boolean; model: string | null; provider: string | null; provider_configured: boolean; issues: string[]; runtime: { installed: boolean; running: boolean; installing: boolean; phase: string; error: string; path: string } }
type Status = VoiceStatus;
const labels: Record<Phase, string> = { connecting: 'Connecting', permission: 'Enable your microphone', listening: 'I’m listening', thinking: 'Thinking', speaking: 'Speaking', approval: 'Approval needed', muted: 'Microphone muted', offline: 'Voice is disconnected', setup: 'Set up local voice', error: 'Voice needs attention' };
const ACTIVITY_ROWS = 5;

/** Approval state can ride on a tool result, an approval event, or bare metadata. */
function approvalFromEvent(event: WsEvent): ApprovalRef | null {
  const result = event.tool_result as { metadata?: unknown } | undefined;
  const candidates: unknown[] = [result?.metadata, event.metadata, event.approval ? { approval: event.approval } : null];
  for (const candidate of candidates) {
    if (candidate && typeof candidate === 'object') {
      const ref = approvalRefFromMetadata(candidate as Record<string, unknown>);
      if (ref) return ref;
    }
  }
  return null;
}

export function VoicePage({ active = true, panelOpen = true, onPhaseChange, onOpenSettings, controlsTarget }: { active?: boolean; panelOpen?: boolean; onPhaseChange?: (phase: VoicePhase) => void; onOpenSettings?: () => void; controlsTarget?: HTMLElement | null }) {
  const instance = useInstanceName();
  const navigate = useNavigate();
  const retained = useContext(RetainedSessionContext);
  const [foreground, setForeground] = useState(!document.hidden);
  const visible = active && (retained?.visible ?? true) && foreground;
  const [appearance, setAppearance] = useState<{ instance: string; color: string } | null>(null);
  const smokeColor = appearance && appearance.instance === instance ? appearance.color : instanceColor(instance ?? '');
  useEffect(() => {
    if (!instance || !panelOpen) return;
    let disposed = false;
    void requestJson<{ database_name: string; appearance?: { color?: string | null } }>(`/instances/${encodeURIComponent(instance)}`)
      .then(value => {
        if (!disposed) setAppearance({ instance, color: value.appearance?.color || instanceColor(value.database_name) });
      })
      .catch(() => { /* Keep the default instance palette if appearance is unavailable. */ });
    return () => { disposed = true; };
  }, [instance, panelOpen]);
  const [enabled, setEnabled] = useState(true);
  useVoiceWorkspace(instance, visible && enabled);
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState<Phase>('connecting');
  useEffect(() => { onPhaseChange?.(phase); }, [phase, onPhaseChange]);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState('');
  const [level, setLevel] = useState(0);
  const [heard, setHeard] = useState('');
  const [reply, setReply] = useState('');
  const [muted, setMuted] = useState(false);
  const [spoken, setSpoken] = useState(true);
  const voiceSelection = useVoiceModelSelection(instance ?? '');
  const speechSpeed = useVoiceSpeed(instance ?? '');
  const microphoneDevice = useMicrophone();
  const cues = useVoicePreferences(state => state.cues);
  const [microphone, setMicrophone] = useState<MicrophonePermission | null>(null);
  const [microphoneBlocked, setMicrophoneBlocked] = useState(false);
  const [openingSettings, setOpeningSettings] = useState(false);
  const awaitingSettings = useRef(false);
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [resetTick, setResetTick] = useState(0);
  const [approval, setApproval] = useState<ApprovalRef | null>(null);
  const [tools, setTools] = useState<VoiceToolCall[]>([]);
  const [resolvingApproval, setResolvingApproval] = useState(false);
  const approvalRef = useRef<ApprovalRef | null>(null);
  approvalRef.current = approval;
  const current = useRef({ muted, spoken, voiceSelection, speechSpeed, cues });
  current.current = { muted, spoken, voiceSelection, speechSpeed, cues };
  const interrupt = useRef<() => void>(() => {});
  const silence = useRef<() => void>(() => {});
  const streamHandler = useRef<(event: WsEvent) => void>(() => {});
  // A faint hum while Voice thinks, fading out when it speaks, listens or waits for approval.
  useEffect(() => {
    if (phase !== 'thinking' || !cues) return;
    return startThinkingSound();
  }, [phase, cues]);
  const dictationActive = useDictationStore(state => state.owner !== null);
  useEffect(() => { if (dictationActive) interrupt.current(); }, [dictationActive]);
  const path = `/instances/${encodeURIComponent(instance ?? '')}/voice`;
  useEffect(() => {
    const refresh = () => setAttempt(value => value + 1);
    window.addEventListener('sentinel:voice-runtime-changed', refresh);
    return () => window.removeEventListener('sentinel:voice-runtime-changed', refresh);
  }, []);

  // The Voice conversation is one hidden session per instance; Reset replaces it.
  useEffect(() => {
    if (!instance) return;
    const controller = new AbortController();
    setVoiceSessionId(null);
    fetchVoiceSession(instance, controller.signal).then(id => { if (!controller.signal.aborted) { setVoiceSessionId(id); useVoiceSessionStore.getState().setSessionId(instance, id); } })
      .catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'The Voice conversation is unavailable.'); });
    return () => controller.abort();
  }, [instance, resetTick]);
  useEffect(() => {
    const onReset = (event: Event) => {
      const detail = (event as CustomEvent<{ instance: string }>).detail;
      if (detail?.instance !== instance) return;
      interrupt.current();
      setHeard(''); setReply(''); setError(''); setApproval(null);
      setResetTick(value => value + 1);
    };
    window.addEventListener(VOICE_SESSION_RESET, onReset);
    return () => window.removeEventListener(VOICE_SESSION_RESET, onReset);
  }, [instance]);
  useSessionStream(instance ?? null, visible && enabled ? voiceSessionId : null, { onEvent: event => streamHandler.current(event) });

  const retry = () => { setEnabled(true); setAttempt(value => value + 1); };
  const openMicrophoneSettings = async () => {
    setOpeningSettings(true); setError('');
    try { await window.sentinelDesktop?.openMicrophoneSettings(); awaitingSettings.current = true; }
    catch { setError('Could not open settings. Open your system privacy settings and select Microphone.'); }
    finally { setOpeningSettings(false); }
  };
  const resolveApproval = async (decision: 'approve' | 'reject', scope: ApprovalScope = 'once') => {
    if (!instance || !approval) return;
    setResolvingApproval(true); setError('');
    try { await resolveVoiceApproval(instance, approval, decision, scope); setApproval(null); }
    catch (err) { setError(err instanceof Error ? err.message : 'Could not record the decision.'); }
    finally { setResolvingApproval(false); }
  };

  useEffect(() => {
    if (!microphoneBlocked || !visible) return;
    let disposed = false;
    const check = () => {
      if (!awaitingSettings.current) return;
      void window.sentinelDesktop?.getMicrophonePermission?.().then(permission => {
        if (disposed) return;
        setMicrophone(permission);
        if (permission.status === 'granted') { awaitingSettings.current = false; setEnabled(true); setAttempt(value => value + 1); }
      }).catch(() => {});
    };
    window.addEventListener('focus', check);
    check();
    return () => { disposed = true; window.removeEventListener('focus', check); };
  }, [microphoneBlocked, visible]);

  useEffect(() => {
    const update = () => setForeground(!document.hidden);
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  // Installation belongs to the app and continues if this pane is closed.
  useEffect(() => {
    if (!visible || !status?.runtime.installing) return;
    let disposed = false;
    const timer = setInterval(() => {
      void requestJson<Status>(`${path}/status`).then(data => {
        if (disposed) return;
        setStatus(data);
        if (data.ready) { setEnabled(true); setAttempt(value => value + 1); }
      }).catch(() => {});
    }, 2000);
    return () => { disposed = true; clearInterval(timer); };
  }, [path, visible, status?.runtime.installing]);

  useEffect(() => {
    if (!visible || !enabled || !instance) { setPhase('offline'); return; }
    if (!voiceSessionId) { setPhase('connecting'); return; }
    const sessionId = voiceSessionId;
    const controller = new AbortController();
    const signal = controller.signal;
    let close: (() => void) | undefined;
    let segmenter: VoiceSegmenter | undefined;
    let interruptionDetector: VoiceInterruptionDetector | undefined;
    let capturingInterruption = false;
    let pendingUtterance: Blob | undefined;
    let busy = false;
    let running = false;
    let ending: Promise<void> | undefined;
    let awaitingRun: ReturnType<typeof setTimeout> | undefined;
    let speechController: AbortController | undefined;
    let speechQueue: VoiceSpeechQueue | undefined;
    let sentences: SpokenSentences | undefined;
    let playback: Promise<void> | undefined;
    let replyText = '';
    let silenced = false;
    let lease: string | undefined;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    const releaseConnection = () => {
      close?.(); clearInterval(heartbeat); clearTimeout(awaitingRun);
      const released = lease; lease = undefined;
      if (released) void requestJson(`${path}/runtime/leases/${released}`, { method: 'DELETE', timeoutMs: 50_000 }).catch(() => {});
    };
    const idlePhase = (): Phase => approvalRef.current ? 'approval' : current.current.muted ? 'muted' : 'listening';
    const stopSpeaking = () => {
      silenced = true;
      speechQueue?.close(true);
      speechController?.abort();
    };
    silence.current = stopSpeaking;
    interrupt.current = () => { stopSpeaking(); if (running) void stopVoiceRun(instance, sessionId).catch(() => {}); };
    const say = async (text: AsyncIterable<string> | string) => {
      if (!current.current.spoken || !lease || signal.aborted || useDictationStore.getState().owner) return;
      interruptionDetector?.reset();
      speechController = new AbortController();
      try {
        await speakLocal(path, lease, text, speechController.signal, current.current.speechSpeed, undefined,
          caption => { if (!signal.aborted) setReply(caption); },
          playing => { if (!signal.aborted) setPhase(value => value === 'error' || value === 'approval' ? value : playing ? 'speaking' : running ? 'thinking' : idlePhase()); });
      }
      catch (err) {
        silenced = true;
        if (!signal.aborted && !speechController.signal.aborted) setError('Kokoro playback is unavailable. The reply is shown on screen.');
        void err;
      } finally { speechController.abort(); speechController = undefined; }
    };
    const beginRun = () => {
      if (running || signal.aborted) return;
      running = true; busy = true; silenced = false; replyText = '';
      clearTimeout(awaitingRun);
      if (current.current.cues) playVoiceCue('thinking');
      setReply(''); setError(''); setTools([]);
      setPhase(value => value === 'speaking' ? value : 'thinking');
      const queue = new VoiceSpeechQueue();
      speechQueue = queue;
      sentences = new SpokenSentences(sentence => {
        replyText = replyText ? `${replyText} ${sentence}` : sentence;
        // Spoken replies caption each sentence as it is voiced; silent replies show the text as it arrives.
        if (silenced || !current.current.spoken || signal.aborted) { setReply(replyText); return; }
        queue.push(sentence);
        playback ??= say(queue).finally(() => { playback = undefined; });
      });
    };
    const endRun = () => {
      if (!running) return;
      ending ??= (async () => {
        sentences?.flush();
        speechQueue?.close();
        await playback;
        running = false; busy = false; sentences = undefined; speechQueue = undefined; ending = undefined;
        // Playback cancellation must not erase the phrase that interrupted it.
        if (!capturingInterruption) segmenter?.reset();
        if (!signal.aborted) setPhase(value => value === 'error' ? value : idlePhase());
        const next = pendingUtterance; pendingUtterance = undefined;
        if (next && lease && !signal.aborted && !current.current.muted) void send(next);
      })();
    };
    const settleTool = (result: { tool_call_id?: string; is_error?: boolean; content?: unknown } | undefined, fallbackId = '') => {
      const id = String(result?.tool_call_id || fallbackId);
      setTools(list => list.map(item => item.id === id && item.status === 'running'
        ? { ...item, status: result?.is_error ? 'error' : 'done', isError: Boolean(result?.is_error), outputJson: serializeToolArguments(result?.content), elapsedMs: Date.now() - item.startedAt } satisfies VoiceToolCall
        : item));
    };
    streamHandler.current = event => {
      if (signal.aborted) return;
      const ref = approvalFromEvent(event);
      if (ref) {
        if (ref.pending) {
          if (approvalRef.current?.approvalId !== ref.approvalId) {
            setApproval(ref); setPhase('approval');
            void say(`This needs your approval: ${ref.label ?? ref.action ?? 'an action'}.`);
          }
        } else if (approvalRef.current?.approvalId === ref.approvalId) setApproval(null);
      }
      switch (event.type) {
        case 'run_state': if (event.run_active) beginRun(); else endRun(); break;
        case 'agent_thinking': beginRun(); break;
        case 'text_delta': beginRun(); if (typeof event.delta === 'string') sentences?.push(event.delta); break;
        case 'done': if (event.stop_reason !== 'tool_use') endRun(); break;
        case 'toolcall_start': {
          const call = event.tool_call as { id?: string; name?: string; arguments?: unknown } | undefined;
          const id = String(call?.id ?? `${Date.now()}`);
          const entry: VoiceToolCall = { id, name: call?.name ?? 'tool', argumentsJson: serializeToolArguments(call?.arguments), outputJson: '', isError: false, status: 'running', startedAt: Date.now() };
          // Newest on top; rows beyond the visible count slide out before they are dropped.
          setTools(list => {
            const next = [entry, ...list.filter(item => item.id !== id && !item.leaving)];
            return [...next.slice(0, ACTIVITY_ROWS), ...next.slice(ACTIVITY_ROWS).map(item => ({ ...item, leaving: true }))];
          });
          window.setTimeout(() => setTools(list => list.filter(item => !item.leaving)), 440);
          break;
        }
        case 'toolcall_delta': {
          const delta = typeof event.delta === 'string' ? event.delta : '';
          if (delta) setTools(list => { const index = list.findIndex(item => item.status === 'running'); return index < 0 ? list : list.map((item, at) => at === index ? { ...item, argumentsJson: mergeStreamingToolArguments(item.argumentsJson, delta) } : item); });
          break;
        }
        case 'toolcall_end': {
          const call = event.tool_call as { id?: string; name?: string; arguments?: unknown } | undefined;
          const id = String(call?.id ?? '');
          const complete = serializeToolArguments(call?.arguments);
          setTools(list => list.map(item => item.id === id ? { ...item, name: call?.name ?? item.name, argumentsJson: complete.trim() && complete.trim() !== '{}' ? complete : item.argumentsJson } : item));
          if (event.tool_result) settleTool(event.tool_result as { tool_call_id?: string; is_error?: boolean; content?: unknown }, id);
          break;
        }
        case 'tool_result': settleTool(event.tool_result as { tool_call_id?: string; is_error?: boolean; content?: unknown } | undefined); break;
        case 'error': case 'agent_error':
          setError(String(event.error ?? event.message ?? 'Voice request failed.'));
          endRun();
          break;
      }
    };
    const send = async (audio: Blob) => {
      if (busy || signal.aborted || current.current.muted || useDictationStore.getState().owner) return;
      capturingInterruption = false;
      pendingUtterance = undefined;
      busy = true; setPhase('thinking'); setError(''); setLevel(0); setHeard(''); setReply('');
      if (current.current.cues) playVoiceCue('heard');
      try {
        const { text } = await requestJson<{ text: string; trace_id?: string | null }>(`${path}/transcribe?lease_id=${lease}`, { method: 'POST', rawBody: audio, signal, timeoutMs: 50_000 });
        if (!text.trim() || signal.aborted || useDictationStore.getState().owner) { busy = false; if (!signal.aborted) setPhase(idlePhase()); return; }
        setHeard(text);
        const { tier, provider_id, reasoning_level, fast_mode } = current.current.voiceSelection;
        const sent = sendSessionStreamMessage(instance, sessionId, voiceMessagePayload(text, { tier, provider_id, reasoning_level, fast_mode }));
        if (!sent) throw new Error('Voice is not connected to its conversation yet. Try again in a moment.');
        // The run announces itself over the stream; give up waiting if it never starts.
        awaitingRun = setTimeout(() => { if (!running && !signal.aborted) { busy = false; setError('Voice did not start a reply.'); setPhase(idlePhase()); } }, 20_000);
      } catch (err) {
        busy = false;
        if (!signal.aborted && !(err instanceof DOMException && err.name === 'AbortError')) {
          setError(err instanceof Error ? err.message : 'Voice request failed.');
          setPhase(idlePhase());
        }
      } finally {
        if (!busy && !capturingInterruption) segmenter?.reset();
      }
    };
    setPhase('connecting'); setError(''); setMicrophoneBlocked(false);
    void (async () => {
      try {
        const data = await requestJson<Status>(`${path}/status`, { signal });
        if (signal.aborted) return;
        setStatus(data);
        if (!data.ready) { setPhase('setup'); return; }
        setPhase('permission');
        await ensureMicrophonePermission(signal);
        if (signal.aborted) return;
        setPhase('connecting');
        const connection = await requestJson<{ lease_id: string }>(`${path}/runtime/connect`, { method: 'POST', signal, timeoutMs: 50_000 });
        lease = connection.lease_id;
        if (signal.aborted) { void requestJson(`${path}/runtime/leases/${lease}`, { method: 'DELETE' }).catch(() => {}); return; }
        heartbeat = setInterval(() => {
          void requestJson(`${path}/runtime/leases/${lease}`, { method: 'POST', signal }).catch(() => {
            if (!signal.aborted) { setError('The voice connection expired. Reconnect to continue.'); setEnabled(false); }
          });
        }, 20_000);
        close = await captureVoice(signal, (frame, rate) => {
          if (current.current.muted || useDictationStore.getState().owner) {
            segmenter?.reset(); interruptionDetector?.reset();
            capturingInterruption = false; pendingUtterance = undefined;
            setLevel(0); return;
          }
          segmenter ??= new VoiceSegmenter(rate, audio => {
            // The cancelled playback promise may still be unwinding. Keep one
            // complete interruption and dispatch it after that turn settles.
            if (busy && capturingInterruption) pendingUtterance = audio;
            else void send(audio);
          });
          if (speechController && !speechController.signal.aborted) {
            interruptionDetector ??= new VoiceInterruptionDetector(rate);
            const onset = interruptionDetector.push(frame);
            if (!onset) return;
            capturingInterruption = true;
            interrupt.current();
            setReply(''); setHeard(''); setPhase('listening');
            segmenter.reset();
            for (const buffered of onset) segmenter.push(buffered);
          } else {
            if (busy && !capturingInterruption) { segmenter.reset(); setLevel(0); return; }
            setLevel(Math.min(1, segmenter.push(frame) * 8));
          }
        }, {
          deviceId: microphoneDevice || undefined,
          onInputHealth: (state, label) => setError(state === 'dead' ? deadInputMessage(label) : ''),
        });
        if (signal.aborted) { close(); return; }
        setPhase(idlePhase());
      } catch (err) {
        releaseConnection();
        if (!signal.aborted) {
          if (err instanceof MicrophoneAccessError) {
            setMicrophone(err.permission ?? null); setMicrophoneBlocked(true); setPhase('permission');
          } else { setError(err instanceof Error ? err.message : 'Could not open the microphone.'); setPhase('error'); }
        }
      }
    })();
    return () => {
      controller.abort(); releaseConnection(); stopSpeaking();
      streamHandler.current = () => {};
      interrupt.current = () => {}; silence.current = () => {}; setLevel(0);
    };
  }, [instance, path, visible, enabled, attempt, microphoneDevice, voiceSessionId]);

  const connected = ['listening', 'thinking', 'speaking', 'approval', 'muted'].includes(phase);
  const compact = controlsTarget !== undefined;
  const canDisconnect = connected || phase === 'connecting' || phase === 'permission';
  const controls = <div className="voice-controls-dock">
    {phase === 'speaking' && <button className="voice-interrupt" aria-label="Interrupt reply" title="Interrupt reply" onClick={() => interrupt.current()}>{compact ? <Square size={14} /> : 'Interrupt reply'}</button>}
    <div className="voice-controls" role="group" aria-label="Voice controls">
      <button disabled={!connected} aria-label={muted ? 'Unmute microphone' : 'Mute microphone'} title={muted ? 'Unmute microphone' : 'Mute microphone'} aria-pressed={muted} onClick={() => { const next = !muted; setMuted(next); current.current.muted = next; setPhase(value => ['listening', 'muted'].includes(value) ? next ? 'muted' : 'listening' : value); }}>{muted ? <MicOff size={20} /> : <Mic size={20} />}</button>
      <button className="voice-connect" aria-label={canDisconnect ? 'Disconnect' : 'Connect'} title={canDisconnect ? 'Disconnect Voice' : 'Connect Voice'} onClick={() => { if (canDisconnect) setEnabled(false); else { setEnabled(true); setAttempt(value => value + 1); } }}><Power size={18} />{!compact && (canDisconnect ? 'Disconnect' : 'Connect')}</button>
      <button aria-label={spoken ? 'Disable spoken replies' : 'Enable spoken replies'} title={spoken ? 'Disable spoken replies' : 'Enable spoken replies'} aria-pressed={spoken} onClick={() => { setSpoken(!spoken); current.current.spoken = !spoken; if (spoken) silence.current(); }}>{spoken ? <Volume2 size={20} /> : <VolumeX size={20} />}</button>
    </div>
  </div>;
  return <div className="voice-pane">
    <div className="voice-heading"><span><AudioLines size={14} />VOICE</span><span className="voice-local-label">VOICE BRIDGE · LOCAL AUDIO</span></div>
    <div className="voice-stage">
      <div className="voice-orbit" data-phase={phase}>
        <InstanceClouds ring color={smokeColor} energy={phase === 'speaking' ? .7 : phase === 'thinking' ? .3 : level} paused={!visible || !panelOpen} />
        <AudioLines className="voice-waveform" size={36} strokeWidth={1.5} aria-hidden="true" />
      </div>
      <VoiceOrbitText label={labels[phase]} heard={connected ? heard : ''} reply={connected ? reply : ''} hold={phase === 'speaking'} />
      {connected && (tools.length > 0 || approval) && <div className="voice-activity-anchor"><div className="voice-activity-stack">
        {approval && <section className="voice-setup voice-approval" aria-labelledby="voice-approval-title">
          <div className="voice-permission-heading"><ShieldCheck size={18} /><h3 id="voice-approval-title">Approval needed</h3></div>
          <p>{approval.label ?? approval.action ?? 'An action'} is waiting for your decision.</p>
          <ApprovalActions busy={resolvingApproval} sessionId={voiceSessionId} action={approval.action} onResolve={(decision, scope) => void resolveApproval(decision, scope)} />
        </section>}
        {tools.length > 0 && <VoiceActivity calls={tools} />}
      </div></div>}
      {phase === 'permission' && <section className="voice-setup voice-microphone" aria-labelledby="voice-microphone-title">
        <div className="voice-permission-heading"><Mic size={18} /><h3 id="voice-microphone-title">{microphoneBlocked ? 'Microphone access' : 'Waiting for microphone access'}</h3></div>
        <p>Voice needs your microphone to hear requests. Use Disconnect to stop listening, even when this panel is closed.</p>
        {!microphoneBlocked ? <p role="status">Choose Allow in the system permission prompt to continue.</p> : <>
          {microphone?.status === 'restricted' ? <p>Microphone access is restricted by this device’s policy. Ask your administrator to enable it.</p> : microphone?.platform === 'darwin' ? <>
            <ol><li>Open System Settings → Privacy &amp; Security → Microphone.</li><li>Enable <strong>{microphone.appName}</strong>{microphone.appName === 'Electron' ? ' (the Sentinel development app)' : ''}.</li><li>Return here to connect. If macOS asks, quit and reopen the app first.</li></ol>
            <p className="voice-permission-hint">macOS may block access without showing another prompt. Changing the setting may require an app restart.</p>
          </> : microphone?.platform === 'win32' ? <p>In Windows microphone privacy settings, enable microphone access and “Let desktop apps access your microphone”, then try again.</p> : <p>Allow microphone access in your browser’s site settings and your system privacy settings, then try again.{window.sentinelDesktop ? ' If you just updated Sentinel, restart the desktop app to load its microphone permission support.' : ''}</p>}
          <div className="voice-permission-actions">
            {microphone?.canOpenSettings && microphone.status !== 'restricted' && <button disabled={openingSettings} onClick={() => void openMicrophoneSettings()}>{openingSettings ? 'Opening…' : 'Open microphone settings'}</button>}
            <button onClick={retry}><RefreshCw size={13} />{microphone?.status === 'not-determined' ? 'Allow microphone' : 'Try again'}</button>
          </div>
        </>}
        <button className="voice-permission-later" onClick={() => setEnabled(false)}>Not now</button>
      </section>}
      {error && <p className="voice-error" role="alert">{error}</p>}
      {phase === 'setup' && <section className="voice-setup"><h3>Local voice setup</h3>
        <p>Finish setup in <button type="button" className="voice-settings-link" onClick={() => { if (onOpenSettings) onOpenSettings(); else if (instance) openVoiceSettings(navigate, instance); }}>Settings → Voice</button>.</p>
        <button onClick={retry}><RefreshCw size={14} />Check again</button>
      </section>}
      {controlsTarget ? createPortal(controls, controlsTarget) : !compact && phase !== 'permission' ? controls : null}
    </div>
  </div>;
}
