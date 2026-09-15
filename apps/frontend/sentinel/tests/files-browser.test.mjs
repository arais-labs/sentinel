import assert from 'node:assert/strict';
import test from 'node:test';
import { buildChangeTree, changeMode, relativeChangePath } from '../src/components/files/changeTree.ts';
import { resolveFilesWorkspace } from '../src/components/files/workspaceSelection.ts';
import { gitFileStatus } from '../src/components/files/gitStatus.ts';
const change = (path, status, extra = {}) => ({ path, status, staged: status[0] !== ' ' && status !== '??', unstaged: status[1] !== ' ' && status !== '??', untracked: status === '??', ...extra });
const flatten = nodes => nodes.flatMap(node => node.change ? [node.change] : flatten(node.children));

test('changed tree is repository-relative, compacts folders and retains deleted/renamed files', () => {
  const changes = [change('trees/feature/src/api/old.ts', ' D'), change('trees/feature/src/api/new.ts', 'R ', { original_path: 'trees/feature/src/api/before.ts' }), change('trees/feature/README.md', ' M')];
  const nodes = buildChangeTree(changes, 'trees/feature');
  assert.deepEqual(nodes.map(node => node.name), ['src/api', 'README.md']);
  assert.equal(nodes[0].path, 'trees/feature/src/api');
  assert.equal(nodes[0].count, 2);
  assert.deepEqual(nodes[0].children.map(node => node.name), ['new.ts', 'old.ts']);
  assert.equal(flatten(nodes).find(item => item.status === ' D').path, changes[0].path);
  assert.equal(flatten(nodes).find(item => item.status === 'R ').original_path, changes[1].original_path);
});
test('search keeps matching ancestors, handles dotfiles and does not confuse root prefixes', () => {
  const changes = [change('repo/.config/app.json', ' M'), change('repo/src/app.ts', 'A ')];
  const nodes = buildChangeTree(changes, 'repo', 'JSON config');
  assert.equal(nodes[0].name, '.config');
  assert.equal(nodes[0].children[0].name, 'app.json');
  assert.equal(buildChangeTree(changes, 'repo', 'missing').length, 0);
  assert.equal(relativeChangePath('repo-two/app', 'repo'), 'repo-two/app');
  assert.equal(relativeChangePath('app', ''), 'app');
});
test('tree opens the correct comparison for staged, unstaged, mixed and unversioned changes', () => {
  assert.equal(changeMode(change('a', 'A ')), 'staged');
  assert.equal(changeMode(change('a', ' D')), 'working');
  assert.equal(changeMode(change('a', 'MM')), 'combined');
  assert.equal(changeMode(change('a', '??')), 'combined');
  assert.equal(changeMode(change('a', 'UU', { conflicted: true })), 'combined');
});
test('following switches to the attachment, pinning holds selection, detached sessions retain fallback', () => {
  const a = { id: 'a' }, b = { id: 'b' }, fresh = { id: 'new' };
  assert.equal(resolveFilesWorkspace([a, b], 'a', false, b), b);
  assert.equal(resolveFilesWorkspace([a, b], 'a', true, b), a);
  assert.equal(resolveFilesWorkspace([a, b], 'b', false, null), b);
  assert.equal(resolveFilesWorkspace([a, b], 'missing', true, null), a);
  assert.equal(resolveFilesWorkspace([a, b], 'a', false, fresh), fresh);
  assert.equal(resolveFilesWorkspace([], '', false, null), undefined);
});
test('Git file styling distinguishes unversioned, conflict, staged deletion, local deletion, rename and index state', () => {
  assert.equal(gitFileStatus(change('a', '??')), 'unversioned');
  assert.equal(gitFileStatus(change('a', 'UU', { conflicted: true })), 'conflict');
  assert.equal(gitFileStatus(change('a', 'A ')), 'added');
  assert.equal(gitFileStatus(change('a', ' M')), 'modified');
  assert.equal(gitFileStatus(change('a', 'D ')), 'deleted');
  assert.equal(gitFileStatus(change('a', ' D')), 'missing');
  assert.equal(gitFileStatus(change('a', 'RM'), 'staged'), 'renamed');
  assert.equal(gitFileStatus(change('a', 'RM'), 'working'), 'modified');
  assert.equal(gitFileStatus(undefined), undefined);
});
