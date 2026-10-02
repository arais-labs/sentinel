import { useEffect, useId, useRef, useState } from 'react';
import { ClickAwayListener, Popper } from '@mui/material';
import { Check, ChevronDown } from 'lucide-react';
import { api } from '../lib/api';
import { useInstanceName } from '../lib/workspace-context';
import { providersChanged } from '../hooks/useModelCatalog';
import { ModelTierIdentity, modelTiers } from './ui/ModelTierIdentity';

type Tier = 'fast' | 'normal' | 'hard';
type Models = Record<Tier, string | null>;
interface ModelSettings {
  namespace: string;
  defaults: Record<Tier, string>;
  overrides: Models;
  effective: Record<Tier, string>;
}
interface ModelOptions { namespace: string; models: string[]; message: string | null }
const inherited: Models = { fast: null, normal: null, hard: null };

/** Editable, searchable model IDs without a browser-native datalist popup. */
function ModelPicker({ label, value, defaultModel, options, disabled, onChange }: {
  label: string; value: string | null; defaultModel: string; options: string[];
  disabled: boolean; onChange: (value: string | null) => void;
}) {
  const anchor = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlighted, setHighlighted] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const choices = [{ model: null, label: 'Use default', detail: defaultModel },
    ...options.map(model => ({ model, label: model, detail: '' }))]
    .filter(option => `${option.label} ${option.detail}`.toLowerCase().includes(query.toLowerCase()));

  useEffect(() => {
    const menu = list.current;
    const option = menu?.children[highlighted] as HTMLElement | undefined;
    if (!open || !menu || !option) return;
    // Scroll the suggestions only; never move the settings pane or the page.
    const top = option.offsetTop;
    const bottom = top + option.offsetHeight;
    if (top < menu.scrollTop) menu.scrollTop = top;
    else if (bottom > menu.scrollTop + menu.clientHeight) menu.scrollTop = bottom - menu.clientHeight;
  }, [open, highlighted, query]);

  function choose(index: number) {
    const choice = choices[index];
    if (!choice) return;
    onChange(choice.model); setOpen(false); setQuery('');
  }
  return <ClickAwayListener onClickAway={() => setOpen(false)}>
    <div className="provider-model-picker" ref={anchor}>
      <input ref={input} className="provider-model-combobox" aria-label={label} role="combobox"
        aria-autocomplete="list" aria-expanded={open && !disabled} aria-controls={listId}
        aria-activedescendant={open && choices[highlighted] ? `${listId}-${highlighted}` : undefined}
        value={value ?? ''} placeholder={defaultModel} maxLength={200} disabled={disabled}
        autoComplete="off" spellCheck={false}
        onFocus={() => { setQuery(''); setHighlighted(0); setOpen(true); }}
        onBlur={() => setOpen(false)}
        onChange={event => { onChange(event.target.value); setQuery(event.target.value); setHighlighted(0); setOpen(true); }}
        onKeyDown={event => {
          if (event.key === 'Escape') { event.preventDefault(); setOpen(false); }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault(); setOpen(true);
            setHighlighted(index => choices.length ? (index + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length : 0);
          }
          if (event.key === 'Enter' && open) { event.preventDefault(); choose(highlighted); }
        }} />
      <button type="button" className="provider-model-chevron" aria-label={`Choose ${label} model`} disabled={disabled}
        onMouseDown={event => event.preventDefault()} onClick={() => {
          const wasOpen = open; input.current?.focus(); setQuery(''); setHighlighted(0); setOpen(!wasOpen);
        }}><ChevronDown size={14} /></button>
      <Popper open={open && !disabled} anchorEl={anchor.current} placement="bottom-start" sx={{ zIndex: 10000 }}
        modifiers={[{ name: 'offset', options: { offset: [0, 6] } }]}>
        <div className="provider-model-menu" data-pane-menu style={{ width: anchor.current?.clientWidth }}>
          <div ref={list} role="listbox" id={listId} aria-label={`${label} models`}>
            {choices.map((option, index) => <button type="button" role="option" key={option.model ?? 'default'}
              tabIndex={-1}
              id={`${listId}-${index}`} aria-selected={(value || null) === option.model}
              className={`provider-model-option ${index === highlighted ? 'is-highlighted' : ''}`}
              onMouseDown={event => event.preventDefault()} onMouseEnter={() => setHighlighted(index)} onClick={() => choose(index)}>
              <span><strong>{option.label}</strong>{option.detail && <small>{option.detail}</small>}</span>
              {(value || null) === option.model && <Check size={13} />}
            </button>)}
          </div>
          {!choices.length && <p className="provider-model-empty">No matching suggestions. You can save this model ID directly.</p>}
          <p className="provider-model-menu-hint">Type to search or enter a custom model ID.</p>
        </div>
      </Popper>
    </div>
  </ClickAwayListener>;
}

