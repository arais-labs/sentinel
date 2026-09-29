// Bump only for a breaking worker contract, not for each build or feature.
// An absent version is the original protocol. Never infer versions from fields.
export const RUNTIME_PROTOCOL = 2;

export class RuntimeCompatibilityError extends Error {
  readonly code: 'runtime_update_required' | 'app_update_required';
  readonly details: { installed: number; required: number; capability?: string };
  constructor(installed: number, remote = false, capability?: string) {
    const older = installed < RUNTIME_PROTOCOL || Boolean(capability);
    super(!remote ? 'Restart Sentinel to load its bundled runtime. If this persists, update Sentinel.'
      : older ? 'Update Sentinel Runtime on this machine to use its workspaces.' : 'Update Sentinel to connect to this runtime.');
    this.code = remote && older ? 'runtime_update_required' : 'app_update_required';
    this.details = { installed, required: RUNTIME_PROTOCOL, ...(capability ? {capability} : {}) };
  }
}

export function requireRuntimeCapability(capabilities: string[] | undefined, capability: string, remote = false): void {
  if (!capabilities?.includes(capability)) throw new RuntimeCompatibilityError(RUNTIME_PROTOCOL, remote, capability);
}

export function requireRuntimeProtocol(installed: number, remote = false): void {
  if (installed !== RUNTIME_PROTOCOL) throw new RuntimeCompatibilityError(installed, remote);
}
