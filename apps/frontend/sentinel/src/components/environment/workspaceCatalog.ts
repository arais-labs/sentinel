import type { WorkspaceCatalog, WorkspaceMinimum } from '../../types/api';

/** "chromium" selects the distribution's default browser. */
export const defaultBrowser = 'chromium';
const browserCategory = 'Browsers';

export type WorkspaceSelection = { desktop: string; browser: string; tools: string[] };

const availableOn = (distribution: string) => (item: { distributions: string[] }) => item.distributions.includes(distribution);

export function workspaceChoices(catalog: WorkspaceCatalog, distribution: string) {
  const available = availableOn(distribution);
  const tools = catalog.tools.filter(available);
  const ids = new Set(tools.map(tool => tool.id));
  return {
    desktops: catalog.desktops.filter(available),
    browsers: tools.filter(tool => tool.category === browserCategory),
    tools: tools.filter(tool => tool.category !== browserCategory),
    stacks: catalog.stacks.filter(stack => stack.tools.every(id => ids.has(id))),
  };
}

/** Drops selections the distribution cannot run. */
export function availableSelection(catalog: WorkspaceCatalog, distribution: string, selection: WorkspaceSelection): WorkspaceSelection {
  const choices = workspaceChoices(catalog, distribution);
  return {
    desktop: choices.desktops.some(item => item.id === selection.desktop) ? selection.desktop : 'none',
    browser: choices.browsers.some(item => item.id === selection.browser) ? selection.browser : defaultBrowser,
    tools: selection.tools.filter(id => choices.tools.some(tool => tool.id === id)),
  };
}

/** Views named after guest features the distribution does not declare in its catalog entry. */
const featureViews = ['terminal', 'files'];

export function unsupportedViews(catalog: WorkspaceCatalog, distribution: string): string[] {
  const features = catalog.distributions.find(item => item.id === distribution)?.features;
  return features ? featureViews.filter(view => !features.includes(view)) : [];
}

export type WorkspaceResources = { cpus: number; memory_gib: number; disk_gib: number };

/** Raises each allocation below the distribution's catalog `minimum` to it. */
export function withMinimum(resources: WorkspaceResources, distribution?: { minimum?: WorkspaceMinimum }): WorkspaceResources {
  const minimum = distribution?.minimum ?? {};
  return {
    cpus: Math.max(resources.cpus, minimum.cpus ?? 0),
    memory_gib: Math.max(resources.memory_gib, minimum.memory_gib ?? 0),
    disk_gib: Math.max(resources.disk_gib, minimum.disk_gib ?? 0),
  };
}
