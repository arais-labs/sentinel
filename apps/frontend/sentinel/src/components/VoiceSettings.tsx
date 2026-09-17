import { type CSSProperties, useEffect, useId, useState } from 'react';
import { AudioLines, ChevronDown, Download, Minus, Plus, Trash2 } from 'lucide-react';
import { requestJson } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { useMicrophone, useVoicePreferences, useVoiceModelSelection, useVoiceSpeed, VOICE_SPEED } from '../store/voice-preferences-store';
import { listMicrophones, type MicrophoneDevice } from '../lib/voice-audio';
import { notificationPublisher } from '../lib/notifications';
import type { VoiceStatus } from '../pages/VoicePage';
import type { ModelOption } from '../types/api';
import { useModelCatalog } from '../hooks/useModelCatalog';
import { resolveModelSelection, type SessionModelChoice } from '../lib/model-selection';
import { ProviderLogo, providerLabel, SessionModelControls, SessionTierOptions } from './session/SessionModelControls';
import { StatusChip } from './ui/StatusChip';
import { VoiceTraceLog } from './VoiceTraceLog';
import './appearance.css';
import './voice-settings.css';
import './session/run-settings.css';
import './notifications.css';
import { playVoiceCue } from '../lib/voice-cues';

const notify = notificationPublisher('Voice');

