import { KeyRound, Laptop, type LucideIcon } from 'lucide-react';
import type { MachineProvider } from '../types/api';

export interface MachineProviderTile {
  id: MachineProvider;
  label: string;
  description: string;
  icon: LucideIcon;
}

/** Single catalog of selectable runtime providers, shared by the Workspaces and
 *  Onboarding choosers so labels/descriptions/icons can't drift between them. */
export const MACHINE_PROVIDER_TILES: MachineProviderTile[] = [
  { id: 'local', label: 'Local (this Mac)', description: 'Run directly on this Mac, sandboxed to a workspace.', icon: Laptop },
  { id: 'ssh', label: 'Custom SSH', description: 'Bring your own SSH host.', icon: KeyRound },
];

/** Build the POST /machines body for a managed provider. */
export function buildManagedMachineBody(
  provider: Exclude<MachineProvider, 'ssh'>,
  name: string,
) {
  return { provider, name };
}
