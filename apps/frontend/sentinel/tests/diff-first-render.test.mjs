import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { DEFAULT_THEMES, DiffHunksRenderer, getSingularPatch, preloadHighlighter } from '@pierre/diffs';

test('cold diff is readable immediately and preloaded highlighting renders synchronously', async () => {
  const patch = 'diff --git a/index.html b/index.html\n--- a/index.html\n+++ b/index.html\n@@ -1 +1 @@\n-<title>Before</title>\n+<title>After</title>\n';
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    server: { middlewareMode: true }, logLevel: 'error', optimizeDeps: { noDiscovery: true, include: [] } });
  let renderer;
  try {
    const { DiffViewer } = await server.ssrLoadModule('/src/components/workbench/DiffViewer.tsx');
    const html = renderToStaticMarkup(createElement(DiffViewer, { diff: patch }));
    assert.match(html, /diff-readable-preview/);
    assert.match(html, /data-change="removed"/);
    assert.match(html, /data-change="added"/);
    assert.match(html, /&lt;title&gt;Before&lt;\/title&gt;/);
    assert.match(html, /&lt;title&gt;After&lt;\/title&gt;/);
    await preloadHighlighter({ themes: Object.values(DEFAULT_THEMES), langs: ['html'] });
    renderer = new DiffHunksRenderer({ disableFileHeader: true, diffStyle: 'unified' });
    const rendered = renderer.renderDiff(getSingularPatch(patch));
    assert.ok(rendered?.unifiedContentAST, 'the first highlighted render must already contain code');
  } finally {
    renderer?.cleanUp();
    await server.close();
  }
});
