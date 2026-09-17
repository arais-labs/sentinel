import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { AudioLines, RotateCcw, Settings2, X } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { createPortal } from 'react-dom';
import { WorkspaceProvider } from '../lib/workspace-context';
import { VoicePage, type VoicePhase } from '../pages/VoicePage';
import { openVoiceSettings } from '../lib/workspace-navigation';
import { resetVoiceSession } from '../lib/voice-session';
import { useVoiceSessionStore } from '../store/voice-session-store';
import { WorkspaceAttachment } from './session/WorkspaceAttachment';
import './top-bar-voice.css';

const statusLabels: Record<VoicePhase, string> = {
  offline: 'Paused', connecting: 'Connecting', permission: 'Microphone access needed',
  setup: 'Setup needed', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking',
  approval: 'Approval needed', muted: 'Microphone muted', error: 'Needs attention',
};

/** Lives above the workspace: changing or focusing a chat never owns Voice. */
export function TopBarVoice({ instanceName }: { instanceName: string }) {
  const [open, setOpen] = useState(false);
  const [visited, setVisited] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>('offline');
  const popover = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const island = useRef<HTMLDivElement>(null);
  const [controlsTarget, setControlsTarget] = useState<HTMLDivElement | null>(null);
  const id = useId();
  const voiceSessionId = useVoiceSessionStore(state => state.ids[instanceName] ?? null);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const element = island.current;
    const actions = element?.querySelector<HTMLElement>('.topbar-voice-island-actions');
    if (!element || !actions) return;
    const fit = () => element.style.setProperty('--voice-actions-width', `${Math.ceil(actions.getBoundingClientRect().width) + 3}px`);
    const observer = new ResizeObserver(fit);
    observer.observe(actions);
    fit();
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const node = popover.current;
    if (!node) return;
    const sync = () => {
      const showing = node.matches(':popover-open');
      setOpen(showing);
      if (showing) setVisited(true);
    };
    node.addEventListener('toggle', sync);
    return () => node.removeEventListener('toggle', sync);
  }, []);

  const dismiss = useCallback(() => { popover.current?.hidePopover(); trigger.current?.focus({ preventScroll: true }); }, []);
  // An approval must be visible to be decided; the overlay opens itself.
  useEffect(() => {
    const node = popover.current;
    if (phase === 'approval' && node && !node.matches(':popover-open')) node.showPopover();
  }, [phase]);
  const reset = () => { void resetVoiceSession(instanceName).catch(() => {}); };
  const showSettings = () => { dismiss(); openVoiceSettings(navigate, instanceName); };
  // The control island and the veil share a lifetime but live in separate DOM
  // surfaces. Native auto-dismiss would close the veil on an island click.
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !island.current?.contains(event.target) && !popover.current?.contains(event.target)) popover.current?.hidePopover();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); dismiss(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open, dismiss]);

  // Preserve old Voice bookmarks without reopening a workspace pane.
  useEffect(() => {
    const search = new URLSearchParams(location.search);
    if (search.get('voice') !== '1') return;
    popover.current?.showPopover();
    search.delete('voice');
    navigate({ pathname: location.pathname, search: search.toString(), hash: location.hash }, { replace: true });
  }, [location.pathname, location.search, location.hash, navigate]);

  return <>
    <div ref={island} className="topbar-voice-island" data-open={open} role="group" aria-label="Voice">
    <button ref={trigger} type="button" className="topbar-voice-trigger" data-phase={phase}
      aria-label={`Voice · ${statusLabels[phase]}`} aria-expanded={open} aria-haspopup="dialog" aria-controls={id}
      title={`Voice · ${statusLabels[phase]}`} onClick={() => {
        const node = popover.current;
        if (node?.matches(':popover-open')) dismiss(); else node?.showPopover();
      }}>
      <AudioLines size={24} strokeWidth={1.6} />
    </button>
    <div className="topbar-voice-island-actions" inert={!open} aria-hidden={!open}>
      <div ref={setControlsTarget} className="topbar-voice-controls-slot" />
      <WorkspaceAttachment compact className="topbar-voice-settings" sessionId={voiceSessionId} instanceName={instanceName} busy={phase === 'thinking' || phase === 'speaking'} />
      <button type="button" className="topbar-voice-settings" aria-label="Reset Voice conversation" title="Reset Voice conversation" onClick={reset}><RotateCcw size={16} /></button>
      <button type="button" className="topbar-voice-settings" aria-label="Voice settings" title="Voice settings" onClick={showSettings}><Settings2 size={16} /></button>
      <button type="button" className="topbar-voice-close" aria-label="Close Voice" title="Close Voice" onClick={dismiss}><X size={16} /></button>
    </div>
    </div>
    {createPortal(<div ref={popover} id={id} popover="manual" role="dialog" aria-label="Voice" className="topbar-voice-popover">
      {visited && <WorkspaceProvider instanceName={instanceName} workspaceMode={false}>
        <VoicePage panelOpen={open} onPhaseChange={setPhase} onOpenSettings={showSettings} controlsTarget={controlsTarget} />
      </WorkspaceProvider>}
    </div>, document.body)}
  </>;
}
