import { useEffect, useState } from 'react';
import { Download, FileWarning, Loader2 } from 'lucide-react';
import { apiUrl } from '../../lib/api';

export function mediaKind(type: string | undefined): 'image' | 'audio' | 'video' | 'pdf' | null {
  if (type === 'application/pdf') return 'pdf';
  if (type?.startsWith('image/')) return 'image';
  if (type?.startsWith('audio/')) return 'audio';
  if (type?.startsWith('video/')) return 'video';
  return null;
}

export function MediaFilePreview({ instance, workspaceId, path, type, onDownload }: {
  instance: string; workspaceId: string; path: string; type: string; onDownload: () => void;
}) {
  const kind = mediaKind(type);
  const url = apiUrl(`/instances/${encodeURIComponent(instance)}/workspaces/${workspaceId}/browse/content?path=${encodeURIComponent(path)}`);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    setReady(false); setError('');
    void fetch(url, { method: 'HEAD', signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(response.status === 404 ? 'This file no longer exists.' : 'Could not open this file. Try downloading it.');
      setReady(true);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not open this file.'); });
    return () => controller.abort();
  }, [url]);
  const failed = () => setError('This file’s format or codec cannot be previewed here. Download it to open it in another app.');
  if (error || !kind) return <div className="project-file-state" role="alert"><FileWarning size={28} /><p>{error || 'This file cannot be previewed here.'}</p><button className="project-preview-download" onClick={onDownload}><Download size={14} />Download file</button></div>;
  if (!ready) return <div className="project-file-state" role="status"><Loader2 size={24} className="animate-spin" /><p>Opening preview…</p></div>;
  if (kind === 'pdf') return <iframe className="project-pdf-preview" title={`PDF preview: ${path}`} src={`${url}#view=FitH&navpanes=0`} onError={failed} />;
  return <div className="project-media-preview">
    {kind === 'image' ? <img src={url} alt={path.split('/').pop() || 'File preview'} onError={failed} />
      : kind === 'audio' ? <audio key={url} controls preload="metadata" src={url} onError={failed} aria-label={`Audio preview: ${path}`} />
        : <video key={url} controls preload="metadata" src={url} onError={failed} aria-label={`Video preview: ${path}`} />}
  </div>;
}
