import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiUrl } from '../../lib/api';

export function HtmlFilePreview({ instance, workspaceId, path }: { instance: string; workspaceId: string; path: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const limit = 5 * 1024 * 1024;
    setHtml(null);
    setError('');
    const url = apiUrl(`/instances/${encodeURIComponent(instance)}/workspaces/${workspaceId}/browse/content?path=${encodeURIComponent(path)}`);
    // Bound the response itself, not only the rendered text after downloading it.
    void fetch(url, { headers: { Range: `bytes=0-${limit}` }, signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('Could not load HTML preview.');
        const text = await response.blob();
        if (text.size > limit) throw new Error('This HTML file is too large to preview. Download it to open it in a browser.');
        const html = await text.text();
        if (!controller.signal.aborted) setHtml(html);
      }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load HTML preview.'); });
    return () => controller.abort();
  }, [instance, workspaceId, path]);
  if (error) return <p className="project-inline-error" role="alert">{error}</p>;
  if (html === null) return <p className="project-hint"><Loader2 size={14} className="animate-spin" />Loading HTML preview…</p>;
  return <iframe className="project-html-preview" title={`HTML preview: ${path}`} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={html} />;
}
