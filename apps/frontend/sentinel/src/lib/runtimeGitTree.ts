import type {
  SessionRuntimeGitChangedFile,
  SessionRuntimeGitChangedFilesResponse,
} from '../types/api';

export type RuntimeGitChangedTreeNode = {
  key: string;
  name: string;
  fullPath: string;
  kind: 'file' | 'directory';
  fileCount: number;
  children: RuntimeGitChangedTreeNode[];
  entry?: SessionRuntimeGitChangedFile;
};

type MutableTreeNode = {
  key: string;
  name: string;
  fullPath: string;
  kind: 'file' | 'directory';
  children: MutableTreeNode[];
  fileCount: number;
  entry?: SessionRuntimeGitChangedFile;
};

function trimPrefix(path: string, prefix: string): string {
  const normalizedPath = path.trim().replace(/^\/+|\/+$/g, '');
  const normalizedPrefix = prefix.trim().replace(/^\/+|\/+$/g, '');
  if (!normalizedPrefix) return normalizedPath;
  if (normalizedPath === normalizedPrefix) return '';
  if (normalizedPath.startsWith(`${normalizedPrefix}/`)) {
    return normalizedPath.slice(normalizedPrefix.length + 1);
  }
  return normalizedPath;
}

function sortNodes(nodes: MutableTreeNode[]): RuntimeGitChangedTreeNode[] {
  const sorted = [...nodes].sort((left, right) => {
    if (left.kind !== right.kind) {
      return left.kind === 'directory' ? -1 : 1;
    }
    return left.name.localeCompare(right.name);
  });
  return sorted.map((node) => {
    const children = sortNodes(node.children);
    const fileCount = node.kind === 'file'
      ? 1
      : children.reduce((sum, child) => sum + child.fileCount, 0);
    return {
      key: node.key,
      name: node.name,
      fullPath: node.fullPath,
      kind: node.kind,
      fileCount,
      children,
      entry: node.entry,
    };
  });
}

export function buildRuntimeGitChangedTree(
  payload: SessionRuntimeGitChangedFilesResponse | null,
): RuntimeGitChangedTreeNode[] {
  if (!payload?.entries?.length) return [];

  const basePath = (payload.path || payload.git_root || '').trim();
  const roots: MutableTreeNode[] = [];

  for (const entry of payload.entries) {
    const relativePath = trimPrefix(entry.path, basePath || payload.git_root || '');
    const parts = (relativePath || entry.path).split('/').filter(Boolean);
    if (!parts.length) continue;

    let cursor = roots;
    let currentPath = basePath;

    for (const [index, part] of parts.entries()) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const isLeaf = index === parts.length - 1;
      let node = cursor.find((candidate) => candidate.name === part && candidate.kind === (isLeaf ? 'file' : 'directory'));
      if (!node) {
        node = {
          key: currentPath,
          name: part,
          fullPath: currentPath,
          kind: isLeaf ? 'file' : 'directory',
          children: [],
          fileCount: 0,
          entry: isLeaf ? entry : undefined,
        };
        cursor.push(node);
      }
      if (isLeaf) {
        node.entry = entry;
      } else {
        cursor = node.children;
      }
    }
  }

  return sortNodes(roots);
}
