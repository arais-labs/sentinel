// Run with Electron. Verify its native PDF viewer under Sentinel's real scheme.
import { app, BrowserWindow, protocol } from 'electron';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';

const profile = mkdtempSync(path.join(os.tmpdir(), 'sentinel-pdf-preview-'));
for (const name of ['appData', 'userData', 'sessionData']) app.setPath(name, profile);
app.on('window-all-closed', () => {});
protocol.registerSchemesAsPrivileged([{ scheme: 'sentinel', privileges: {
  standard: true, secure: true, supportFetchAPI: true, stream: true,
} }]);
const timeout = setTimeout(() => { console.error('PDF preview timed out'); app.exit(1); }, 25000);
async function run() {
  let exitCode = 0;
  let server;
  try {
    await app.whenReady();
    const source = new BrowserWindow({ show: false });
    await source.loadURL('data:text/html,<h1>Sentinel PDF preview</h1>');
    const pdf = await source.webContents.printToPDF({});
    const media = await source.webContents.executeJavaScript(`(async () => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32;
      const context = canvas.getContext('2d'); context.fillStyle = 'blue'; context.fillRect(0, 0, 32, 32);
      const png = await new Promise(resolve => canvas.toBlob(resolve));
      const stream = canvas.captureStream(10);
      const recorder = new MediaRecorder(stream, {mimeType: 'video/webm'});
      const chunks = []; recorder.ondataavailable = event => chunks.push(event.data);
      const recorded = new Promise(resolve => recorder.onstop = resolve);
      recorder.start();
      const timer = setInterval(() => { context.fillStyle = 'red'; context.fillRect(0, 0, 32, 32); }, 40);
      await new Promise(resolve => setTimeout(resolve, 500)); clearInterval(timer); recorder.stop(); await recorded;
      stream.getTracks().forEach(track => track.stop());
      const wav = new ArrayBuffer(1644), view = new DataView(wav);
      const text = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset+i, c.charCodeAt(0)));
      text(0, 'RIFF'); view.setUint32(4, 1636, true); text(8, 'WAVE'); text(12, 'fmt ');
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, 8000, true); view.setUint32(28, 16000, true); view.setUint16(32, 2, true);
      view.setUint16(34, 16, true); text(36, 'data'); view.setUint32(40, 1600, true);
      return {png: [...new Uint8Array(await png.arrayBuffer())], wav: [...new Uint8Array(wav)],
        webm: [...new Uint8Array(await new Blob(chunks).arrayBuffer())]};
    })()`);
    source.destroy();
    const download = Buffer.from(Array.from({ length: 1024 * 1024 }, (_, i) => i % 256));
    protocol.handle('sentinel', request => {
      const extension = new URL(request.url).pathname.split('.').at(-1);
      if (Object.hasOwn(media, extension)) {
        const bytes = Buffer.from(media[extension]);
        return new Response(bytes, {headers: {
          'content-type': {png: 'image/png', wav: 'audio/wav', webm: 'video/webm'}[extension],
          'content-length': String(bytes.length), 'x-content-type-options': 'nosniff',
          'content-security-policy': "frame-ancestors 'self' sentinel://app http://localhost:* http://127.0.0.1:*",
        }});
      }
      if (new URL(request.url).pathname === '/download.bin') {
        return new Response(download, { headers: {
          'content-type': 'application/octet-stream',
          'content-length': String(download.length),
          'content-disposition': "attachment; filename*=UTF-8''download.bin",
        } });
      }
      if (new URL(request.url).pathname === '/report.pdf') {
        console.log('PDF request', request.method, request.headers.get('range'), request.headers.get('origin'));
        return new Response(pdf, { headers: {
          'content-type': 'application/pdf', 'content-length': String(pdf.length), 'accept-ranges': 'bytes',
          'content-security-policy': "frame-ancestors 'self' sentinel://app http://localhost:* http://127.0.0.1:*",
          'x-content-type-options': 'nosniff',
          'content-disposition': "inline; filename*=UTF-8''report.pdf",
        } });
      }
      return new Response('<!doctype html><iframe title="PDF" src="sentinel://app/report.pdf"></iframe>', { headers: { 'content-type': 'text/html' } });
    });
    const window = new BrowserWindow({ show: false, webPreferences: {
      nodeIntegration: false, contextIsolation: true, sandbox: false,
    } });
    server = http.createServer((_request, response) => {
      response.setHeader('content-type', 'text/html');
      response.end('<!doctype html><iframe title="PDF" src="sentinel://app/report.pdf"></iframe>');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    for (const origin of ['sentinel://app/', `http://127.0.0.1:${server.address().port}/`]) {
    await window.loadURL(origin);
    let rendered = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      rendered = window.webContents.mainFrame.framesInSubtree.some(frame => frame.url.startsWith('chrome-extension://') && frame.url.endsWith('/index.html'));
      if (rendered) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(rendered, 'PDF viewer must load within the app, without an external browser');
    assert.deepEqual(await window.webContents.executeJavaScript(`Promise.all([
      ['img', 'png', 'load'], ['audio', 'wav', 'loadeddata'], ['video', 'webm', 'loadeddata']
    ].map(([tag, extension, event]) => new Promise((resolve, reject) => {
      const element = document.createElement(tag);
      element.addEventListener(event, () => resolve(tag), {once: true});
      element.onerror = () => reject(new Error(tag + ' preview failed'));
      element.src = 'sentinel://app/media.' + extension;
      if (tag !== 'img') element.preload = 'auto';
      document.body.appendChild(element);
    })))`), ['img', 'audio', 'video']);
    }
    console.log('Native inline PDF viewer loaded under sentinel://app');
    const destination = path.join(profile, 'download.bin');
    const downloaded = new Promise((resolve, reject) => {
      window.webContents.session.once('will-download', (_event, item) => {
        item.setSavePath(destination);
        item.once('done', (_event, state) => state === 'completed' ? resolve() : reject(new Error(state)));
      });
    });
    await window.webContents.executeJavaScript(`
      const link = document.createElement('a');
      link.href = 'sentinel://app/download.bin';
      link.download = 'download.bin';
      document.body.appendChild(link); link.click(); link.remove();
    `);
    await downloaded;
    assert.deepEqual(readFileSync(destination), download, 'Native download preserves exact file bytes');
    console.log('Native streamed download preserves all bytes under sentinel://app');
  } catch (error) {
    console.error(error);
    exitCode = 1;
  } finally {
    clearTimeout(timeout);
    server?.close();
    for (const window of BrowserWindow.getAllWindows()) window.destroy();
    rmSync(profile, { recursive: true, force: true });
    app.exit(exitCode);
  }

}
void run();
