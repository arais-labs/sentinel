import assert from 'node:assert/strict';
import test from 'node:test';
import {deflateSync, inflateSync, constants} from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from 'playwright';

test('real noVNC clipboard: both directions, Unicode, shortcuts, off, focus and permission errors', { timeout: 30000 }, async () => {
  const server = await createServer({
    root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{ name: 'vnc-pacing-fixture', configureServer(s) {
      s.middlewares.use((req, res, next) => {
        if (req.url !== '/pacing-test') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(`<input id="other"><div id="normal"></div><script type="module">
          import RFB from '/node_modules/@novnc/novnc/core/rfb.js';
          import {shareVncClipboard} from '/src/lib/vnc-clipboard.ts';
          window.clients={};window.ready=0;
          for(const name of ['normal']) {
            const ws=new WebSocket('ws://vnc-fixture/'+name);
            // Playwright's routed WebSocket has a different prototype chain.
            // Expose the normal channel interface explicitly for noVNC.
            const channel={send:ws.send.bind(ws),close:ws.close.bind(ws)};
            for(const key of ['binaryType','onerror','onmessage','onopen','onclose','protocol','readyState'])
              Object.defineProperty(channel,key,{enumerable:true,get:()=>ws[key],set:value=>ws[key]=value});
            const r=new RFB(document.getElementById(name),channel);
            if(name==='normal')window.bridge=shareVncClipboard(r,document.getElementById(name),message=>window.clipboardErrors?.push(message));
            r.addEventListener('connect',()=>window.ready++);clients[name]=r;
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
      const peer = peers[new URL(route.url()).pathname.slice(1)] = { route, encodings: [], requests: 0, pointers: [], continuous: [], cuts: [], keys: [], extended: [] };
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
          else if (input[0] === 4) length = 8;
          else if (input[0] === 6) { if(input.length<8)return; length=8+Math.abs(input.readInt32BE(4)); }
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
          else if (data[0] === 4) peer.keys.push([data.readUInt32BE(4), data[1]]);
          else if (data[0] === 6) { if(data.readInt32BE(4)<0)peer.extended.push(data.subarray(8));else peer.cuts.push(data.subarray(8).toString('latin1')); }
          else if (data[0] === 5) peer.pointers.push([data.readUInt16BE(2), data.readUInt16BE(4), data[1]]);
        }
      });
      route.send(Buffer.from('RFB 003.008\n'));
    });
    await page.goto(`${server.resolvedUrls.local[0]}pacing-test`);
    await page.waitForFunction(() => window.ready === 1);
    const normal = peers.normal;
    await page.evaluate(() => {
      window.clipboardValue = 'local text'; window.clipboardWrites = []; window.clipboardErrors = [];
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
        readText: async () => window.clipboardValue,
        writeText: async text => { clipboardWrites.push(text); clipboardValue = text; },
      }});
      clients.normal.focus(); bridge.setEnabled(true);
    });
    await page.waitForTimeout(50);
    assert.deepEqual(normal.cuts, ['local text']);
    const serverText = text => {
      const bytes=Buffer.from(text,'latin1'), packet=Buffer.alloc(8+bytes.length);
      packet[0]=3;packet.writeInt32BE(bytes.length,4);bytes.copy(packet,8);normal.route.send(packet);
    };
    serverText('remote text');
    await page.waitForFunction(() => clipboardWrites.length === 1);
    assert.equal(await page.evaluate(() => clipboardValue), 'remote text');
    await page.evaluate(() => clipboardValue = 'paste me');
    await page.keyboard.press('Control+Shift+V');
    await page.waitForTimeout(50);
    assert.equal(normal.cuts.at(-1), 'paste me');
    assert.deepEqual(normal.keys.slice(-6), [[65507,1],[65505,1],[86,1],[86,0],[65505,0],[65507,0]], 'terminal paste preserves Shift and releases modifiers');
    normal.keys=[];
    await page.keyboard.press('Meta+c');
    await page.waitForTimeout(50);
    assert.ok(normal.keys.some(([key,down])=>key===99&&down===1), 'Mac copy reaches guest');
    assert.ok(normal.keys.some(([key,down])=>key===65507&&down===1), 'Mac copy uses Control');
    normal.keys=[];
    await page.evaluate(() => {
      const canvas=document.querySelector('#normal canvas');
      canvas.dispatchEvent(new ClipboardEvent('copy',{bubbles:true,cancelable:true}));
      canvas.dispatchEvent(new ClipboardEvent('cut',{bubbles:true,cancelable:true}));
      const data=new DataTransfer();data.setData('text/plain','menu paste');
      canvas.dispatchEvent(new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data}));
    });
    await page.waitForTimeout(50);
    assert.equal(normal.cuts.at(-1),'menu paste');
    assert.deepEqual(normal.keys.filter(([key,down])=>key<128&&down===1).map(([key])=>key),[99,120,118],'native menu clipboard events become guest shortcuts');
    const caps=Buffer.alloc(16);caps[0]=3;caps.writeInt32BE(-8,4);caps.writeUInt32BE(0x1f000001,8);
    normal.route.send(caps);
    await page.waitForFunction(() => clients.normal._clipboardServerCapabilitiesActions[0x10000000]);
    await page.evaluate(() => clipboardValue = 'héllo 世界 🌍\nsecond line');
    await page.keyboard.press('Meta+v');
    await page.waitForTimeout(50);
    const request=Buffer.alloc(12);request[0]=3;request.writeInt32BE(-4,4);request.writeUInt32BE(0x02000001,8);
    normal.route.send(request);
    await page.waitForTimeout(50);
    const provided=normal.extended.findLast(data=>data.readUInt32BE(0)===0x10000001);
    assert.ok(provided, 'deferred Unicode clipboard provided');
    const raw=inflateSync(provided.subarray(4),{finishFlush:constants.Z_SYNC_FLUSH});
    assert.equal(raw.subarray(4).toString('utf8'), 'héllo 世界 🌍\r\nsecond line\0');
    const remoteText=Buffer.from('guest 日本語 🌍\0'), rawRemote=Buffer.alloc(4+remoteText.length);
    rawRemote.writeUInt32BE(remoteText.length,0);remoteText.copy(rawRemote,4);
    const compressed=deflateSync(rawRemote), update=Buffer.alloc(12+compressed.length);
    update[0]=3;update.writeInt32BE(-(4+compressed.length),4);update.writeUInt32BE(0x10000001,8);compressed.copy(update,12);
    normal.route.send(update);
    await page.waitForFunction(() => clipboardValue === 'guest 日本語 🌍');
    await page.evaluate(() => { bridge.setEnabled(false);clipboardValue='do not share'; });
    const sent=normal.extended.length, written=await page.evaluate(()=>clipboardWrites.length);
    normal.route.send(request);serverText('disabled guest');
    await page.keyboard.press('Control+v');
    await page.waitForTimeout(50);
    assert.equal(normal.extended.length,sent,'off cancels deferred clipboard data');
    assert.equal(await page.evaluate(()=>clipboardWrites.length),written,'off blocks incoming clipboard');
    await page.evaluate(() => { bridge.setEnabled(true); document.getElementById('other').focus(); });
    serverText('background guest');
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(()=>clipboardWrites.length),written,'background desktop cannot overwrite system clipboard');
    await page.evaluate(() => {
      clients.normal.focus();
      navigator.clipboard.readText=()=>new Promise(resolve=>window.finishRead=resolve);
    });
    await page.keyboard.press('Control+v');
    await page.evaluate(() => { bridge.setEnabled(false);finishRead('late secret'); });
    await page.waitForTimeout(50);
    assert.equal(await page.evaluate(()=>clients.normal._clipboardText),null,'disabled pending read cannot restore cached text');
    await page.evaluate(() => {
      navigator.clipboard.readText=async()=>{throw new Error('permission denied');};
      bridge.setEnabled(true);
    });
    await page.waitForFunction(()=>clipboardErrors.some(Boolean));
    await page.evaluate(() => { bridge.dispose();for(const r of Object.values(clients))r.disconnect(); });
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
