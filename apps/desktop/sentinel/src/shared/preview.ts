/** Recognize tool links without treating arbitrary URLs as desktop commands. */
export function previewTargetPath(href: string): string | null {
  if (!href.startsWith('/') && !href.startsWith('sentinel://app/')) return null;
  const url = new URL(href, 'sentinel://app');
  if (url.protocol !== 'sentinel:' || url.host !== 'app') return null;
  const match = url.pathname.match(/^\/api\/v1\/instances\/([a-zA-Z0-9_-]+)\/sessions\/([a-fA-F0-9-]{36})\/runtime\/forwards\/(pf-[a-f0-9]+)\/?$/);
  return match ? `/api/v1/instances/${match[1]}/sessions/${match[2]}/runtime/forward-target/${match[3]}` : null;
}

export function previewOrigin(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid preview destination');
  const url = new URL(value);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Invalid preview destination');
  }
  return url.origin;
}
