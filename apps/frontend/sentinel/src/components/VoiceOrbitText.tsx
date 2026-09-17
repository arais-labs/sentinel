import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { voiceCaptionSentences } from '../lib/voice-captions';

type Tone = 'user' | 'assistant' | 'status';

/** One sentence beneath the ring, faded out before the next caption appears. */
/** `hold` keeps the current caption up while audio plays; playback replaces it with the next sentence. */
export function VoiceOrbitText({ label, heard, reply, hold = false }: { label: string; heard: string; reply: string; hold?: boolean }) {
  const node = useRef<HTMLHeadingElement>(null);
  const [metrics, setMetrics] = useState({ width: 0, font: '', spacing: 0 });
  const [finished, setFinished] = useState('');
  const transcript = reply || heard;
  const key = `${reply ? 'assistant' : 'user'}:${transcript}`;
  const active = !!transcript && finished !== key;
  const text = active ? transcript : label;
  const tone: Tone = active ? reply ? 'assistant' : 'user' : 'status';
  const [displayed, setDisplayed] = useState({ text: '', tone });
  const [leaving, setLeaving] = useState(false);
  const lastShown = useRef({ tone, time: 0 });
  // Captions already animated for the current speaker; a growing reply resumes after them.
  const shown = useRef<{ tone: Tone; captions: string[] }>({ tone, captions: [] });

  useLayoutEffect(() => {
    const element = node.current;
    if (!element) return;
    const measure = () => {
      if (!element.clientWidth) return;
      const style = getComputedStyle(element);
      const next = { width: element.clientWidth - 8, font: style.font, spacing: parseFloat(style.letterSpacing) || 0 };
      setMetrics(previous => previous.width === next.width && previous.font === next.font && previous.spacing === next.spacing ? previous : next);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    document.fonts.addEventListener('loadingdone', measure);
    measure();
    return () => { observer.disconnect(); document.fonts.removeEventListener('loadingdone', measure); };
  }, [displayed.tone]);

  const captions = useMemo(() => {
    if (!metrics.width) return [];
    const context = document.createElement('canvas').getContext('2d');
    if (!context) return [text];
    context.font = metrics.font;
    return voiceCaptionSentences(text, metrics.width, value => context.measureText(value).width + Math.max(0, Array.from(value).length - 1) * metrics.spacing);
  }, [text, metrics]);

  useEffect(() => {
    if (!captions.length) return;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let timer: ReturnType<typeof setTimeout>;
    const readingTime = (index: number) => Math.max(1800, captions[index].trim().split(/\s+/u).length * 320);
    const show = (index: number) => {
      setLeaving(true);
      timer = setTimeout(() => {
        setDisplayed({ text: captions[index], tone });
        shown.current = { tone, captions: [...shown.current.captions.slice(0, index), captions[index]] };
        lastShown.current = { tone, time: performance.now() };
        setLeaving(false);
        if (index + 1 < captions.length || (active && !hold)) {
          timer = setTimeout(() => {
            if (index + 1 < captions.length) show(index + 1);
            else setFinished(key);
          }, readingTime(index));
        }
      }, reduced ? 0 : 180);
    };
    // A reply that grew by more sentences continues after the ones already animated.
    // A rewrapped or changed final sentence is shown again; anything else starts over.
    const previous = shown.current.tone === tone ? shown.current.captions : [];
    const settled = previous.slice(0, -1);
    const extends_ = settled.every((caption, index) => captions[index] === caption);
    let start = 0;
    if (extends_ && previous.length) start = captions[previous.length - 1] === previous[previous.length - 1] ? previous.length : previous.length - 1;
    if (!extends_) shown.current = { tone, captions: [] };
    if (start >= captions.length) {
      // Nothing new to animate; let the last caption finish its reading time.
      if (active && !hold) timer = setTimeout(() => setFinished(key), readingTime(captions.length - 1));
      return () => clearTimeout(timer);
    }
    // A quick model response still lets the user's text register first.
    const delay = start === 0 && tone === 'assistant' && lastShown.current.tone === 'user'
      ? Math.max(0, 900 - (performance.now() - lastShown.current.time)) : 0;
    timer = setTimeout(() => show(start), delay);
    return () => clearTimeout(timer);
  }, [captions, tone, active, key, hold]);

  return <div className={`voice-orbit-center${leaving ? ' is-leaving' : ''}`}>
    <div className="voice-status" role="status" aria-live="polite" aria-atomic="true">
      <h2 ref={node} className="voice-caption-line" data-tone={displayed.tone}>{displayed.text}</h2>
    </div>
  </div>;
}
