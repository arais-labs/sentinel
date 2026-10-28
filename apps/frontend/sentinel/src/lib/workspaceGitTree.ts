import type { SessionWorkspaceGitChangedFile, SessionWorkspaceGitChangedFilesResponse } from '../types/api';

export type WorkspaceGitChangedTreeNode =
  | {
      kind: 'directory';
      key: string;
      name: string;
      fullPath: string;
      children: WorkspaceGitChangedTreeNode[];
      fileCount: number;
    }
  | {
      kind: 'file';
      key: string;
      name: string;
      fullPath: string;
      entry: SessionWorkspaceGitChangedFile;
    };

type WorkspaceGitChangedFileNode = Extract<WorkspaceGitChangedTreeNode, { kind: 'file' }>;
type WorkspaceGitChangedDirectoryNode = Extract<WorkspaceGitChangedTreeNode, { kind: 'directory' }>;

interface MutableDirectoryNode {
  kind: 'directory';
  key: string;
  name: string;
  fullPath: string;
  children: Map<string, MutableDirectoryNode | WorkspaceGitChangedFileNode>;
  fileCount: number;
}

function makeDirectory(name: string, fullPath: string): MutableDirectoryNode {
  return {
    kind: 'directory',
    key: `dir:${fullPath || '.'}`,
    name,
    fullPath,
    children: new Map(),
    fileCount: 0,
  };
}

function freezeDirectory(node: MutableDirectoryNode): WorkspaceGitChangedDirectoryNode {
  const children = Array.from(node.children.values())
    .map((child): WorkspaceGitChangedTreeNode => (child.kind === 'directory' ? freezeDirectory(child) : child))
    .sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'directory' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  return {
    kind: 'directory',
    key: node.key,
    name: node.name,
    fullPath: node.fullPath,
    children,
    fileCount: node.fileCount,
  };
}

export function buildWorkspaceGitChangedTree(
  payload: SessionWorkspaceGitChangedFilesResponse | null | undefined,
): WorkspaceGitChangedTreeNode[] {
  const root = makeDirectory('', '');
  const entries = Array.isArray(payload?.entries) ? payload.entries : [];

  for (const entry of entries) {
    const cleanPath = entry.path.split('/').filter(Boolean);
    if (cleanPath.length === 0) continue;

    let current = root;
    current.fileCount += 1;

    for (let index = 0; index < cleanPath.length; index += 1) {
      const part = cleanPath[index];
      const fullPath = cleanPath.slice(0, index + 1).join('/');
      const isFile = index === cleanPath.length - 1;

      if (isFile) {
        current.children.set(fullPath, {
          kind: 'file',
          key: `file:${fullPath}`,
          name: part,
          fullPath,
          entry,
        });
        continue;
      }

      const existing = current.children.get(fullPath);
      if (existing?.kind === 'directory' && 'children' in existing && existing.children instanceof Map) {
        existing.fileCount += 1;
        current = existing;
        continue;
      }

      const next = makeDirectory(part, fullPath);
      next.fileCount = 1;
      current.children.set(fullPath, next);
      current = next;
    }
  }

  return freezeDirectory(root).children;
}