/** One editor for all connections; defaults and effective routing come from the backend. */
export function ProviderModelSettings({ provider, models, onSaved }: {
  provider: 'anthropic' | 'openai' | 'gemini' | 'ollama';
  models?: Record<Tier, string>;
  onSaved?: () => void;
}) {
  const instance = useInstanceName();
  const [config, setConfig] = useState<ModelSettings | null>(null);
  const [draft, setDraft] = useState<Models>(inherited);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [options, setOptions] = useState<string[]>([]);
  const [optionsMessage, setOptionsMessage] = useState('');
  const loadEpoch = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    ++loadEpoch.current;
    setConfig(null); setBusy(false); setError(''); setMessage(''); setOptions([]); setOptionsMessage('');
    void api.get<ModelSettings>(`/settings/providers/${provider}/models`, { signal: controller.signal })
      .then(async value => {
        if (controller.signal.aborted) return;
        setConfig(value); setDraft(value.overrides);
        try {
          const choices = await api.get<ModelOptions>(`/settings/providers/${provider}/model-options`, { signal: controller.signal, timeoutMs: 15_000 });
          if (!controller.signal.aborted && choices.namespace === value.namespace) { setOptions(choices.models); setOptionsMessage(choices.message ?? ''); }
        } catch {
          if (!controller.signal.aborted) setOptionsMessage('Suggestions are unavailable. You can still enter a model ID.');
        }
      })
      .catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Could not load models.'); });
    return () => { controller.abort(); ++loadEpoch.current; };
  }, [instance, provider]);

  const changed = config && modelTiers.some(({tier}) => (draft[tier]?.trim() || null) !== config.overrides[tier]);
  const modelOptions = config ? [...new Set([...options, ...Object.values(config.defaults), ...Object.values(config.effective)])].filter(Boolean) : [];
  async function save(models: Models) {
    if (!config || busy) return;
    const epoch = loadEpoch.current;
    setBusy(true); setError(''); setMessage('');
    try {
      const value = await api.put<ModelSettings>(`/settings/providers/${provider}/models`, {
        namespace: config.namespace,
        ...Object.fromEntries(modelTiers.map(({tier}) => [tier, models[tier]?.trim() || null])),
      });
      if (epoch === loadEpoch.current) { setConfig(value); setDraft(value.overrides); setMessage('Models saved.'); }
      providersChanged(instance);
      if (epoch === loadEpoch.current) onSaved?.();
    } catch (err) {
      if (epoch === loadEpoch.current) setError(err instanceof Error ? err.message : 'Could not save models.');
    } finally { if (epoch === loadEpoch.current) setBusy(false); }
  }

  return <section className="settings-provider-models" aria-label="Tier models">
    <h3>Tier models</h3>
    <div>
      <p className="provider-model-description">Choose a model for each tier, or keep the defaults.</p>
      <form aria-busy={!config} onSubmit={event => { event.preventDefault(); void save(draft); }}>
        {modelTiers.map(({tier, label}) => <div key={tier} className="provider-model-setting text-xs">
          <ModelTierIdentity tier={tier} className="provider-tier-label" />
          <div className="provider-model-input">
            <ModelPicker label={label} value={config ? draft[tier] : null} defaultModel={config?.defaults[tier] ?? models?.[tier] ?? 'Loading models…'} options={modelOptions} disabled={!config || busy}
              onChange={value => { setDraft(current => ({ ...current, [tier]: value })); setMessage(''); }} />
          </div>
        </div>)}
        {optionsMessage && <p className="provider-model-notice" role="status">{optionsMessage}</p>}
        {provider === 'ollama' && <p className="provider-model-notice">Models must be installed on this server and support tools.</p>}
        <div className="provider-model-footer">
          <button className="btn-secondary" type="submit" disabled={busy || !changed}>{busy ? 'Saving…' : 'Save models'}</button>
          <button className="btn-secondary" type="button" disabled={!config || busy || !modelTiers.some(({tier}) => config.overrides[tier] !== null || draft[tier])}
            onClick={() => void save(inherited)}>Reset to defaults</button>
        </div>
      </form>
      {error && <p role="alert" className="text-xs text-rose-400">{error}</p>}
      {message && <p role="status" className="text-xs text-(--text-muted)">{message}</p>}
    </div>
  </section>;
}
