import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/** Tool deltas contain text, not provider token counts. This is an estimate. */
export function useToolInputRate(raw: string, active: boolean) {
  const samples = useRef<{ time: number; chars: number }[]>([]);
  const previous = useRef(raw);
  const [rate, setRate] = useState<number | null>(null);
  useEffect(() => {
    const before = previous.current;
    previous.current = raw;
    if (!active) { samples.current = []; return; }
    if (raw.startsWith(before) && raw.length > before.length) {
      samples.current.push({ time: performance.now(), chars: raw.length - before.length });
    } else if (raw !== before) { samples.current = []; }
  }, [raw, active]);
  useEffect(() => {
    setRate(null);
    if (!active) return;
    const started = performance.now();
    const timer = window.setInterval(() => {
      const now = performance.now();
      samples.current = samples.current.filter(sample => now - sample.time < 3000);
      const chars = samples.current.reduce((sum, sample) => sum + sample.chars, 0);
      setRate(chars ? Math.round(chars / 4 / Math.max(.5, Math.min(3, (now - started) / 1000))) : 0);
    }, 500);
    return () => window.clearInterval(timer);
  }, [active]);
  return active ? rate : null;
}

export function FlowingToolTail({ text }: { text: string }) {
  const element = useRef<HTMLSpanElement>(null);
  const previous = useRef(text);
  const animation = useRef<Animation | null>(null);
  const visibleText = text.slice(-Math.min(180, text.length - 55));
  useLayoutEffect(() => {
    const node = element.current;
    const before = previous.current;
    previous.current = text;
    if (!node) return;
    const offset = animation.current ? new DOMMatrixReadOnly(getComputedStyle(node).transform).m41 : 0;
    animation.current?.cancel();
    if (!text.startsWith(before) || text === before || document.hidden || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const appended = Math.min(text.length - before.length, visibleText.length);
    const range = document.createRange();
    if (!node.firstChild) return;
    range.setStart(node.firstChild, visibleText.length - appended);
    range.setEnd(node.firstChild, visibleText.length);
    const advance = range.getBoundingClientRect().width;
    animation.current = node.animate([
      { transform: `translateX(${Math.min(advance + offset, node.getBoundingClientRect().width)}px)` },
      { transform: 'translateX(0)' },
    ], { duration: 180, easing: 'linear', fill: 'forwards' });
  }, [text, visibleText]);
  useEffect(() => () => animation.current?.cancel(), []);
  return <span className="tool-card-stream-tail"><span ref={element}>{visibleText}</span></span>;
}
