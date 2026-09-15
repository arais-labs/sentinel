import { useState } from 'react';
import { Search } from 'lucide-react';
import { shortcutReference } from './product-tour-content';

const spokenKeys: Record<string, string> = {
  '⌘': 'Command',
  '⌥': 'Option',
  '⌃': 'Control',
  '⇧': 'Shift',
  '↵': 'Enter',
  '⌫': 'Delete',
  '←': 'Left',
  '→': 'Right',
  '↑': 'Up',
  '↓': 'Down',
  Esc: 'Escape',
};
export function TourKeycaps({
  combinations,
  pressed = [],
}: {
  combinations: string[][];
  pressed?: string[];
}) {
  return (
    <span
      className="tour-keycaps"
      aria-label={combinations
        .map((keys) => keys.map((key) => spokenKeys[key] ?? key).join(' + '))
        .join(' or ')}
    >
      {combinations.map((keys, index) => (
        <span className="tour-key-combination" key={keys.join()} aria-hidden="true">
          {index > 0 && <span className="tour-key-or">or</span>}
          {keys.map((key) => (
            <kbd
              key={key}
              data-pressed={
                pressed.includes(key) ||
                (key === '1–9' && pressed.some((value) => /^[1-9]$/.test(value))) ||
                undefined
              }
            >
              {key}
            </kbd>
          ))}
        </span>
      ))}
    </span>
  );
}

export function TourShortcuts() {
  const [query, setQuery] = useState('');
  const rows = shortcutReference.filter((item) =>
    `${item.group} ${item.title} ${item.detail} ${item.keys
      .flat()
      .map((key) => spokenKeys[key] ?? key)
      .join(' ')}`
      .toLowerCase()
      .includes(query.toLowerCase().trim())
  );
  return (
    <section className="tour-shortcuts" aria-label="Keyboard shortcuts">
      <h2>Your keyboard reference.</h2>
      <p>Command shortcuts are for the Mac desktop app. Pane-specific shortcuts need focus in that pane.</p>
      <label className="tour-search">
        <Search size={15} />
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find a shortcut…"
          aria-label="Search shortcuts"
        />
      </label>
      {[...new Set(rows.map((item) => item.group))].map((group) => (
        <section key={group} className="tour-shortcut-group">
          <h3>{group}</h3>
          {rows
            .filter((item) => item.group === group)
            .map((item) => (
              <div className="tour-shortcut-row" key={item.title}>
                <div>
                  <strong>{item.title}</strong>
                  <TourKeycaps combinations={item.keys} />
                </div>
                <p>{item.detail}</p>
              </div>
            ))}
        </section>
      ))}
      {!rows.length && <p className="tour-empty">No matching shortcuts. Try “chat”, “focus”, or “file”.</p>}
    </section>
  );
}
