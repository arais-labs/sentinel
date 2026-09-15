import { useEffect, useState } from 'react';
import './chat-appearance.css';

const presets = {
  balanced: { label: 'Balanced', detail: 'Everyday chat · 14px', size: 14, line: 1.65 },
  compact: { label: 'Compact', detail: 'More on screen · 13px', size: 13, line: 1.55 },
  reading: { label: 'Reading', detail: 'Longer answers · 15px', size: 15, line: 1.7 },
  large: { label: 'Large text', detail: 'Easier to read · 16px', size: 16, line: 1.7 },
};
const fonts = {
  system: { label: 'System', value: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
  sentinel: { label: 'Sentinel', value: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
  mono: { label: 'Monospace', value: 'ui-monospace, SFMono-Regular, Menlo, monospace' },
};
type Preferences = { preset: keyof typeof presets; font: keyof typeof fonts };
const key = 'sentinel.chat-appearance';
function load(): Preferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null');
    return { preset: value && Object.hasOwn(presets, value.preset) ? value.preset : 'balanced', font: value && Object.hasOwn(fonts, value.font) ? value.font : 'system' };
  } catch { return { preset: 'balanced', font: 'system' }; }
}
export function applyChatAppearance(preferences = load()) {
  const root = document.documentElement;
  root.style.setProperty('--chat-font-size', `${presets[preferences.preset].size}px`);
  root.style.setProperty('--chat-line-height', String(presets[preferences.preset].line));
  root.style.setProperty('--chat-font-family', fonts[preferences.font].value);
}

export function ChatAppearanceSettings() {
  const [preferences, setPreferences] = useState(load);
  useEffect(() => {
    applyChatAppearance(preferences);
    try { localStorage.setItem(key, JSON.stringify(preferences)); } catch { /* Keep in-memory preferences. */ }
  }, [preferences]);
  return <section className="chat-appearance settings-section" aria-label="Chat appearance">
    <header><div><h2>Chat appearance</h2><p className="settings-global-note">Text preferences apply to conversations across all instances.</p></div></header>
        <fieldset><legend>Text preset</legend><div className="chat-preset-grid">{Object.entries(presets).map(([value, preset]) => <button key={value} aria-pressed={preferences.preset === value} onClick={() => setPreferences(p => ({ ...p, preset: value as Preferences['preset'] }))}><span>{preset.label}</span><small>{preset.detail}</small></button>)}</div></fieldset>
        <label className="chat-font-choice">Font<select value={preferences.font} onChange={event => setPreferences(p => ({ ...p, font: event.target.value as Preferences['font'] }))}>{Object.entries(fonts).map(([value, font]) => <option key={value} value={value}>{font.label}</option>)}</select></label>
        <div className="chat-font-preview">A clearer view of your next idea.<small>Applied to your conversations instantly.</small></div>
        <footer><button onClick={() => setPreferences({ preset: 'balanced', font: 'system' })}>Reset</button></footer>
  </section>;
}