export function VoiceSettings() {
  const instance = useInstanceName() ?? '';
  const path = `/instances/${encodeURIComponent(instance)}/voice`;
  const selection = useVoiceModelSelection(instance);
  const speed = useVoiceSpeed(instance);
  const speedId = useId();
  const microphoneId = useId();
  const microphone = useMicrophone();
  const cues = useVoicePreferences(state => state.cues);
  const cuesId = useId();
  const [microphones, setMicrophones] = useState<MicrophoneDevice[] | null>(null);
  const [microphoneError, setMicrophoneError] = useState('');
  useEffect(() => {
    let active = true;
    const refresh = () => void listMicrophones().then(devices => { if (active) { setMicrophones(devices); setMicrophoneError(''); } })
      .catch(err => { if (active) { setMicrophones([]); setMicrophoneError(err instanceof Error && err.name === 'NotAllowedError' ? 'Allow microphone access to list input devices.' : 'Could not list microphones.'); } });
    refresh();
    navigator.mediaDevices?.addEventListener?.('devicechange', refresh);
    return () => { active = false; navigator.mediaDevices?.removeEventListener?.('devicechange', refresh); };
  }, []);
  const selectedMissing = microphone !== '' && microphones !== null && !microphones.some(device => device.deviceId === microphone);
  const setSpeed = (value: number) => useVoicePreferences.getState().setSpeed(instance, value);
  const [modelExpanded, setModelExpanded] = useState(false);
  const modelControlsId = useId();
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const { models } = useModelCatalog(instance, true, modelExpanded);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const refresh = () => void requestJson<VoiceStatus>(`${path}/status`).then(data => {
      if (active) setStatus(data);
    }).catch(err => { if (active) setError(err.message); });
    refresh();
    const timer = setInterval(refresh, 2000);
    return () => { active = false; clearInterval(timer); };
  }, [instance, path]);
  const activeModel = models.find(model => model.tier === selection.tier);
  const { provider: selectedProvider } = resolveModelSelection(activeModel, selection);
  const selectModel = (tier: ModelOption['tier'], choice: SessionModelChoice) => {
    const resolved = resolveModelSelection(models.find(model => model.tier === tier), choice);
    useVoicePreferences.getState().setSelection(instance, { tier, ...resolved.choice });
  };
  const manage = async (remove: boolean) => {
    setBusy(true); setError('');
    try {
      await requestJson(`${path}/runtime${remove ? '' : '/install'}`, {method: remove ? 'DELETE' : 'POST', timeoutMs:60000});
      setStatus(await requestJson<VoiceStatus>(`${path}/status`));
      setConfirmRemove(false);
      window.dispatchEvent(new Event('sentinel:voice-runtime-changed'));
      notify.success(remove ? 'Local voice data removed. Chats were kept.' : 'Voice setup started. You can leave Settings.');
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not update local voice.'); }
    finally { setBusy(false); }
  };
  return <section className="app-appearance voice-settings" aria-label="Voice settings">
    <header className="app-appearance-page-heading"><h2>Voice</h2></header>
    <div className="app-appearance-panel">
      <div className="app-appearance-panel-heading"><AudioLines size={16} /><div><strong>Speech &amp; provider</strong></div></div>
      <div className="voice-model-setting">
        <button className="run-settings-section-toggle voice-model-toggle" aria-label="Model & reasoning" aria-expanded={modelExpanded} aria-controls={modelControlsId} disabled={!models.length} onClick={() => setModelExpanded(value => !value)}>
          <span className="voice-model-label">Model &amp; reasoning</span>
          <span className="voice-model-summary">
            {selectedProvider && <ProviderLogo id={selectedProvider.provider_id} />}
            <span className="voice-model-name" title={selectedProvider ? `${providerLabel(selectedProvider.provider_id)} · ${selectedProvider.model}` : undefined}>{selectedProvider?.model ?? (models.length ? 'Provider unavailable' : 'Loading…')}</span>
            <span className="voice-model-effort">{activeModel?.label ?? 'Fast'}{selection.reasoning_level ? ` · ${selection.reasoning_level}` : ''}</span>
          </span><ChevronDown size={13} className={modelExpanded ? 'rotate-180' : ''} />
        </button>
        {modelExpanded && <div id={modelControlsId} className="voice-model-details run-settings-options" role="region" aria-label="Voice model options">
          <SessionModelControls models={models} tier={selection.tier} choice={selection} disabled={false} onSelect={selectModel} allowDefaultProvider>
            <SessionTierOptions models={models} tier={selection.tier} choice={selection} disabled={false} compact onSelect={tier => selectModel(tier, selection)} />
          </SessionModelControls>
        </div>}
      </div>
      <div className="appearance-setting-row">
        <div className="appearance-setting-copy"><label htmlFor={microphoneId}>Microphone</label><span>{selectedMissing ? 'The saved microphone is not connected; the system default is used.' : microphoneError || 'Applies to Voice and composer dictation on this computer.'}</span></div>
        <div className="appearance-select-control">
          <select id={microphoneId} value={selectedMissing ? '' : microphone} disabled={microphones === null} onChange={event => useVoicePreferences.getState().setMicrophone(event.target.value)}>
            <option value="">System default</option>
            {microphones?.map(device => <option key={device.deviceId} value={device.deviceId}>{device.label}</option>)}
          </select><ChevronDown size={15} aria-hidden="true" />
        </div>
      </div>
      <div className="appearance-setting-row">
        <div className="appearance-setting-copy"><label htmlFor={speedId}>Speech speed</label><span>Applies to the next reply.</span></div>
        <div className="appearance-setting-control"><output htmlFor={speedId}>{speed.toFixed(2).replace(/0$/, '')}×</output><div className="appearance-size-control">
          <button type="button" aria-label="Slower speech" disabled={speed <= VOICE_SPEED.min} onClick={() => setSpeed(speed - VOICE_SPEED.step)}><Minus size={14} /></button>
          <input id={speedId} type="range" min={VOICE_SPEED.min} max={VOICE_SPEED.max} step={VOICE_SPEED.step} value={speed} aria-valuetext={`${speed} times normal speed`} style={{ '--appearance-size-progress': `${((speed - VOICE_SPEED.min) / (VOICE_SPEED.max - VOICE_SPEED.min)) * 100}%` } as CSSProperties} onChange={event => setSpeed(Number(event.target.value))} />
          <button type="button" aria-label="Faster speech" disabled={speed >= VOICE_SPEED.max} onClick={() => setSpeed(speed + VOICE_SPEED.step)}><Plus size={14} /></button>
        </div></div>
      </div>
      <div className="appearance-setting-row">
        <div className="appearance-setting-copy"><label htmlFor={cuesId}>Sound cues</label><span>A short tick when Voice hears you, a soft tone when it starts thinking.</span></div>
        <div className="voice-settings-actions"><button type="button" className="btn-secondary" aria-label="Preview sound cues" onClick={() => { playVoiceCue('heard'); window.setTimeout(() => playVoiceCue('thinking'), 500); }}>Preview</button>
          <input id={cuesId} type="checkbox" role="switch" className="voice-cues-switch" checked={cues} onChange={event => useVoicePreferences.getState().setCues(event.target.checked)} /></div>
      </div>
      <div className="appearance-setting-row"><div className="appearance-setting-copy"><strong>Speech engine</strong><span>{status?.runtime.installing ? `${status.runtime.phase}…` : 'Whisper + Kokoro · English · Heart'}</span></div>
        <div className="voice-settings-actions">{status && <StatusChip label={status.runtime.installing ? 'Installing' : status.runtime.installed ? 'Installed' : 'Not installed'} tone={status.runtime.installed ? 'good' : 'default'} />}
        {!status ? <span>Checking installation…</span> : !status.runtime.installed && !status.runtime.installing && <button className="btn-primary" aria-label="Install local speech engine" disabled={busy} onClick={() => void manage(false)}><Download size={14} />Install</button>}
        {(status?.runtime.installed || status?.runtime.installing) && !confirmRemove && <button className="btn-secondary voice-settings-danger" aria-label="Remove local voice data…" disabled={busy} onClick={() => setConfirmRemove(true)}><Trash2 size={14} />Remove</button>}</div>
      </div>
      {confirmRemove && <div className="voice-settings-maintenance" role="alertdialog" aria-label="Remove local voice data?"><div><p>This disconnects Voice and removes speech files for all instances. Chats, providers, and Ollama models are kept.</p><div className="voice-settings-actions"><button className="btn-secondary voice-settings-danger" disabled={busy} onClick={() => void manage(true)}><Trash2 size={14} />Remove local voice data</button><button className="btn-secondary" disabled={busy} onClick={() => setConfirmRemove(false)}>Cancel</button></div></div></div>}
      {status && <details className="voice-settings-files"><summary>Local files</summary><code>{status.runtime.path}</code></details>}
    </div>
    <VoiceTraceLog key={path} path={path} />
    {status?.runtime.error && <p className="voice-settings-danger" role="alert">{status.runtime.error}</p>}
    {error && <p className="text-xs text-(--status-error)" role="alert">{error}</p>}
    <footer className="voice-settings-note">Voice stays connected when the panel closes; use Disconnect to stop listening. Audio stays local; transcripts and recent chat activity go to your selected provider.</footer>
  </section>;
}
