import { Zap } from 'lucide-react';
import { ReasoningFluid, reasoningColor } from './ReasoningFluid';
import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type PointerEvent } from 'react';
import type { ModelOption } from '../../types/api';
import claudeLogo from '../../assets/provider-logos/claude.svg?raw';
import openaiLogo from '../../assets/provider-logos/openai.svg?raw';
import geminiLogo from '../../assets/provider-logos/gemini.svg?raw';
import './session-model-controls.css';

export type ReasoningLevel = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max' | 'ultra';
export type SessionModelChoice = { provider_id?: string; reasoning_level?: ReasoningLevel; fast_mode?: boolean };
export const providerLabel = (id?: string) => ({ anthropic: 'Claude', openai: 'OpenAI', 'openai-codex': 'Codex', gemini: 'Gemini' }[id ?? ''] ?? 'Default provider');
const levelLabel = (level: string) => level === 'xhigh' ? 'X-high' : level.charAt(0).toUpperCase() + level.slice(1);

export function ProviderLogo({ id }: { id?: string }) {
  const source = id === 'anthropic' ? claudeLogo : id === 'gemini' ? geminiLogo : openaiLogo;
  return <span aria-hidden="true" className="session-provider-logo" dangerouslySetInnerHTML={{ __html: source }} />;
}

function useFluidPosition(target: number) {
  const [position, setPosition] = useState(target);
  const motion = useRef({ position: target, velocity: 0 });
  useEffect(() => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    if (reduced.matches) {
      motion.current = { position: target, velocity: 0 };
      setPosition(target);
      return;
    }
    let frame = 0;
    let previous = performance.now();
    const tick = (now: number) => {
      const dt = Math.min((now - previous) / 1000, .032);
      previous = now;
      const state = motion.current;
      // Preserve momentum across pointer updates and release-to-stop changes.
      state.velocity += ((target - state.position) * 240 - state.velocity * 28) * dt;
      state.position += state.velocity * dt;
      if (Math.abs(target - state.position) < .0003 && Math.abs(state.velocity) < .002) {
        motion.current = { position: target, velocity: 0 };
        setPosition(target);
        return;
      }
      setPosition(Math.max(0, Math.min(1, state.position)));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target]);
  return position;
}

function Slider({ label, labels, value, disabled, defaultLabel, onSelect }: {
  label: string; labels: string[]; value: number; disabled: boolean; defaultLabel?: string; onSelect: (index: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  const fluidPosition = useFluidPosition(draft / Math.max(1, labels.length - 1));
  const latest = useRef(value);
  const dragging = useRef<number | null>(null);
  const stop = Math.round(draft);
  useEffect(() => { if (!disabled) { latest.current = value; setDraft(value); } }, [value, disabled]);
  const select = (index: number) => { if (index !== value || defaultLabel) onSelect(index); };
  const preview = (index: number) => { latest.current = index; setDraft(index); };
  const move = (event: PointerEvent<HTMLInputElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - 3) / Math.max(1, rect.width - 6)));
    preview(fraction * (labels.length - 1));
  };
  return <div className="session-model-slider">
    <div className="session-reasoning-range" style={{ '--range-fill': `${fluidPosition * 100}%`, '--liquid-glow': .08 + .65 * fluidPosition ** 1.5, '--liquid-hue': 210 * (1 - fluidPosition) } as CSSProperties}>
    <div className="session-liquid-aura" aria-hidden="true"><span /></div>
    <div className="session-reasoning-track" aria-hidden="true"><ReasoningFluid amount={fluidPosition} /></div>
    <input aria-label={label} aria-valuetext={labels[stop]} type="range" min={0} max={labels.length - 1} step="any" value={draft} disabled={disabled}
      onChange={event => preview(Number(event.target.value))}
      onPointerDown={event => {
        if (disabled || event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        dragging.current = event.pointerId;
        event.currentTarget.setPointerCapture(event.pointerId);
        move(event);
      }}
      onPointerMove={event => { if (dragging.current === event.pointerId) move(event); }}
      onPointerUp={event => {
        if (dragging.current !== event.pointerId) return;
        move(event);
        dragging.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
        const next = Math.round(latest.current);
        preview(next);
        select(next);
      }}
      onPointerCancel={() => { dragging.current = null; preview(value); }}
      onLostPointerCapture={() => { if (dragging.current !== null) { dragging.current = null; preview(value); } }}
      onKeyDown={event => {
        const offsets: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, PageDown: -1, ArrowRight: 1, ArrowUp: 1, PageUp: 1 };
        if (!(event.key in offsets) && event.key !== 'Home' && event.key !== 'End') return;
        event.preventDefault();
        preview(event.key === 'Home' ? 0 : event.key === 'End' ? labels.length - 1 : Math.max(0, Math.min(labels.length - 1, Math.round(latest.current) + offsets[event.key])));
      }}
      onKeyUp={event => { if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) select(Math.round(latest.current)); }} />
    </div>
    <div className="session-model-stops">{labels.map((text, index) => {
      return <button key={text} disabled={disabled} aria-pressed={index === stop} onClick={() => select(index)} style={{ '--level-color': reasoningColor(index / Math.max(1, labels.length - 1)) } as CSSProperties} className={index === stop ? 'is-selected' : ''}>
        {text}
      </button>;
    })}</div>
  </div>;
}

