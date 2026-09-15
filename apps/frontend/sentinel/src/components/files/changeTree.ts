import type { Change, FileTab } from './types';

export type ChangeNode = { name: string; path: string; children: ChangeNode[]; change?: Change; count: number };
export function relativeChangePath(path: string, root: string): string {
  return root && path.startsWith(`${root}/`) ? path.slice(root.length + 1) : path;
}
export function changeMode(change: Change): FileTab['mode'] {
  if (change.conflicted || change.untracked || change.staged && change.unstaged) return 'combined';
  return change.staged ? 'staged' : 'working';
}

// Build from Git rather than the filesystem: deleted files still have a place
// in their original directory, and filtering keeps every matching ancestor.
export function buildChangeTree(changes: Change[], root: string, query = ''): ChangeNode[] {
  type Branch = { node: ChangeNode; children: Map<string, Branch> };
  const top = new Map<string, Branch>();
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  for (const change of changes) {
    const relative = relativeChangePath(change.path, root);
    if (!terms.every(term => relative.toLocaleLowerCase().includes(term))) continue;
    const segments = relative.split('/');
    let children = top;
    let path = root;
    segments.forEach((name, index) => {
      path = path ? `${path}/${name}` : name;
      let branch = children.get(name);
      if (!branch) {
        branch = { node: { name, path, children: [], count: 0 }, children: new Map() };
        children.set(name, branch);
      }
      branch.node.count++;
      if (index === segments.length - 1) branch.node.change = change;
      children = branch.children;
    });
  }
  function finish(children: Map<string, Branch>): ChangeNode[] {
    return [...children.values()].map(({ node, children }) => {
      node.children = finish(children);
      // Compact only directory chains; never swallow the final filename.
      while (!node.change && node.children.length === 1 && !node.children[0].change) {
        const child = node.children[0];
        node = { ...child, name: `${node.name}/${child.name}` };
      }
      return node;
    }).sort((a, b) => Number(Boolean(a.change)) - Number(Boolean(b.change)) || a.name.localeCompare(b.name, undefined, { numeric: true }));
  }
  return finish(top);
}
