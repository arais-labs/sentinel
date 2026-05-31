export function instancePrefixFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/instances\/([^/]+)/);
  return match?.[1] ? `/instances/${match[1]}` : null;
}

export function instanceRouteFromPath(pathname: string, path: string): string {
  const prefix = instancePrefixFromPath(pathname);
  if (!prefix) return '/';
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${prefix}${normalizedPath}`;
}

export function instanceRoute(instanceName: string, path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `/instances/${encodeURIComponent(instanceName)}${normalizedPath}`;
}
