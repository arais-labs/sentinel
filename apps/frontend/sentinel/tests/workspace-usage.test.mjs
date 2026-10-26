import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const usage = {
  workspaces: [{ id: 'a', name: 'A', bytes: 300 }, { id: 'b', name: 'B', bytes: 0 }],
  other: 100,
  shared: { systems: 400, packages: 100, sources: 0, installs: 0, distributions: 100 },
  total: 1000,
  free: 2000,
  capacity: 4000,
};

test('disk usage sizes read like the rest of the app and the bar covers the disk', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    server: { middlewareMode: true },
    optimizeDeps: { noDiscovery: true, include: [] },
    logLevel: 'error',
  });
  try {
    const { formatBytes, usageSegments } = await server.ssrLoadModule('/src/components/environment/workspaceUsage.ts');
    assert.equal(formatBytes(512), '512 B');
    assert.equal(formatBytes(1536), '1.5 KB');
    assert.equal(formatBytes(7851704320), '7.3 GB');
    assert.equal(formatBytes(494384795648), '460 GB');

    // Workspaces (with other ones) as one part; empty items drop out; the rest of the disk follows.
    const segments = usageSegments(usage);
    assert.deepEqual(segments.map(({ kind, bytes }) => [kind, bytes]), [
      ['workspaces', 400], ['systems', 400], ['packages', 100], ['distributions', 100], ['elsewhere', 1000], ['free', 2000],
    ]);
    assert.equal(segments.reduce((sum, segment) => sum + segment.share, 0), 1);
    // Without the disk's size, the bar is Sentinel's own use.
    assert.deepEqual(usageSegments({ ...usage, free: null, capacity: null }).map(segment => segment.kind),
      ['workspaces', 'systems', 'packages', 'distributions']);
  } finally {
    await server.close();
  }
});
