import type { WorkspaceSpec } from '../workspaceLifecycle.js';

/** One metadata conversion; legacy tool IDs never enter normal provisioning. */
export function migrateDesktopSelection(entry: WorkspaceSpec): void {
  if (entry.desktop !== undefined) return;
  entry.desktop = entry.tools.includes('desktop') ? 'xfce' : 'none';
  entry.tools = entry.tools.filter(tool => tool !== 'desktop');
  const legacy = entry as WorkspaceSpec & { graphicsVersion?: number; desktopAppearanceVersion?: number; desktopComputerVersion?: number };
  delete legacy.graphicsVersion;
  delete legacy.desktopAppearanceVersion;
  delete legacy.desktopComputerVersion;
}
