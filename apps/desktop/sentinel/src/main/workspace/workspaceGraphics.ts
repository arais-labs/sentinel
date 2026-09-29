import type { WorkspaceDesktop } from './workspaceDesktop.js';
import type { WorkspaceDistribution } from './workspaceDistributions.js';
import type { WorkspaceRuntime } from './workspaceRuntime.js';

/** Installation executes on the VM's owner, using that worker's bundled assets. */
export class WorkspaceGraphics {
  constructor(private readonly runtime: WorkspaceRuntime) {}
  async install(workspace: string, desktop: WorkspaceDesktop, distribution: WorkspaceDistribution = 'alpine') {
    await this.runtime.request('graphics_install', { workspace, desktop, distribution }, 20 * 60_000);
  }
}
