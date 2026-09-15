import assert from 'node:assert/strict';
import test from 'node:test';
import { searchWorktrees } from '../src/components/files/worktreeSearch.ts';

const tree = (path, branch, extra = {}) => ({ path, branch, head: 'a1b2c3d4', available: true, current: false, locked: false, prunable: false, ...extra });
const fixtures = [
  tree('sample', 'main', { current: true, last_commit_at: 10 }),
  tree('sample-worktrees/api-client-refresh', 'fix/api-client-retry', { last_commit_at: 20 }),
  tree('sample-worktrees/release-2', 'release/v2', { last_commit_at: 30 }),
  tree('sample-worktrees/release-10', 'release/v10', { last_commit_at: 40 }),
  tree('sample-worktrees/local-stack-check', null, { head: 'deadbeef1234', locked: true }),
  tree('sample-worktrees/old-client-test', 'test/client', { available: false }),
];
const search = (query, options = {}, trees = fixtures) => searchWorktrees(trees, { query, kind: 'all', availability: 'all', sort: 'smart', ...options });
const paths = result => result.matches.map(match => match.tree.path);

test('exact names outrank fuzzy matches and the currently viewed checkout', () => {
  const result = search('main', {}, [tree('current', 'maintain', { current: true }), tree('exact', 'main'), tree('typo', 'pain')]);
  assert.equal(result.matches[0].tree.path, 'exact');
  assert.equal(result.matches[0].approximate, false);
});
test('matches unordered words across branch and folder, with typo tolerance', () => {
  assert.deepEqual(paths(search('refresh retry')), ['sample-worktrees/api-client-refresh']);
  const result = search('clinet retri');
  assert.deepEqual(paths(result), ['sample-worktrees/api-client-refresh']);
  assert.equal(result.matches[0].approximate, true);
});
test('understands initials and compact abbreviations', () => {
  assert.equal(search('facr').matches[0].tree.branch, 'fix/api-client-retry');
  assert.equal(search('apcli').matches[0].tree.branch, 'fix/api-client-retry');
});
test('field filters, exclusions, and literal phrases combine', () => {
  assert.deepEqual(paths(search('branch:fix path:refresh -test')), ['sample-worktrees/api-client-refresh']);
  assert.equal(search('branch:refresh').matches.length, 0);
  assert.equal(search('"clinet retri"').matches.length, 0);
  assert.deepEqual(paths(search('"api-client" -path:old')), ['sample-worktrees/api-client-refresh']);
  assert.equal(search('path:"My Project"', {}, [tree('My Project/work', 'main')]).matches.length, 1);
});
test('commit identifiers match prefixes, never fuzzy guesses when scoped', () => {
  assert.equal(search('commit:DEADBE').matches[0].tree.head, 'deadbeef1234');
  assert.equal(search('commit:daedbe').matches.length, 0);
});
test('status predicates and visible filters intersect, unavailable entries stay discoverable', () => {
  assert.deepEqual(paths(search('is:locked is:detached')), ['sample-worktrees/local-stack-check']);
  assert.deepEqual(paths(search('is:current')), ['sample']);
  assert.equal(search('is:unavailable', { availability: 'available' }).matches.length, 0);
  assert.deepEqual(paths(search('', { availability: 'unavailable' })), ['sample-worktrees/old-client-test']);
  assert.deepEqual(paths(search('', { kind: 'detached' })), ['sample-worktrees/local-stack-check']);
  assert.equal(search('-is:available').matches.length, 1);
  assert.match(search('is:banana').error, /Unknown filter/);
});
test('sorts by commit time, natural branch/folder order, or visits with current pinned', () => {
  assert.deepEqual(paths(search('', { sort: 'recent' })).slice(0, 3), ['sample', 'sample-worktrees/release-10', 'sample-worktrees/release-2']);
  for (const sort of ['branch', 'folder']) {
    const result = paths(search('release', { sort }));
    assert.deepEqual(result, ['sample-worktrees/release-2', 'sample-worktrees/release-10']);
  }
  assert.deepEqual(paths(search('', { sort: 'visited', visited: { 'sample-worktrees/local-stack-check': 100 } })).slice(0, 2), ['sample', 'sample-worktrees/local-stack-check']);
});
test('normalizes case and accents and accepts empty and punctuation-only queries', () => {
  assert.equal(search('CAFÉ', {}, [tree('cafe', 'main')]).matches.length, 1);
  assert.equal(search('  ').matches.length, fixtures.length);
  assert.equal(search('[]').matches.length, 0);
  assert.equal(search('a'.repeat(240)).matches.length, 0);
});
