import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const catalog = {
  os: 'darwin',
  workspaces_available: true,
  distributions: [
    { id: 'linux-a', name: 'Linux A', detail: null, features: ['shell', 'terminal', 'files'], installed: true, minimum: {} },
    { id: 'windows-b', name: 'Windows B', detail: null, features: [], installed: false, minimum: { memory_gib: 8, disk_gib: 64 } },
    { id: 'terminal-c', name: 'Terminal C', detail: null, features: ['shell', 'terminal'], installed: false },
  ],
  desktops: [
    { id: 'desk-a', name: 'Desk A', distributions: ['linux-a'] },
    { id: 'desk-b', name: 'Desk B', distributions: ['windows-b'] },
  ],
  tools: [
    { id: 'git', name: 'Git', detail: '', category: 'Essentials', distributions: ['linux-a', 'windows-b'] },
    { id: 'docker', name: 'Docker', detail: '', category: 'Containers', distributions: ['linux-a'] },
    { id: 'chromium', name: 'Chromium', detail: '', category: 'Browsers', distributions: ['linux-a'] },
    { id: 'firefox', name: 'Firefox', detail: '', category: 'Browsers', distributions: ['linux-a', 'windows-b'] },
  ],
  stacks: [
    { id: 'docker', name: 'Docker', description: '', tools: ['docker'] },
    { id: 'basic', name: 'Basic', description: '', tools: ['git'] },
  ],
};

test('workspace choices and selections follow the catalog availability of the distribution', async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)),
    configFile: false,
    server: { middlewareMode: true },
    optimizeDeps: { noDiscovery: true, include: [] },
    logLevel: 'error',
  });
  try {
    const { availableSelection, unsupportedViews, withMinimum, workspaceChoices } = await server.ssrLoadModule('/src/components/environment/workspaceCatalog.ts');
    const ids = items => items.map(item => item.id);

    const linux = workspaceChoices(catalog, 'linux-a');
    assert.deepEqual(ids(linux.desktops), ['desk-a']);
    assert.deepEqual(ids(linux.browsers), ['chromium', 'firefox']);
    assert.deepEqual(ids(linux.tools), ['git', 'docker']);
    assert.deepEqual(ids(linux.stacks), ['docker', 'basic']);

    const windows = workspaceChoices(catalog, 'windows-b');
    assert.deepEqual(ids(windows.desktops), ['desk-b']);
    assert.deepEqual(ids(windows.browsers), ['firefox']);
    assert.deepEqual(ids(windows.tools), ['git']);
    assert.deepEqual(ids(windows.stacks), ['basic']);

    const selection = { desktop: 'desk-a', browser: 'chromium', tools: ['git', 'docker'] };
    assert.deepEqual(availableSelection(catalog, 'linux-a', selection), selection);
    assert.deepEqual(availableSelection(catalog, 'windows-b', selection), { desktop: 'none', browser: 'chromium', tools: ['git'] });
    assert.deepEqual(availableSelection(catalog, 'windows-b', { desktop: 'desk-b', browser: 'firefox', tools: [] }),
      { desktop: 'desk-b', browser: 'firefox', tools: [] });

    assert.deepEqual(unsupportedViews(catalog, 'linux-a'), []);
    assert.deepEqual(unsupportedViews(catalog, 'windows-b'), ['terminal', 'files']);
    assert.deepEqual(unsupportedViews(catalog, 'terminal-c'), ['files']);
    assert.deepEqual(unsupportedViews(catalog, 'unknown'), []);

    const [linuxA, windowsB] = catalog.distributions;
    const small = { cpus: 2, memory_gib: 4, disk_gib: 32 };
    assert.deepEqual(withMinimum(small, windowsB), { cpus: 2, memory_gib: 8, disk_gib: 64 });
    assert.deepEqual(withMinimum({ cpus: 4, memory_gib: 16, disk_gib: 128 }, windowsB), { cpus: 4, memory_gib: 16, disk_gib: 128 });
    assert.deepEqual(withMinimum(small, linuxA), small);
    assert.deepEqual(withMinimum(small, undefined), small);
    assert.deepEqual(withMinimum(small, { minimum: { cpus: 4 } }), { cpus: 4, memory_gib: 4, disk_gib: 32 });
  } finally {
    await server.close();
  }
});
