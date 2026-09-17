import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const source = (await readFile(new URL('../src/lib/model-catalog.ts', import.meta.url), 'utf8'))
  .replace("import { requestJson } from './api';", 'const requestJson = (...args) => globalThis.catalogRequest(...args);');
const { code } = await transformWithOxc(source, 'model-catalog.ts');
const { loadModelCatalog, invalidateModelCatalog } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

function mockRequest(t, implementation) {
  const previous = globalThis.catalogRequest;
  globalThis.catalogRequest = implementation;
  t.after(() => { globalThis.catalogRequest = previous; });
}

test('several panes share one in-flight read, but later opens refresh', async t => {
  const calls = [];
  mockRequest(t, (path, options) => new Promise(resolve => calls.push({ path, options, resolve })));
  const first = loadModelCatalog('alpha');
  assert.equal(loadModelCatalog('alpha'), first);
  assert.equal(calls.length, 1);
  const beta = loadModelCatalog('beta');
  assert.equal(calls.length, 2);
  calls[0].resolve({ models: ['anthropic'] });
  calls[1].resolve({ models: ['codex'] });
  assert.deepEqual(await first, { models: ['anthropic'] });
  assert.deepEqual(await beta, { models: ['codex'] });
  const refreshed = loadModelCatalog('alpha');
  assert.equal(calls.length, 3);
  calls[2].resolve({ models: ['anthropic', 'codex'] });
  await refreshed;
});

test('settings invalidation prevents joining the old request and keeps instance isolation', async t => {
  const calls = [];
  mockRequest(t, (path, options) => new Promise(resolve => calls.push({ path, options, resolve })));
  const old = loadModelCatalog('alpha');
  const beta = loadModelCatalog('beta');
  invalidateModelCatalog('alpha');
  assert.equal(calls[0].options.signal.aborted, true);
  assert.equal(calls[1].options.signal.aborted, false);
  const fresh = loadModelCatalog('alpha');
  assert.notEqual(old, fresh);
  calls[0].resolve({ models: [] });
  await old;
  assert.equal(loadModelCatalog('alpha'), fresh, 'old completion cannot clear the newer request');
  calls[1].resolve({ models: [] });
  calls[2].resolve({ models: ['anthropic'] });
  await Promise.all([beta, fresh]);
});
