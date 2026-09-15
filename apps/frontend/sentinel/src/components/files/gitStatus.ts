import type { Change } from './types';
export type GitFileStatus = 'added' | 'modified' | 'deleted' | 'missing' | 'unversioned' | 'conflict' | 'renamed' | 'copied';
export function gitFileStatus(change?: Change, area?: 'staged' | 'working'): GitFileStatus | undefined {
  if (!change) return undefined;
  if (change.conflicted) return 'conflict';
  if (change.untracked) return 'unversioned';
  const code = area === 'staged' ? change.status[0] : area === 'working' ? change.status[1] : change.status.includes('D') ? 'D' : change.status[0] !== ' ' ? change.status[0] : change.status[1];
  switch (code) {
    case 'A': return 'added';
    case 'C': return 'copied';
    case 'R': return 'renamed';
    case 'D': return change.status[0] === 'D' && area !== 'working' ? 'deleted' : 'missing';
    case 'U': return 'conflict';
    default: return 'modified';
  }
}
export const gitStatusLabel = (change?: Change, area?: 'staged' | 'working'): string => {
  const status = gitFileStatus(change, area);
  if (!status) return '';
  return ({ added: 'Added', modified: 'Modified', deleted: 'Deleted', missing: 'Deleted locally', unversioned: 'Unversioned', conflict: 'Conflict', renamed: 'Renamed', copied: 'Copied' })[status];
};
