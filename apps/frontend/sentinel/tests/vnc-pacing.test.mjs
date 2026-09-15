import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from 'playwright';

test('real noVNC preserves codecs, decodes frames and bounds requests during a stall', { timeout: 30000 }, async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{ name: 'vnc-pacing-fixture', configureServer(s) {
      s.middlewares.use((req, res, next) => {
        if (req.url !== '/pacing-test') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(`<div id="paced"></div><div id="normal"></div><script type="module">
          import RFB from '/node_modules/@novnc/novnc/core/rfb.js';
          import {controlVncVisibility} from '/src/lib/vnc-visibility.ts';
          import {paceVncUpdates} from '/src/lib/vnc-pacing.ts';
          import {forwardVncPointer} from '/src/lib/vnc-input.ts';
          window.clients={};window.ready=0;window.visibility={};window.visible=true;
          for(const name of ['paced','normal']) {
            const ws=new WebSocket('ws://vnc-fixture/'+name);
            // Playwright's routed WebSocket has a different prototype chain.
            // Expose the normal channel interface explicitly for noVNC.
            const channel={send:ws.send.bind(ws),close:ws.close.bind(ws)};
            for(const key of ['binaryType','onerror','onmessage','onopen','onclose','protocol','readyState'])
              Object.defineProperty(channel,key,{enumerable:true,get:()=>ws[key],set:value=>ws[key]=value});
            const r=new RFB(document.getElementById(name),channel);
            if(name==='paced'){window.disposePacing=paceVncUpdates(r,()=>window.visible);window.disposePointer=forwardVncPointer(r);}
            window.visibility[name]=controlVncVisibility(r);r.addEventListener('connect',()=>window.ready++);clients[name]=r;
          }
        </script>`);
      });
    }}], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error',
  });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const peers = {};
    await page.routeWebSocket('ws://vnc-fixture/**', route => {
      const peer = peers[new URL(route.url()).pathname.slice(1)] = { route, encodings: [], requests: 0, pointers: [], continuous: [] };
      let stage = 0, input = Buffer.alloc(0);
      route.onMessage(message => {
        input = Buffer.concat([input, Buffer.from(message)]);
        while (input.length) {
          let length;
          if (stage === 0) length = 12;
          else if (stage < 3) length = 1;
          else if (input[0] === 0) length = 20;
          else if (input[0] === 2) { if (input.length < 4) return; length = 4 + input.readUInt16BE(2) * 4; }
          else if (input[0] === 3) length = 10;
          else if (input[0] === 5) length = 6;
          else if (input[0] === 150) length = 10;
          else throw new Error(`Unexpected client message ${input[0]}`);
          if (input.length < length) return;
          const data = input.subarray(0, length); input = input.subarray(length);
          if (stage === 0) { stage++; route.send(Buffer.from([1, 1])); }
          else if (stage === 1) { stage++; route.send(Buffer.alloc(4)); }
          else if (stage === 2) {
            stage++;
            const init = Buffer.alloc(24); init.writeUInt16BE(64, 0); init.writeUInt16BE(64, 2);
            init.set([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 0, 8, 16], 4);
            route.send(init);
          } else if (data[0] === 2) {
            peer.encodings = Array.from({ length: data.readUInt16BE(2) }, (_, i) => data.readInt32BE(4 + i * 4));
          } else if (data[0] === 3) peer.requests++;
          else if (data[0] === 150) peer.continuous.push([...data]);
          else if (data[0] === 5) peer.pointers.push([data.readUInt16BE(2), data.readUInt16BE(4), data[1]]);
        }
      });
      route.send(Buffer.from('RFB 003.008\n'));
    });
    await page.goto(`${server.resolvedUrls.local[0]}pacing-test`);
    await page.waitForFunction(() => window.ready === 2);
    await page.waitForTimeout(250);
    assert.deepEqual(peers.paced.encodings, peers.normal.encodings.filter(e => e !== -312 && e !== -313));
    assert.ok(peers.normal.encodings.includes(-312));
    assert.ok(peers.paced.encodings.includes(7));
    assert.equal(peers.paced.requests, 2, 'initial request plus one lookahead during a 250 ms stall');
    assert.equal(peers.normal.requests, 1, 'other clients retain upstream behavior');
    peers.normal.route.send(Buffer.from([150]));
    await page.waitForFunction(() => clients.normal._enabledContinuousUpdates);
    await page.waitForTimeout(50);
    assert.deepEqual(peers.normal.continuous, [[150,1,0,0,0,0,0,64,0,64]], 'native remote mode enables server-driven updates');
    await page.evaluate(() => {
      const r=clients.paced;
      r._handleMouseButton(10,10,1);
      r._handleMouseMove(12,12);
      r._handleMouseMove(20,20);
      r._handleMouseButton(20,20,0);
      r.viewOnly=true;
      r._handleMouseMove(30,30);
      r.viewOnly=false;
    });
    await page.waitForTimeout(50);
    assert.deepEqual(peers.paced.pointers, [[10,10,1],[12,12,1],[20,20,1],[20,20,0]], 'moves bypass the timer, preserve button ordering and respect view-only mode');
    const frame = Buffer.alloc(20); frame.writeUInt16BE(1, 2);
    frame.writeUInt16BE(64, 8); frame.writeUInt16BE(64, 10); frame.writeInt32BE(7, 12);
    frame.set([0x80, 12, 34, 56], 16);
    peers.normal.route.send(frame);
    await page.waitForFunction(() => document.querySelector('#normal canvas')?.getContext('2d').getImageData(1,1,1,1).data[1] === 34);
    assert.equal(peers.normal.requests, 1, 'streaming frames need no additional pull request');
    await page.evaluate(() => { window.visible=false; visibility.normal.setVisible(false); visibility.paced.setVisible(false); });
    await page.waitForTimeout(50);
    assert.equal(peers.normal.continuous.at(-1)[1],0);
    const pausedCount=peers.paced.requests;
    peers.paced.route.send(frame);
    await page.waitForTimeout(100);
    assert.equal(peers.paced.requests,pausedCount,'hidden pull client stops requesting frames');
    await page.evaluate(() => { visibility.normal.setVisible(true); visibility.paced.setVisible(true); window.visible=true; });
    await page.waitForTimeout(50);
    assert.equal(peers.normal.continuous.at(-1)[1],1);
    peers.paced.requests=2;
    // A following incomplete raw rectangle holds more than the receive budget.
    // Pacing must stop even though the previous update completed successfully.
    const rawHeader = Buffer.alloc(16); rawHeader.writeUInt16BE(1, 2);
    rawHeader.writeUInt16BE(640, 8); rawHeader.writeUInt16BE(480, 10);
    peers.paced.route.send(Buffer.concat([frame, rawHeader, Buffer.alloc(600 * 1024)]));
    await page.waitForFunction(() => document.querySelector('#paced canvas')?.getContext('2d').getImageData(1, 1, 1, 1).data[1] === 34);
    await page.waitForTimeout(100);
    assert.equal(peers.paced.requests, 3, 'received backlog suspends lookahead while ordinary pull continues');
    peers.paced.route.send(Buffer.alloc(640 * 480 * 4 - 600 * 1024));
    await page.waitForFunction(() => document.querySelector('#paced canvas').getContext('2d').getImageData(1, 1, 1, 1).data[1] === 0);
    await page.waitForTimeout(100);
    assert.equal(peers.paced.requests, 5, 'draining the backlog restores ordinary pull plus one lookahead');
    await page.evaluate(() => { disposePointer(); Object.values(visibility).forEach(v=>v.dispose()); disposePacing(); for (const r of Object.values(clients)) r.disconnect(); });
    await page.waitForTimeout(100);
    assert.equal(peers.paced.requests, 5, 'disconnect cancels pacing');
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
