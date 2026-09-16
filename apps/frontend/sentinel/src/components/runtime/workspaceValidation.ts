import { ApiError } from '../../lib/api';

export function projectDirectoryError(value: string): string | null {
  const path = value.trim();
  if (!path) return null;
  if (/[\0\n\r]/.test(path)) return 'Choose a folder path without control characters.';
  if (!path.startsWith('/')) return 'Enter the full folder path, starting with /, or use Browse.';
  const parts = path.split('/').filter(part => part && part !== '.');
  if (!parts.length) return 'Choose a project folder, not the filesystem root (/). This folder is shared with your workspace.';
  if (parts.includes('..')) return 'Choose a folder path without .., or use Browse.';
  return null;
}

export function workspaceSaveError(error: unknown): string {
  if (error instanceof ApiError && error.status === 422 && Array.isArray(error.details)) {
    const labels: Record<string, string> = { name: 'Name', directory: 'Project folder', machine_id: 'Machine',
      development_tools: 'Tools', distribution: 'Operating system', resources: 'Resources',
      cpus: 'CPUs', memory_gib: 'Memory', disk_gib: 'Disk' };
    const messages = error.details.flatMap(detail => {
      if (!detail || typeof detail.msg !== 'string') return [];
      const field = Array.isArray(detail.loc) ? detail.loc.filter((key: unknown) => key !== 'body').map((key: string) => labels[key] ?? key).join(' · ') : '';
      return [`${field ? `${field}: ` : ''}${detail.msg.replace(/^Value error, /, '')}`];
    });
    if (messages.length) return messages.join('\n');
  }
  return error instanceof Error ? error.message : 'Could not save workspace';
}
