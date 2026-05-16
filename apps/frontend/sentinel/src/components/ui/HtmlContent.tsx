import { useEffect, useMemo, useRef, useState, type HTMLAttributes } from 'react';
import { createPortal } from 'react-dom';
import { Maximize2, X } from 'lucide-react';

const HTML_MARKER = /^<!--\s*sentinel:html\s*-->\s*/i;
const HTML_START = /^<!--\s*sentinel:html\s*-->|^<!doctype\s+html\b|^<html[\s>]|^<body[\s>]/i;
const MIN_HEIGHT = 180;
// No max height: the parent chat container is already scrollable. Capping
// here would force the iframe to scroll internally on top of that.
const MAX_HEIGHT = Number.POSITIVE_INFINITY;

interface HtmlContentProps extends Omit<HTMLAttributes<HTMLDivElement>, 'children'> {
  content: string;
}

export function looksLikeHtmlContent(content: string): boolean {
  return HTML_START.test(content.trimStart());
}

function clampHeight(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.ceil(value)));
}

function normalizeHtml(content: string): string {
  return content.trimStart().replace(HTML_MARKER, '');
}

function resizeBridge(id: string): string {
  return `
<script>
(() => {
  const id = ${JSON.stringify(id)};
  const postHeight = () => {
    const body = document.body;
    const root = document.documentElement;
    const height = Math.max(
      body ? body.scrollHeight : 0,
      body ? body.offsetHeight : 0,
      root ? root.scrollHeight : 0,
      root ? root.offsetHeight : 0
    );
    parent.postMessage({ source: 'sentinel-html-artifact', id, height }, '*');
  };
  window.addEventListener('load', postHeight);
  if (window.ResizeObserver) {
    new ResizeObserver(postHeight).observe(document.documentElement);
    if (document.body) new ResizeObserver(postHeight).observe(document.body);
  }
  setTimeout(postHeight, 0);
  setTimeout(postHeight, 250);
})();
</script>`;
}

function withResizeBridge(content: string, id: string): string {
  const html = normalizeHtml(content);
  const bridge = resizeBridge(id);
  if (/<\/body\s*>/i.test(html)) {
    return html.replace(/<\/body\s*>/i, `${bridge}</body>`);
  }
  return `${html}${bridge}`;
}

export function HtmlContent({ content, className = '', ...rest }: HtmlContentProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null);
  const fullscreenIframeRef = useRef<HTMLIFrameElement | null>(null);
  const idRef = useRef(`html-${crypto.randomUUID()}`);
  const fullscreenIdRef = useRef(`html-fs-${crypto.randomUUID()}`);
  const [height, setHeight] = useState(MIN_HEIGHT);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const inlineSrcDoc = useMemo(
    () => withResizeBridge(content, idRef.current),
    [content],
  );
  const fullscreenSrcDoc = useMemo(
    () => withResizeBridge(content, fullscreenIdRef.current),
    [content],
  );

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      const data = event.data as { source?: unknown; id?: unknown; height?: unknown };
      if (data?.source !== 'sentinel-html-artifact' || data.id !== idRef.current) return;
      if (event.source !== iframeRef.current?.contentWindow) return;
      const nextHeight = clampHeight(data.height);
      if (nextHeight !== null) setHeight(nextHeight);
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  useEffect(() => {
    if (!isFullscreen) return;
    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setIsFullscreen(false);
    }
    window.addEventListener('keydown', handleKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', handleKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [isFullscreen]);

  return (
    <>
      <div className={`html-artifact group/htmlartifact relative ${className}`.trim()} {...rest}>
        <iframe
          ref={iframeRef}
          title="Assistant HTML"
          sandbox="allow-scripts allow-forms allow-popups allow-modals"
          referrerPolicy="no-referrer"
          srcDoc={inlineSrcDoc}
          style={{ height }}
        />
        <button
          type="button"
          onClick={() => setIsFullscreen(true)}
          title="Expand"
          aria-label="Expand artifact"
          className="absolute top-2 right-2 z-10 inline-flex items-center justify-center w-7 h-7 rounded-lg bg-[color:var(--surface-0)]/85 hover:bg-[color:var(--surface-0)] border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:text-[color:var(--text-primary)] backdrop-blur-sm shadow-sm opacity-0 group-hover/htmlartifact:opacity-100 focus-visible:opacity-100 transition-opacity"
        >
          <Maximize2 size={13} />
        </button>
      </div>
      {isFullscreen
        ? createPortal(
            <div
              className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/70 backdrop-blur-sm p-6 animate-in fade-in duration-150"
              onClick={() => setIsFullscreen(false)}
            >
              <div
                className="relative w-full h-full max-w-[1600px] rounded-2xl overflow-hidden bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] shadow-2xl"
                onClick={(e) => e.stopPropagation()}
              >
                <iframe
                  ref={fullscreenIframeRef}
                  title="Assistant HTML (fullscreen)"
                  sandbox="allow-scripts allow-forms allow-popups allow-modals"
                  referrerPolicy="no-referrer"
                  srcDoc={fullscreenSrcDoc}
                  className="block w-full h-full border-0 bg-transparent"
                  style={{ colorScheme: 'normal' }}
                />
                <button
                  type="button"
                  onClick={() => setIsFullscreen(false)}
                  title="Close (Esc)"
                  aria-label="Close fullscreen"
                  className="absolute top-3 right-3 z-10 inline-flex items-center justify-center w-9 h-9 rounded-full bg-[color:var(--surface-0)]/85 hover:bg-[color:var(--surface-0)] border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] hover:text-[color:var(--text-primary)] backdrop-blur-sm shadow-md transition-colors"
                >
                  <X size={16} />
                </button>
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
