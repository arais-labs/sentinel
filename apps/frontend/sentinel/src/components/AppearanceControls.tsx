import { ChevronDown, Minus, Plus, RotateCcw, Type } from 'lucide-react';
import { useEffect, useState } from 'react';
import './appearance.css';

const legacyPresets = {
  balanced: { spacing: 1.65 },
  compact: { spacing: 1.55 },
  reading: { spacing: 1.7 },
  large: { spacing: 1.7 },
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
type Preferences = { font: FontChoice; spacing: number; weight: number };

const storageKey = 'sentinel.appearance';
const legacyStorageKey = 'sentinel.chat-appearance';
const defaults: Preferences = { font: 'system', spacing: 1.65, weight: 450 };
const quickStyles: { label: string; preferences: Preferences }[] = [
  { label: 'Sentinel', preferences: defaults },
  { label: 'Focused', preferences: { font: 'system', spacing: 1.55, weight: 400 } },
  { label: 'Compact', preferences: { font: 'inter', spacing: 1.5, weight: 400 } },
  { label: 'Reading', preferences: { font: 'charter', spacing: 1.75, weight: 400 } },
  { label: 'Modern', preferences: { font: 'manrope', spacing: 1.65, weight: 400 } },
  { label: 'Technical', preferences: { font: 'jetbrains', spacing: 1.55, weight: 400 } },
];
const clampWeight = (value: unknown) => Math.max(300, Math.min(700, Math.round((Number(value) || defaults.weight) / 50) * 50));
const legacySpacing = { compact: 1.55, balanced: 1.65, relaxed: 1.7 } as const;
const clampSpacing = (value: unknown) => Math.max(1.2, Math.min(2, Math.round((Number(value) || defaults.spacing) * 20) / 20));

function load(): Preferences {
  try {
    const stored = localStorage.getItem(storageKey) ?? localStorage.getItem(legacyStorageKey);
    const value = JSON.parse(stored || 'null');
    const legacy = value?.preset && Object.hasOwn(legacyPresets, value.preset)
      ? legacyPresets[value.preset as keyof typeof legacyPresets]
      : null;
    return {
      font: value && Object.hasOwn(fonts, value.font) ? value.font : defaults.font,
      spacing: clampSpacing(typeof value?.spacing === 'string' && Object.hasOwn(legacySpacing, value.spacing) ? legacySpacing[value.spacing as keyof typeof legacySpacing] : value?.spacing ?? legacy?.spacing),
      weight: clampWeight(value?.weight),
    };
  } catch { return defaults; }
}

export function applyAppearance(preferences = load()) {
  const root = document.documentElement;
  const body = document.body;
  const fontFamily = fonts[preferences.font].value;
  root.style.removeProperty('--app-ui-scale');
  root.style.removeProperty('zoom');
  root.style.removeProperty('width');
  root.style.removeProperty('height');
  root.style.removeProperty('--app-font-size');
  root.style.removeProperty('--app-font-secondary-size');
  root.style.removeProperty('--app-font-small-size');
  root.style.removeProperty('--app-font-meta-size');
  root.style.removeProperty('--app-font-code-size');
  root.style.setProperty('--app-line-height', String(preferences.spacing));
  root.style.setProperty('--app-font-family', fontFamily);
  root.style.setProperty('--app-font-weight', String(preferences.weight));
  root.style.setProperty('--app-font-agent-weight', String(preferences.weight));
  root.style.setProperty('--app-font-tool-weight', String(Math.max(300, preferences.weight - 50)));
  root.style.setProperty('--app-font-strong-weight', String(Math.min(750, preferences.weight + 200)));
  body.style.setProperty('font-family', fontFamily);
  body.style.setProperty('font-weight', String(preferences.weight));
  body.style.setProperty('line-height', String(preferences.spacing));
  body.style.removeProperty('transform');
  body.style.removeProperty('transform-origin');
  body.style.removeProperty('width');
  body.style.removeProperty('height');
}

export function AppearanceSettings() {
  const [preferences, setPreferences] = useState(load);
  useEffect(() => {
    applyAppearance(preferences);
    try {
      localStorage.setItem(storageKey, JSON.stringify(preferences));
      localStorage.removeItem(legacyStorageKey);
    } catch { /* Keep in-memory preferences. */ }
  }, [preferences]);

  const updateSpacing = (spacing: number) => setPreferences(current => ({ ...current, spacing: clampSpacing(spacing) }));
  const updateWeight = (weight: number) => setPreferences(current => ({ ...current, weight: clampWeight(weight) }));
  const isSelected = (candidate: Preferences) => candidate.font === preferences.font && candidate.spacing === preferences.spacing && candidate.weight === preferences.weight;
  return <section className="app-appearance" aria-label="Appearance">
    <header className="app-appearance-page-heading"><h2>Appearance</h2></header>
    <div className="app-appearance-panel">
      <div className="app-appearance-panel-heading"><Type size={15} /><div><strong>Interface typography</strong><span>Applied throughout Sentinel across every instance.</span></div></div>
      <div className="appearance-quick-styles" aria-label="Quick appearance styles"><span>Quick styles</span><div>{quickStyles.map(style => <button type="button" key={style.label} aria-pressed={isSelected(style.preferences)} onClick={() => setPreferences(style.preferences)}>{style.label}</button>)}</div></div>
      <div className="appearance-setting-row appearance-native-zoom">
        <div className="appearance-setting-copy"><strong>Interface size</strong><span>Use native page zoom so every surface stays fitted to the window.</span></div>
        <div className="appearance-zoom-shortcuts" aria-label="Native interface zoom shortcuts">
          <span><kbd>⌘/Ctrl</kbd><kbd>−</kbd><small>Smaller</small></span>
          <span><kbd>⌘/Ctrl</kbd><kbd>0</kbd><small>Reset</small></span>
          <span><kbd>⌘/Ctrl</kbd><kbd>+</kbd><small>Larger</small></span>
        </div>
      </div>
      <div className="appearance-setting-row">
        <div className="appearance-setting-copy"><label htmlFor="chat-font-family">Font</label><span>{fonts[preferences.font].detail}</span></div>
        <div className="appearance-select-control" style={{ fontFamily: fonts[preferences.font].value }}><select id="chat-font-family" value={preferences.font} onChange={event => setPreferences(current => ({ ...current, font: event.target.value as FontChoice }))}>{Object.entries(fonts).map(([value, font]) => <option key={value} value={value}>{font.label}</option>)}</select><ChevronDown size={15} aria-hidden="true" /></div>
      </div>
      <div className="appearance-setting-row">
        <div className="appearance-setting-copy"><strong>Text weight</strong><span>Adjust the weight of conversation text.</span></div>
        <div className="appearance-setting-control"><output>{preferences.weight}</output><div className="appearance-size-control">
          <button type="button" aria-label="Decrease text weight" disabled={preferences.weight <= 300} onClick={() => updateWeight(preferences.weight - 50)}><Minus size={14} /></button>
          <input aria-label="Chat text weight" type="range" min="300" max="700" step="50" value={preferences.weight} style={{ '--appearance-size-progress': `${((preferences.weight - 300) / 400) * 100}%` } as React.CSSProperties} onChange={event => updateWeight(Number(event.target.value))} />
          <button type="button" aria-label="Increase text weight" disabled={preferences.weight >= 700} onClick={() => updateWeight(preferences.weight + 50)}><Plus size={14} /></button>
        </div></div>
      </div>
      <div className="appearance-setting-row">
        <div className="appearance-setting-copy"><strong>Line spacing</strong><span>Control the distance between lines of text.</span></div>
        <div className="appearance-setting-control"><output>{preferences.spacing.toFixed(2).replace(/0$/, '')}×</output><div className="appearance-size-control">
          <button type="button" aria-label="Decrease line spacing" disabled={preferences.spacing <= 1.2} onClick={() => updateSpacing(preferences.spacing - .05)}><Minus size={14} /></button>
          <input aria-label="Chat line spacing" type="range" min="1.2" max="2" step="0.05" value={preferences.spacing} style={{ '--appearance-size-progress': `${((preferences.spacing - 1.2) / .8) * 100}%` } as React.CSSProperties} onChange={event => updateSpacing(Number(event.target.value))} />
          <button type="button" aria-label="Increase line spacing" disabled={preferences.spacing >= 2} onClick={() => updateSpacing(preferences.spacing + .05)}><Plus size={14} /></button>
        </div></div>
      </div>
      <footer><button type="button" onClick={() => setPreferences(defaults)}><RotateCcw size={13} />Reset appearance</button></footer>
    </div>
  </section>;
}
