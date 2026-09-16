import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

test('payload installation owns the preparation screen while services restart', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    server: { middlewareMode: true },
    optimizeDeps: { noDiscovery: true, include: [] },
    logLevel: 'error',
  });
  try {
    const { shouldShowDesktopPreparation } = await server.ssrLoadModule('/src/components/desktop-preparation-state.ts');
    const status = {
      ready: true,
      development: false,
      appSupportPath: '/app',
      services: [],
      payload: { installed: true, version: '2.0.3', channel: 'beta', commit: 'abc', builtAt: null },
    };

    assert.equal(shouldShowDesktopPreparation(status), false);
    for (const phase of ['download', 'verify', 'extract', 'swap', 'restart', 'health-check']) {
      assert.equal(shouldShowDesktopPreparation({ ...status, payloadProgress: { phase, message: phase } }), true);
    }
    assert.equal(shouldShowDesktopPreparation({ ...status, ready: false, payloadProgress: { phase: 'restart', message: 'Starting Sentinel…' } }), true);
    assert.equal(shouldShowDesktopPreparation({ ...status, payloadProgress: { phase: 'done', message: 'Installed.' } }), false);
  } finally {
    await server.close();
  }
});
