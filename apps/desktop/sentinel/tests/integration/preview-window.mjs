// Run after tsc --outDir .test-dist, using Electron.
import { app, BrowserWindow } from 'electron';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { WebSocketServer } from 'ws';
import { openPreviewWindow } from '../../.test-dist/main/app/previewWindow.js';

const profile = mkdtempSync(path.join(os.tmpdir(), 'sentinel-preview-test-'));
app.setPath('userData', profile);
app.on('window-all-closed', () => {});
app.on('web-contents-created', (_event, contents) => {
  contents.on('console-message', event => { if (event.level === 'error') console.error(event.message); });
  contents.on('did-fail-load', (_event, code, description) => console.log('load failed', code, description));
});
const timeout = setTimeout(() => { console.error('Preview test timed out'); app.exit(1); }, 15000);
let finish;
const completed = new Promise(resolve => { finish = resolve; });
const server = http.createServer(async (req, res) => {
  if (req.url === '/asset') { res.end('asset-ok'); return; }
  if (req.url === '/result') {
    let body = ''; for await (const chunk of req) body += chunk;
    res.end('ok'); finish(JSON.parse(body)); return;
  }
  assert.equal(req.url, '/');
  res.setHeader('Content-Type', 'text/html');
  res.end(`<title>Preview integration test</title><h1>Workspace preview</h1><script>
    (async () => {
      const asset = await fetch('/asset').then(r => r.text());
      const socket = new WebSocket('ws://' + location.host + '/socket');
      socket.onmessage = event => {
        fetch('/result', {method:'POST',body:JSON.stringify({asset,websocket:event.data,desktop:typeof window.sentinelDesktop,node:typeof require})});
        socket.close();
      };
    })();
  </script>`);
});
const sockets = new WebSocketServer({ server, path: '/socket' });
sockets.on('connection', socket => socket.send('websocket-ok'));
async function main() {
try {
  await app.whenReady(); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const url = `http://127.0.0.1:${server.address().port}/`;
  const link = '/api/v1/instances/demo/sessions/97394c11-0f16-465e-953f-8a1b6640d981/runtime/forwards/pf-bebf36e56cff/';
  await openPreviewWindow(link, {request: async request => {
    assert.equal(new URL(request.url).pathname, link.replace('/forwards/', '/forward-target/').replace(/\/$/, ''));
    return Response.json({url, label:'Preview test'});
  }});
  assert.deepEqual(await completed, {asset:'asset-ok',websocket:'websocket-ok',desktop:'undefined',node:'undefined'});
  console.log('PASS: dedicated Electron preview, root assets, WebSockets, no desktop bridge or Node access');
  clearTimeout(timeout);
  for (const window of BrowserWindow.getAllWindows()) window.destroy();
  for (const socket of sockets.clients) socket.terminate();
  sockets.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  app.on('quit', () => rmSync(profile, { recursive:true, force:true }));
  app.quit();
} catch (error) { console.error(error); app.exit(1); }

}
void main();
