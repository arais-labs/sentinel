import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

const source = await readFile(new URL('../src/lib/model-selection.ts', import.meta.url), 'utf8');
const { code } = await transformWithOxc(source, 'model-selection.ts');
const { resolveModelSelection } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
const codex = { provider_id: 'openai-codex', model: 'gpt-5.6-sol', reasoning_levels: ['low', 'medium', 'high'], supports_fast_mode: true };
const ollama = { provider_id: 'ollama', model: 'local-model', reasoning_levels: [], supports_fast_mode: false };
const model = { tier: 'normal', label: 'Normal', description: '', provider_options: [codex, ollama] };

test('a saved Anthropic choice does not display or submit Codex when Anthropic is missing', () => {
  const saved = { provider_id: 'anthropic', reasoning_level: 'high', fast_mode: false };
  const resolved = resolveModelSelection(model, saved);
  assert.equal(resolved.provider, undefined);
  assert.deepEqual(resolved.choice, saved);
});

test('selecting Codex resolves the displayed provider and outgoing choice together', () => {
  const saved = { provider_id: 'openai-codex', reasoning_level: 'high', fast_mode: true };
  const resolved = resolveModelSelection(model, saved);
  assert.equal(resolved.provider, codex);
  assert.equal(resolved.provider.provider_id, resolved.choice.provider_id);
  assert.deepEqual(resolved.choice, saved);
});

test('default routing remains unpinned as the primary provider changes', () => {
  const first = resolveModelSelection(model, {});
  const next = resolveModelSelection({ ...model, provider_options: [ollama, codex] }, first.choice);
  assert.equal(first.provider, codex);
  assert.equal(next.provider, ollama);
  assert.equal(first.choice.provider_id, undefined);
  assert.equal(next.choice.provider_id, undefined);
});

test('unsupported reasoning and speed settings are cleared without mutating the saved choice', () => {
  const saved = { provider_id: 'ollama', reasoning_level: 'high', fast_mode: true };
  assert.deepEqual(resolveModelSelection(model, saved).choice, {
    provider_id: 'ollama', reasoning_level: undefined, fast_mode: false,
  });
  assert.equal(saved.reasoning_level, 'high');
  assert.equal(saved.fast_mode, true);
});

test('an unloaded or empty catalogue never discards an explicit selection', () => {
  const saved = { provider_id: 'openai-codex' };
  for (const catalogue of [undefined, { ...model, provider_options: [] }]) {
    const resolved = resolveModelSelection(catalogue, saved);
    assert.equal(resolved.provider, undefined);
    assert.deepEqual(resolved.choice, saved);
  }
  assert.equal(resolveModelSelection(model, saved).provider, codex);
});
