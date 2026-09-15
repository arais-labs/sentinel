import { useEffect, useRef, useState, type RefObject } from 'react';
import './conversation-navigator.css';

type Stop = { top: number; text: string; tools: number };
type Marker = { top: number; kind: string; weight: number };
export function ConversationNavigator({ scrollRef, revision }: { scrollRef: RefObject<HTMLDivElement | null>; revision: unknown }) {
  const rail = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [scrolling, setScrolling] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [view, setView] = useState({ stops: [] as Stop[], markers: [] as Marker[], progress: 0, max: 0, height: 0, fraction: 1 });
  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    let frame = 0;
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    setScrolling(false);
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const rect = scroller.getBoundingClientRect();
        const max = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
        const stops = Array.from(scroller.querySelectorAll<HTMLElement>('[data-turn-prompt]')).map(node => ({
          top: node.getBoundingClientRect().top - rect.top + scroller.scrollTop,
          text: node.dataset.turnPrompt || 'Message with attachment',
          tools: Number((node.nextElementSibling as HTMLElement | null)?.dataset.toolCount || 0),
        }));
        // Merge marks sharing a few screen pixels so long sessions stay cheap to render.
        const bins = new Map<string, Marker>();
        const railHeight = Math.max(24, scroller.clientHeight - 48);
        const position = (node: Element, end = false) => {
          const bounds = node.getBoundingClientRect();
          return Math.max(0, Math.min(1, ((end ? bounds.bottom : bounds.top) - rect.top + scroller.scrollTop) / Math.max(1, max)));
        };
        scroller.querySelectorAll<HTMLElement>('[data-nav-tool], [data-turn-complete]').forEach(node => {
          const kind = node.dataset.turnComplete ? 'end' : 'tool';
          const top = position(node, kind === 'end');
          const key = `${kind}:${Math.round(top * railHeight / 3)}`;
          const previous = bins.get(key);
          bins.set(key, { top, kind, weight: (previous?.weight ?? 0) + 1 });
        });
        setView({ stops, markers: [...bins.values()], max, progress: max ? scroller.scrollTop / max : 0, height: scroller.clientHeight, fraction: scroller.clientHeight / scroller.scrollHeight });
      });
    };
    const onScroll = () => {
      setScrolling(true);
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => setScrolling(false), 900);
      setView(previous => ({ ...previous, progress: previous.max ? scroller.scrollTop / previous.max : 0 }));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    const observeContent = () => {
      observer.disconnect();
      observer.observe(scroller);
      Array.from(scroller.children).forEach(child => observer.observe(child));
      measure();
    };
    // Initial loading and pagination replace children without necessarily changing
    // the timeline reference. Observe their arrival, not just the old placeholder.
    const mutations = new MutationObserver(observeContent);
    mutations.observe(scroller, { childList: true, subtree: true });
    observeContent();
    scroller.addEventListener('scroll', onScroll, { passive: true });
    measure();
    return () => { clearTimeout(idleTimer); cancelAnimationFrame(frame); observer.disconnect(); mutations.disconnect(); scroller.removeEventListener('scroll', onScroll); };
  }, [scrollRef, revision]);
  if (view.max < 200 || view.stops.length < 2) return null;
  const nearest = (ratio: number) => ratio >= .999 ? view.stops.length - 1 : view.stops.reduce((best, stop, index) => Math.abs(Math.min(stop.top, view.max) - ratio * view.max) < Math.abs(Math.min(view.stops[best].top, view.max) - ratio * view.max) ? index : best, 0);
  const selected = nearest(hover ?? view.progress);
  const jump = (top: number) => scrollRef.current?.scrollTo({ top, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  const pointer = (clientY: number) => {
    const rect = rail.current!.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
    setHover(ratio);
    if (dragging.current) scrollRef.current?.scrollTo({ top: ratio * view.max, behavior: 'instant' });
  };
  return <nav className="conversation-navigator" data-scrolling={scrolling || undefined} aria-label="Conversation navigation" style={{ top: view.height / 2, height: Math.max(24, view.height - 48) }}>
    <div ref={rail} className="conversation-navigator-rail" role="slider" tabIndex={0} aria-label="Conversation position" aria-orientation="vertical" aria-valuemin={1} aria-valuemax={view.stops.length} aria-valuenow={selected + 1} aria-valuetext={`Turn ${selected + 1}: ${view.stops[selected].text}`}
      onFocus={() => setHover(view.progress)} onBlur={() => setHover(null)}
      onPointerDown={event => { if (event.button !== 0) return; dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); pointer(event.clientY); }}
      onPointerMove={event => pointer(event.clientY)}
      onPointerUp={event => { dragging.current = false; event.currentTarget.releasePointerCapture(event.pointerId); setHover(null); }}
      onPointerCancel={() => { dragging.current = false; setHover(null); }}
      onLostPointerCapture={() => { dragging.current = false; }}
      onPointerLeave={() => { if (!dragging.current) setHover(null); }}
      onKeyDown={event => {
        let index: number;
        if (event.key === 'Home') index = 0;
        else if (event.key === 'End') { event.preventDefault(); jump(view.max); setHover(1); return; }
        else if (event.key === 'ArrowDown' || event.key === 'ArrowRight') index = Math.min(view.stops.length - 1, selected + 1);
        else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') index = Math.max(0, selected - 1);
        else return;
        event.preventDefault(); jump(view.stops[index].top); setHover(Math.min(1, view.stops[index].top / view.max));
      }}>
      {view.stops.map((stop, index) => <span key={index} className="conversation-navigator-tick" data-active={index === nearest(view.progress) || undefined} style={{ top: `${Math.min(1, stop.top / view.max) * 100}%` }} />)}
      {view.markers.map((marker, index) => <span key={`marker-${index}`} className="conversation-navigator-event" data-kind={marker.kind} style={{ top: `${marker.top * 100}%`, opacity: Math.min(1, .65 + Math.log2(marker.weight) * .12) }} />)}
      <span className="conversation-navigator-thumb" style={{ top: `${view.progress * (100 - Math.max(5, view.fraction * 100))}%`, height: `${Math.max(5, view.fraction * 100)}%` }} />
    </div>
    {hover !== null && <div className="conversation-navigator-preview" style={{ top: `clamp(56px, ${hover * 100}%, calc(100% - 40px))` }}>
      <span>Turn {selected + 1} <span aria-hidden="true">/</span> {view.stops.length}</span>
      <p>{view.stops[selected].text}</p>
      <span className="conversation-navigator-stats">{view.stops[selected].tools} tool calls</span>
      <span className="conversation-navigator-hint">Drag to skim · ↑ ↓ to jump</span>
    </div>}
  </nav>;
}
