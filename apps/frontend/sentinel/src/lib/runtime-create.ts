import { Cpu, Container, KeyRound, Laptop, type LucideIcon } from 'lucide-react';
import type { RuntimeProvider } from '../types/api';

export interface RuntimeProviderTile {
  id: RuntimeProvider;
  label: string;
  description: string;
  icon: LucideIcon;
}

/** Single catalog of selectable runtime providers, shared by the Settings and
 *  Onboarding choosers so labels/descriptions/icons can't drift between them. */
export const RUNTIME_PROVIDER_TILES: RuntimeProviderTile[] = [
  { id: 'local', label: 'Local (this Mac)', description: 'Run on this machine over SSH.', icon: Laptop },
  { id: 'lima', label: 'Lima VM', description: 'Provisioned Linux VM via Lima.', icon: Cpu },
  { id: 'docker', label: 'Docker', description: 'Provisioned Linux container via Docker.', icon: Container },
  { id: 'ssh', label: 'Custom SSH', description: 'Bring your own SSH host.', icon: KeyRound },
];

/** Build the POST /runtimes body for a managed provider. `local` needs only a
 *  name (plus an optional workspace folder; blank → backend default); lima/docker
 *  carry a profile + desktop. */
export function buildManagedRuntimeBody(
  provider: Exclude<RuntimeProvider, 'ssh'>,
  name: string,
  opts?: { workspacesDir?: string },
) {
  if (provider === 'local') {
    const dir = opts?.workspacesDir?.trim();
    return dir ? { provider, name, workspaces_dir: dir } : { provider, name };
  }
  return {
    provider,
    name,
    profile: provider === 'lima' ? 'sentinel-linux-xfce' : 'sentinel-docker-linux',
    provider_config: { desktop: 'xfce' },
  };
}
