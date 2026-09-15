import assert from 'node:assert/strict';
import test from 'node:test';
import { previewOrigin, previewTargetPath } from '../../.test-dist/shared/preview.js';
const link = '/api/v1/instances/demo/sessions/97394c11-0f16-465e-953f-8a1b6640d981/runtime/forwards/pf-bebf36e56cff/';
test('forward links resolve to the desktop target endpoint for their instance', () => {
  const target = link.replace('/forwards/', '/forward-target/').replace(/\/$/, '');
  assert.equal(previewTargetPath(link), target);
  assert.equal(previewTargetPath(`sentinel://app${link}`), target);
});
test('arbitrary URLs and backend endpoints cannot be opened as previews', () => {
  for (const value of [`https://evil.test${link}`, `//evil.test${link}`, '/api/v1/admin', '/instances/main', 'sentinel://other'+link]) assert.equal(previewTargetPath(value), null);
  for (const value of ['http://localhost:8000/', 'http://127.0.0.1/', 'http://127.0.0.1:8000/api', 'http://user:secret@127.0.0.1:8000/', 'sentinel://app/', 'https://example.com/']) assert.throws(() => previewOrigin(value));
  assert.equal(previewOrigin('http://127.0.0.1:51823/'), 'http://127.0.0.1:51823');
});
