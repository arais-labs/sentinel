import type { MachineUsage } from '../../types/api';

/** 1024-based sizes with the app's KB/MB/GB labels (as the file browser shows them). */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export type UsageKind = 'workspaces' | 'systems' | 'packages' | 'sources' | 'installs' | 'distributions';

export const sharedLabels: Record<Exclude<UsageKind, 'workspaces'>, string> = {
  systems: 'Installed systems',
  packages: 'Package caches',
  sources: 'Downloads (sources)',
  installs: 'Installs in progress or failed',
  distributions: 'Your distributions',
};

export interface UsageSegment { kind: UsageKind | 'elsewhere' | 'free'; bytes: number; share: number }

/** The bar: Sentinel's parts, then the rest of the disk (used by others, then free) when its size is known. */
export function usageSegments(usage: MachineUsage): UsageSegment[] {
  const workspaces = usage.workspaces.reduce((sum, space) => sum + space.bytes, usage.other);
  const parts: Omit<UsageSegment, 'share'>[] = [
    { kind: 'workspaces', bytes: workspaces },
    ...(Object.keys(sharedLabels) as (keyof typeof sharedLabels)[]).map(kind => ({ kind, bytes: usage.shared[kind] })),
  ];
  const sentinel = parts.reduce((sum, part) => sum + part.bytes, 0);
  if (usage.capacity && usage.free !== null) {
    parts.push({ kind: 'elsewhere', bytes: Math.max(0, usage.capacity - usage.free - sentinel) }, { kind: 'free', bytes: usage.free });
  }
  const whole = parts.reduce((sum, part) => sum + part.bytes, 0) || 1;
  return parts.filter(part => part.bytes > 0).map(part => ({ ...part, share: part.bytes / whole }));
}
