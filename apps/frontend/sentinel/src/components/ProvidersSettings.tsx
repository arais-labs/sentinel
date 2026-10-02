import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { ArrowLeft, Bot, GripVertical, Loader2, Settings2 } from 'lucide-react';
import { api } from '../lib/api';
import { StatusChip } from './ui/StatusChip';
import { Toggle } from './ui/Toggle';
import { ProviderModelSettings } from './ProviderModelSettings';
import { ProviderUsage } from './ProviderUsage';
import { ModelTierIdentity, modelTiers } from './ui/ModelTierIdentity';
import { ProviderLogo } from './ui/ProviderLogo';
import { useInstanceName } from '../lib/workspace-context';
import { providersChanged } from '../hooks/useModelCatalog';

export type Provider = 'anthropic' | 'openai' | 'gemini' | 'ollama';
export interface ProviderStatus {
  configured: boolean;
  auth_method: 'oauth' | 'api_key' | null;
  auth_source: 'manual' | 'cli' | null;
  masked_key: string | null;
  models: Record<'fast' | 'normal' | 'hard', string>;
}
interface ProviderRouting { order: Provider[]; automatic: Provider[] }
export interface ProvidersStatusResponse {
  primary_provider: string;
  routing: ProviderRouting;
  providers: Record<Provider, ProviderStatus>;
}
const names: Record<Provider, string> = { anthropic: 'Anthropic', openai: 'OpenAI', gemini: 'Google Gemini', ollama: 'Ollama' };
const cliNames = { anthropic: 'Claude CLI', openai: 'Codex CLI', gemini: 'Antigravity' };

function modelLabel(model: string) {
  const claude = /^claude-(sonnet|opus|haiku|fable|mythos)-(\d+)-(\d+)$/.exec(model);
  if (claude) return `${claude[1][0].toUpperCase()}${claude[1].slice(1)} ${claude[2]}.${claude[3]}`;
  const gpt = /^gpt-([\d.]+)-(luna|sol|astra)$/.exec(model);
  if (gpt) return `GPT-${gpt[1]} ${gpt[2][0].toUpperCase()}${gpt[2].slice(1)}`;
  return model || '—';
}

