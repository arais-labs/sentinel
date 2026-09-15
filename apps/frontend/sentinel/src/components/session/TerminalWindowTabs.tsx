import { useLayoutEffect, useRef, useState } from 'react';

/** Direct window switching in a dedicated top tab strip. */
export function TerminalWindowTabs({ windows, selected, onSelect }: {
  windows: { window_id: string; name: string }[];
  selected: string; onSelect: (id: string) => void;
}) {
  const rail = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState<{ left: number; width: number } | null>(null);
  useLayoutEffect(() => {
    const element = rail.current;
    if (!element) return;
    const reveal = () => {
      const active = element.querySelector<HTMLElement>('[aria-selected="true"]');
      if (!active) { setIndicator(null); return; }
      active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      const left = active.offsetLeft + 18;
      const width = Math.max(0, active.offsetWidth - 36);
      setIndicator(previous => previous?.left === left && previous.width === width ? previous : { left, width });
    };
    reveal();
    const observer = new ResizeObserver(reveal);
    observer.observe(element);
    element.querySelectorAll('button').forEach(button => observer.observe(button));
    return () => observer.disconnect();
  }, [selected, windows]);
  return <div className="terminal-window-strip">
    <div ref={rail} className="terminal-window-rail" role="tablist" aria-label="Terminal windows"
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const index = windows.findIndex(window => window.window_id === selected);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? windows.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + windows.length) % windows.length;
        const window = windows[next];
        if (window) {
          onSelect(window.window_id);
          rail.current?.querySelectorAll<HTMLButtonElement>('button')[next]?.focus();
        }
      }}>
      {indicator && <span aria-hidden="true" className="terminal-window-indicator" style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }} />}
      {windows.map((window, index) => <button key={window.window_id} type="button" role="tab"
        aria-selected={selected === window.window_id} tabIndex={selected === window.window_id ? 0 : -1}
        title={`${window.name} · Use ← → to switch windows`}
        className="terminal-window-tab"
        onClick={() => onSelect(window.window_id)}>
        <span className="terminal-window-index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span><span>{window.name}</span>
      </button>)}
    </div>
  </div>;
}