export function SessionModelControls({ models, tier, choice, disabled, onSelect, children }: {
  children: ReactNode; models: ModelOption[]; tier: ModelOption['tier']; choice: SessionModelChoice; disabled: boolean;
  onSelect: (tier: ModelOption['tier'], choice: SessionModelChoice) => void;
}) {
  const active = models.find(model => model.tier === tier);
  const options = active?.provider_options ?? [];
  const provider = options.find(option => option.provider_id === choice.provider_id) ?? options[0];
  const levels = (provider?.reasoning_levels ?? []) as ReasoningLevel[];
  const level = choice.reasoning_level && levels.includes(choice.reasoning_level) ? choice.reasoning_level : levels.includes(provider?.reasoning_effort as ReasoningLevel) ? provider!.reasoning_effort as ReasoningLevel : levels.includes('medium') ? 'medium' : levels[0];
  return <div className="session-model-controls">
    <div className="session-model-providers">{options.map(option => <button key={option.provider_id} disabled={disabled} aria-pressed={provider?.provider_id === option.provider_id}
      onClick={() => { if (provider?.provider_id !== option.provider_id) onSelect(tier, { ...choice, provider_id: option.provider_id, fast_mode: option.supports_fast_mode ? choice.fast_mode : false, reasoning_level: option.reasoning_levels.includes(choice.reasoning_level ?? '') ? choice.reasoning_level : undefined }); }}
      className={provider?.provider_id === option.provider_id ? 'is-selected' : ''}>
      <ProviderLogo id={option.provider_id} />{providerLabel(option.provider_id)}
    </button>)}</div>
    {children}
    {provider?.reasoning_levels.length ? <Slider key={`${provider.provider_id}:${provider.model}`} label="Reasoning" labels={levels.map(levelLabel)} value={Math.max(0, levels.indexOf(level))} defaultLabel={!choice.reasoning_level && !provider.reasoning_effort ? 'Provider default' : undefined} disabled={disabled} onSelect={index => onSelect(tier, { ...choice, provider_id: provider.provider_id, reasoning_level: levels[index] })} /> : <p className="session-model-default">This model manages its own reasoning.</p>}
    {provider?.supports_fast_mode && <button type="button" role="switch" aria-label="Fast mode" title={provider.provider_id === 'anthropic' ? 'Faster processing at higher usage cost. Requires fast-mode access from Claude.' : 'Faster processing at higher usage cost.'} aria-checked={!!choice.fast_mode} disabled={disabled}
      className={`session-fast-mode${choice.fast_mode ? ' is-enabled' : ''}`}
      onClick={() => onSelect(tier, { ...choice, provider_id: provider.provider_id, fast_mode: !choice.fast_mode })}>
      <Zap size={12} aria-hidden="true" /><span className="session-fast-mode-label">Fast mode</span><span className="session-fast-mode-note">Higher usage</span>
      <span className="session-fast-mode-toggle" aria-hidden="true"><span /></span>
    </button>}
  </div>;
}
