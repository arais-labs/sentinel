import { ChevronDown, Minus, Plus, RotateCcw, Type } from 'lucide-react';
import { useEffect, useState } from 'react';
import './chat-appearance.css';

const legacyPresets = {
  balanced: { size: 14, spacing: 1.65 },
  compact: { size: 13, spacing: 1.55 },
  reading: { size: 15, spacing: 1.7 },
  large: { size: 16, spacing: 1.7 },
} as const;

const fonts = {
  system: { label: 'System', detail: 'Native and familiar', value: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
  inter: { label: 'Inter', detail: 'Clean and neutral', value: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
  avenir: { label: 'Avenir', detail: 'Warm and open', value: '"Avenir Next", Avenir, Inter, sans-serif' },
  helvetica: { label: 'Helvetica Neue', detail: 'Crisp and understated', value: '"Helvetica Neue", Helvetica, Arial, sans-serif' },
  source: { label: 'Source Sans', detail: 'Clear at small sizes', value: '"Source Sans 3", "Source Sans Pro", Inter, sans-serif' },
  manrope: { label: 'Manrope', detail: 'Modern and spacious', value: 'Manrope, Inter, -apple-system, sans-serif' },
  charter: { label: 'Charter', detail: 'Comfortable reading', value: 'Charter, "Bitstream Charter", Georgia, serif' },
  georgia: { label: 'Georgia', detail: 'Classic long-form serif', value: 'Georgia, "Times New Roman", serif' },
  mono: { label: 'SF Mono', detail: 'Technical and precise', value: 'ui-monospace, SFMono-Regular, Menlo, Monaco, monospace' },
  jetbrains: { label: 'JetBrains Mono', detail: 'Readable code-focused mono', value: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace' },
} as const;

type FontChoice = keyof typeof fonts;
type Preferences = { size: number; font: FontChoice; spacing: number; weight: number };

const storageKey = 'sentinel.chat-appearance';
const defaults: Preferences = { size: 14, font: 'system', spacing: 1.65, weight: 450 };
const quickStyles: { label: string; preferences: Preferences }[] = [
  { label: 'Sentinel', preferences: defaults },
  { label: 'Focused', preferences: { size: 13, font: 'system', spacing: 1.55, weight: 400 } },
  { label: 'Compact', preferences: { size: 13, font: 'inter', spacing: 1.5, weight: 400 } },
  { label: 'Reading', preferences: { size: 16, font: 'charter', spacing: 1.75, weight: 400 } },
  { label: 'Modern', preferences: { size: 14, font: 'manrope', spacing: 1.65, weight: 400 } },
  { label: 'Technical', preferences: { size: 13, font: 'jetbrains', spacing: 1.55, weight: 400 } },
];
const clampSize = (value: unknown) => Math.max(11, Math.min(20, Math.round(Number(value) || defaults.size)));
const clampWeight = (value: unknown) => Math.max(300, Math.min(700, Math.round((Number(value) || defaults.weight) / 50) * 50));
const legacySpacing = { compact: 1.55, balanced: 1.65, relaxed: 1.7 } as const;
const clampSpacing = (value: unknown) => Math.max(1.2, Math.min(2, Math.round((Number(value) || defaults.spacing) * 20) / 20));

function load(): Preferences {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || 'null');
    const legacy = value?.preset && Object.hasOwn(legacyPresets, value.preset)
      ? legacyPresets[value.preset as keyof typeof legacyPresets]
      : null;
    return {
      size: clampSize(value?.size ?? legacy?.size),
      font: value && Object.hasOwn(fonts, value.font) ? value.font : defaults.font,
      spacing: clampSpacing(typeof value?.spacing === 'string' && Object.hasOwn(legacySpacing, value.spacing) ? legacySpacing[value.spacing as keyof typeof legacySpacing] : value?.spacing ?? legacy?.spacing),
      weight: clampWeight(value?.weight),
    };
  } catch { return defaults; }
}

export function applyChatAppearance(preferences = load()) {
  const root = document.documentElement;
  root.style.setProperty('--chat-font-size', `${preferences.size}px`);
  root.style.setProperty('--chat-font-secondary-size', `${preferences.size * .86}px`);
  root.style.setProperty('--chat-font-small-size', `${preferences.size * .78}px`);
  root.style.setProperty('--chat-font-meta-size', `${preferences.size * .7}px`);
  root.style.setProperty('--chat-font-code-size', `${preferences.size * .8}px`);
  root.style.setProperty('--chat-line-height', String(preferences.spacing));
  root.style.setProperty('--chat-font-family', fonts[preferences.font].value);
  root.style.setProperty('--chat-font-weight', String(preferences.weight));
  root.style.setProperty('--chat-font-agent-weight', String(preferences.weight));
  root.style.setProperty('--chat-font-tool-weight', String(Math.max(300, preferences.weight - 50)));
  root.style.setProperty('--chat-font-strong-weight', String(Math.min(750, preferences.weight + 200)));
  root.style.removeProperty('--chat-paragraph-gap');
  root.style.removeProperty('--chat-card-pad-y');
  root.style.removeProperty('--chat-card-pad-compact');
  root.style.removeProperty('--chat-turn-gap');
}

export function ChatAppearanceSettings() {
  const [preferences, setPreferences] = useState(load);
  useEffect(() => {
    applyChatAppearance(preferences);
    try { localStorage.setItem(storageKey, JSON.stringify(preferences)); } catch { /* Keep in-memory preferences. */ }
  }, [preferences]);

  const updateSize = (size: number) => setPreferences(current => ({ ...current, size: clampSize(size) }));
  const updateSpacing = (spacing: number) => setPreferences(current => ({ ...current, spacing: clampSpacing(spacing) }));
  const updateWeight = (weight: number) => setPreferences(current => ({ ...current, weight: clampWeight(weight) }));
  const isSelected = (candidate: Preferences) => candidate.size === preferences.size && candidate.font === preferences.font && candidate.spacing === preferences.spacing && candidate.weight === preferences.weight;
  return <section className="chat-appearance" aria-label="Chat appearance">
    <header className="chat-appearance-page-heading"><h2>Chat appearance</h2></header>
    <div className="chat-appearance-panel">
      <div className="chat-appearance-panel-heading"><Type size={15} /><div><strong>Conversation typography</strong><span>Applied to messages, tools, forms, and conversation cards across all instances.</span></div></div>
      <div className="chat-quick-styles" aria-label="Quick appearance styles"><span>Quick styles</span><div>{quickStyles.map(style => <button type="button" key={style.label} aria-pressed={isSelected(style.preferences)} onClick={() => setPreferences(style.preferences)}>{style.label}</button>)}</div></div>
      <div className="chat-setting-row">
        <div className="chat-setting-copy"><strong>Font size</strong><span>Scale all text shown in a conversation.</span></div>
        <div className="chat-setting-control"><output>{preferences.size}px</output><div className="chat-size-control">
          <button type="button" aria-label="Decrease font size" disabled={preferences.size <= 11} onClick={() => updateSize(preferences.size - 1)}><Minus size={14} /></button>
          <input aria-label="Chat font size" type="range" min="11" max="20" step="1" value={preferences.size} style={{ '--chat-size-progress': `${((preferences.size - 11) / 9) * 100}%` } as React.CSSProperties} onChange={event => updateSize(Number(event.target.value))} />
          <button type="button" aria-label="Increase font size" disabled={preferences.size >= 20} onClick={() => updateSize(preferences.size + 1)}><Plus size={14} /></button>
        </div></div>
      </div>
      <div className="chat-setting-row">
        <div className="chat-setting-copy"><label htmlFor="chat-font-family">Font</label><span>{fonts[preferences.font].detail}</span></div>
        <div className="chat-select-control" style={{ fontFamily: fonts[preferences.font].value }}><select id="chat-font-family" value={preferences.font} onChange={event => setPreferences(current => ({ ...current, font: event.target.value as FontChoice }))}>{Object.entries(fonts).map(([value, font]) => <option key={value} value={value}>{font.label}</option>)}</select><ChevronDown size={15} aria-hidden="true" /></div>
      </div>
      <div className="chat-setting-row">
        <div className="chat-setting-copy"><strong>Text weight</strong><span>Adjust the weight of conversation text.</span></div>
        <div className="chat-setting-control"><output>{preferences.weight}</output><div className="chat-size-control">
          <button type="button" aria-label="Decrease text weight" disabled={preferences.weight <= 300} onClick={() => updateWeight(preferences.weight - 50)}><Minus size={14} /></button>
          <input aria-label="Chat text weight" type="range" min="300" max="700" step="50" value={preferences.weight} style={{ '--chat-size-progress': `${((preferences.weight - 300) / 400) * 100}%` } as React.CSSProperties} onChange={event => updateWeight(Number(event.target.value))} />
          <button type="button" aria-label="Increase text weight" disabled={preferences.weight >= 700} onClick={() => updateWeight(preferences.weight + 50)}><Plus size={14} /></button>
        </div></div>
      </div>
      <div className="chat-setting-row">
        <div className="chat-setting-copy"><strong>Line spacing</strong><span>Control the distance between lines of text.</span></div>
        <div className="chat-setting-control"><output>{preferences.spacing.toFixed(2).replace(/0$/, '')}×</output><div className="chat-size-control">
          <button type="button" aria-label="Decrease line spacing" disabled={preferences.spacing <= 1.2} onClick={() => updateSpacing(preferences.spacing - .05)}><Minus size={14} /></button>
          <input aria-label="Chat line spacing" type="range" min="1.2" max="2" step="0.05" value={preferences.spacing} style={{ '--chat-size-progress': `${((preferences.spacing - 1.2) / .8) * 100}%` } as React.CSSProperties} onChange={event => updateSpacing(Number(event.target.value))} />
          <button type="button" aria-label="Increase line spacing" disabled={preferences.spacing >= 2} onClick={() => updateSpacing(preferences.spacing + .05)}><Plus size={14} /></button>
        </div></div>
      </div>
      <footer><button type="button" onClick={() => setPreferences(defaults)}><RotateCcw size={13} />Reset appearance</button></footer>
    </div>
  </section>;
}
