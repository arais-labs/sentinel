export function resolveFilesWorkspace<T extends { id: string }>(workspaces: T[], selected: string, pinned: boolean, attached: T | null): T | undefined {
  // An attachment can arrive before the workspace list refreshes after creation.
  if (!pinned && attached) return attached;
  return workspaces.find(item => item.id === selected) ?? workspaces[0];
}
