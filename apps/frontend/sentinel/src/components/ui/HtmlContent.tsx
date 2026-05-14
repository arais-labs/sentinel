import { useEffect, useRef, useState, type HTMLAttributes } from 'react';

const HTML_MARKER = /^<!--\s*sentinel:html\s*-->\s*/i;
const HTML_START = /^<!--\s*sentinel:html\s*-->|^<!doctype\s+html\b|^<html[\s>]|^<body[\s>]/i;
const MIN_HEIGHT = 180;
const MAX_HEIGHT = 1600;

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
  const idRef = useRef(`html-${crypto.randomUUID()}`);
  const [height, setHeight] = useState(MIN_HEIGHT);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.source !== iframeRef.current?.contentWindow) return;
      const data = event.data as { source?: unknown; id?: unknown; height?: unknown };
      if (data?.source !== 'sentinel-html-artifact' || data.id !== idRef.current) return;
      const nextHeight = clampHeight(data.height);
      if (nextHeight !== null) setHeight(nextHeight);
    }

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  return (
    <div className={`html-artifact ${className}`.trim()} {...rest}>
      <iframe
        ref={iframeRef}
        title="Assistant HTML"
        sandbox="allow-scripts allow-forms allow-popups allow-modals"
        referrerPolicy="no-referrer"
        srcDoc={withResizeBridge(content, idRef.current)}
        style={{ height }}
      />
    </div>
  );
}