/** Cards own routing order; connection and model editing live in the detail view. */
export function ProvidersSettings({ status, loading, active, renderConnection, onRemove, onChanged, onRoutingChanged, onRetry }: {
  status: ProvidersStatusResponse | null;
  loading: boolean;
  active: boolean;
  renderConnection: (provider: Provider) => ReactNode;
  onRemove: (provider: Provider) => Promise<void>;
  onChanged: () => Promise<void>;
  onRoutingChanged: () => Promise<boolean>;
  onRetry: () => void;
}) {
  const instance = useInstanceName();
  const [selected, setSelected] = useState<Provider | null>(null);
  const [draft, setDraft] = useState<ProviderRouting | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [confirmRemove, setConfirmRemove] = useState<Provider | null>(null);
  const [removing, setRemoving] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const dragged = useRef<Provider | null>(null);
  const transition = useRef<ViewTransition | null>(null);
  const previousPositions = useRef(new Map<string, DOMRect>());
  const mounted = useRef(true);
  const queuedRouting = useRef<ProviderRouting | null>(null);
  const saveRunning = useRef(false);
  const routing = draft ?? status?.routing;
  const enabled = routing?.order.filter(id => routing.automatic.includes(id) && status?.providers[id].configured) ?? [];

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      transition.current?.skipTransition();
      delete document.documentElement.dataset.providerTransition;
    };
  }, []);
  useEffect(() => { if (!active) { transition.current?.skipTransition(); setSelected(null); setConfirmRemove(null); } }, [active]);
  useLayoutEffect(() => {
    const positions = previousPositions.current;
    if (!positions.size) return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) { positions.clear(); return; }
    panel.current?.querySelectorAll<HTMLElement>('[data-provider]').forEach(card => {
      const old = positions.get(card.dataset.provider!);
      card.getAnimations().forEach(animation => animation.cancel());
      const next = card.getBoundingClientRect();
      if (old && (old.x !== next.x || old.y !== next.y)) {
        card.animate([{ transform: `translate(${old.x - next.x}px,${old.y - next.y}px)` }, { transform: 'translate(0,0)' }],
          { duration: 320, easing: 'cubic-bezier(.22,1,.36,1)' });
      }
    });
    positions.clear();
  }, [routing]);

  function clearTransitionNames() {
    panel.current?.style.removeProperty('view-transition-name');
    panel.current?.querySelectorAll<HTMLElement>('[style*="view-transition-name"]').forEach(node => node.style.removeProperty('view-transition-name'));
  }
  function stopCardMotion() {
    panel.current?.querySelectorAll<HTMLElement>('[data-provider]').forEach(card => {
      card.getAnimations().forEach(animation => animation.cancel());
    });
  }
  function rememberCardPositions() {
    const cards = panel.current?.querySelectorAll<HTMLElement>('[data-provider]') ?? [];
    previousPositions.current = new Map([...cards].map(card => [card.dataset.provider!, card.getBoundingClientRect()]));
  }
  function nameTransition(id: Provider, detail: boolean) {
    const root = detail ? panel.current : panel.current?.querySelector<HTMLElement>(`[data-provider="${id}"]`);
    if (!root) return;
    root.style.viewTransitionName = 'provider-settings-focus';
    const title = root.querySelector<HTMLElement>('.provider-title');
    if (title) title.style.viewTransitionName = 'provider-settings-title';
  }
  function showProvider(next: Provider | null) {
    if (transition.current || removing) return;
    setConfirmRemove(null);
    const id = next ?? selected;
    const keyboardNavigation = document.activeElement?.matches(':focus-visible');
    if (!id || !document.startViewTransition || matchMedia('(prefers-reduced-motion: reduce)').matches) {
      setSelected(next); return;
    }
    clearTransitionNames(); nameTransition(id, selected !== null);
    document.documentElement.dataset.providerTransition = next ? 'opening' : 'closing';
    const current = document.startViewTransition(() => {
      flushSync(() => setSelected(next));
      clearTransitionNames(); nameTransition(id, next !== null);
    });
    transition.current = current;
    void current.finished.catch(() => {}).finally(() => {
      if (transition.current !== current) return;
      transition.current = null; clearTransitionNames();
      delete document.documentElement.dataset.providerTransition;
      if (keyboardNavigation) panel.current?.querySelector<HTMLButtonElement>(next ? '.provider-back' : `[data-settings="${id}"]`)?.focus({ preventScroll: true });
    });
  }
  async function saveRouting(next: ProviderRouting) {
    if (removing || transition.current) return;
    // Disabled cards stay last: first choice must also be the first card.
    next = { ...next, order: [...next.order.filter(id => next.automatic.includes(id)), ...next.order.filter(id => !next.automatic.includes(id))] };
    rememberCardPositions();
    setDraft(next); setError('');
    queuedRouting.current = next;
    if (saveRunning.current) return;
    saveRunning.current = true;
    setSaving(true);
    let confirmed = draft ?? status?.routing ?? next;
    // Keep writes serial and coalesce intermediate moves. The UI never waits for them.
    // Capture the instance now: queued writes must not follow a later route change.
    const path = `${instance ? `/instances/${encodeURIComponent(instance)}` : ''}/settings/providers/routing`;
    try {
      while (true) {
        while (queuedRouting.current) {
          const pending = queuedRouting.current;
          queuedRouting.current = null;
          confirmed = await api.put<ProviderRouting>(path, pending);
        }
        providersChanged(instance, 'routing');
        const refreshed = mounted.current ? await onRoutingChanged() : true;
        if (!queuedRouting.current) {
          if (mounted.current) {
            setDraft(refreshed ? null : confirmed);
            if (!refreshed) setError('Provider order was saved, but status could not be refreshed.');
          }
          break;
        }
      }
    } catch (err) {
      queuedRouting.current = null;
      if (mounted.current) {
        rememberCardPositions();
        setDraft(confirmed);
        setError(err instanceof Error ? err.message : 'Could not save provider order.');
      }
    } finally {
      saveRunning.current = false;
      if (mounted.current) setSaving(false);
    }
  }
  function moveProvider(id: Provider, target: Provider) {
    if (!routing || removing || transition.current || id === target) return;
    const order = [...routing.order];
    const from = order.indexOf(id), to = order.indexOf(target);
    order.splice(from, 1); order.splice(to, 0, id);
    void saveRouting({ ...routing, order });
  }
  async function removeProvider(id: Provider) {
    setRemoving(true); setError('');
    try { await onRemove(id); if (mounted.current) setConfirmRemove(null); }
    catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : 'Could not remove provider.'); }
    finally { if (mounted.current) setRemoving(false); }
  }

  return <section hidden={!active} ref={panel} className="settings-section provider-panel">
    <header className="settings-section-heading provider-panel-heading">
      <span className="provider-panel-icon">{selected ? <ProviderLogo id={selected} size={20} /> : <Bot size={20} />}</span>
      <div><h2 className="provider-title">{selected ? names[selected] : 'LLM Providers'}</h2>
        <p>{selected ? 'Connection and model settings' : 'Configure and manage your model providers.'}</p></div>
      {selected && <button className="btn-secondary provider-back" onClick={() => showProvider(null)} disabled={removing}><ArrowLeft size={14} />All providers</button>}
    </header>
    {error && <p role="alert" className="provider-error">{error}</p>}
    {loading && !status ? <div className="provider-loading" role="status"><Loader2 size={20} className="animate-spin" /><span>Loading providers…</span></div>
      : !status || !routing ? <div className="provider-loading"><p>Could not load providers.</p><button className="btn-secondary" onClick={onRetry}>Retry</button></div>
      : selected ? <div className={`provider-detail ${selected === 'ollama' ? 'provider-detail-ollama' : ''}`} key={`${selected}:${status.providers[selected].auth_method}:${status.providers[selected].auth_source}`}>
        {renderConnection(selected)}
        {selected !== 'ollama' && <ProviderModelSettings provider={selected} models={status.providers[selected].models} onSaved={() => void onChanged()} />}
      </div> : <>
        <div className="provider-order-heading"><p className="provider-order-hint">Drag to reorder. The first enabled provider is tried first, then the next.</p>
          <span className="provider-order-status" role="status" aria-live="polite">{saving ? 'Saving order…' : ''}</span></div>
        {enabled.length === 0 && <p role="status" className="provider-order-hint">Configure a provider and enable “Use as fallback” to start automatic routing.</p>}
        <div className="settings-provider-grid" aria-label="Providers in routing order" aria-busy={saving}>
          {routing.order.map((id, index) => {
            const provider = status.providers[id];
            const rank = enabled.indexOf(id);
            const configured = provider.configured;
            return <article key={id} data-provider={id} className="settings-provider provider-card" aria-label={`${names[id]} provider`}
              onDragOver={event => {
                if (!dragged.current || dragged.current === id || removing) return;
                event.preventDefault(); event.dataTransfer.dropEffect = 'move';
                panel.current?.querySelectorAll('.is-drop-target').forEach(card => card.classList.remove('is-drop-target'));
                event.currentTarget.classList.add('is-drop-target');
              }}
              onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) event.currentTarget.classList.remove('is-drop-target'); }}
              onDrop={event => {
                event.preventDefault(); const source = dragged.current; dragged.current = null;
                event.currentTarget.classList.remove('is-drop-target');
                if (source) moveProvider(source, id);
              }}>
              <div className="settings-provider-heading">
                <button className="provider-drag" draggable={!removing} disabled={removing}
                  aria-label={`Reorder ${names[id]}`} title="Drag to reorder, or use arrow keys"
                  onDragStart={event => { stopCardMotion(); dragged.current = id; event.dataTransfer.setData('text/plain', id); event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setDragImage(event.currentTarget.closest('article')!, 24, 18); }}
                  onDragEnd={() => { dragged.current = null; panel.current?.querySelectorAll('.is-drop-target').forEach(card => card.classList.remove('is-drop-target')); }}
                  onKeyDown={event => {
                    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
                    event.preventDefault(); const target = routing.order[index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1)];
                    if (target) moveProvider(id, target);
                  }}><GripVertical size={15} /></button>
                <span className={`provider-status-dot ${configured ? 'is-connected' : ''}`} />
                <ProviderLogo id={id} size={16} />
                <h3 className="provider-title">{names[id]}</h3>
                <button data-settings={id} className="provider-settings-button" aria-label={`${names[id]} settings`} title={`${names[id]} settings`} onClick={() => showProvider(id)}><Settings2 size={16} /></button>
              </div>
              <div className="provider-routing">
                <span className={`provider-rank ${rank === 0 ? 'is-first' : ''}`}>{rank < 0 ? configured ? 'Manual only' : 'Not configured' : <><span>{rank + 1}</span>{rank === 0 ? 'First choice' : 'Fallback'}</>}</span>
                <Toggle className="provider-fallback-control" label="Use as fallback" ariaLabel={`${names[id]} use as fallback`} enabled={rank >= 0}
                  title={!configured ? 'Configure this provider first' : rank >= 0 && enabled.length === 1 ? 'Keep at least one provider enabled' : undefined}
                  disabled={!configured || removing || (rank >= 0 && enabled.length === 1)}
                  onChange={useAsFallback => void saveRouting({ ...routing, automatic: useAsFallback ? [...routing.automatic, id] : routing.automatic.filter(value => value !== id) })} />
              </div>
              <div className="settings-provider-badges"><StatusChip label={!configured ? 'Not configured' : id === 'ollama' ? 'Endpoint' : provider.auth_method === 'oauth' ? provider.auth_source === 'cli' ? 'OAuth · Auto-sync' : 'OAuth' : 'API Key'} tone={configured ? 'info' : 'warn'} /></div>
              <p className="provider-connection">{!configured ? 'Open settings to connect' : id === 'ollama' ? 'Local or remote server' : provider.auth_source === 'cli' ? `${cliNames[id]} · Auto-sync` : provider.masked_key ?? 'Connected'}</p>
              <div className="provider-model-summary">{modelTiers.map(({tier}) => <div key={tier}><ModelTierIdentity tier={tier} compact className="provider-tier-label" /><span title={provider.models[tier]}>{configured ? modelLabel(provider.models[tier]) : '—'}</span></div>)}</div>
              <div className="provider-card-usage">{configured && id !== 'ollama' && provider.auth_method === 'oauth' && <ProviderUsage provider={id} name={names[id]} active={active} connection={provider} />}</div>
              <footer className="settings-provider-actions">
                {configured && (confirmRemove === id ? <><span>Disconnect?</span><button disabled={removing || saving} onClick={() => void removeProvider(id)}>Confirm</button><button disabled={removing} onClick={() => setConfirmRemove(null)}>Cancel</button></> : <button disabled={removing} className="provider-remove" onClick={() => setConfirmRemove(id)}>Remove</button>)}
              </footer>
            </article>;
          })}
        </div>
        <p className="provider-order-note">Turning fallback off keeps the provider available for manual selection.</p>
      </>}
  </section>;
}
