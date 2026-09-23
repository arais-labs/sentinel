import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from 'playwright';

test('desktop clipboard transfers only focused user gestures and cancels stale pastes', { timeout: 30000 }, async () => {
  const server = await createServer({root:fileURLToPath(new URL('..', import.meta.url)),configFile:false,
    server:{host:'127.0.0.1',port:0}, logLevel:'error', plugins:[{
      name:'desktop-clipboard-fixture', configureServer(server) {
        server.middlewares.use('/clipboard-test', (_request, response) => {
          response.setHeader('Content-Type','text/html; charset=utf-8');
          response.end(`<canvas id="desktop" tabindex="0"></canvas><input id="other"><script type="module">
            import { shareDesktopClipboard } from '/src/lib/desktop-clipboard.ts';
            const host = document.querySelector('#desktop');
            window.localText = 'Bonjour 世界 👋'; window.remoteText = 'Guest text';
            window.requests=[]; window.shortcuts=[]; window.errors=[];
            Object.defineProperty(navigator,'clipboard',{value:{
              async readText(){if(window.denied) throw Error('denied');return window.localText;},
              async writeText(text){if(window.denied) throw Error('denied');window.localText=text;}
            }});
            window.fetch=async(_url, options)=>{
              const request=JSON.parse(options.body); window.requests.push(request);
              if(window.hold) await new Promise(resolve=>window.complete=resolve);
              if(request.action==='write') window.remoteText=request.text;
              return new Response(JSON.stringify({text:window.remoteText}), {headers:{'Content-Type':'application/json'}});
            };
            window.sharing=shareDesktopClipboard(host,{release(){},shortcut(key,shift){window.shortcuts.push([key,shift]);}},
              '/instances/test/runtime/desktop/clipboard', error=>window.errors.push(error));
            window.sharing.setEnabled(true); host.focus();
          </script>`);
        });
      },
    }]});
  let browser;
  try {
    await server.listen();
    browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHANNEL?{channel:process.env.PLAYWRIGHT_CHANNEL}:{})});
    const page=await browser.newPage();
    await page.goto(server.resolvedUrls.local[0]+'clipboard-test');
    await page.waitForFunction(()=>window.sharing);
    await page.keyboard.press('Control+Shift+v');
    await page.waitForFunction(()=>window.shortcuts.length===1);
    assert.deepEqual(await page.evaluate(()=>window.shortcuts), [['v',true]]);
    assert.equal(await page.evaluate(()=>window.remoteText), 'Bonjour 世界 👋');
    await page.evaluate(()=>window.remoteText='Copied from guest 🐧');
    await page.keyboard.press('Control+c');
    await page.waitForFunction(()=>window.localText==='Copied from guest 🐧');
    assert.deepEqual(await page.evaluate(()=>window.shortcuts.at(-1)), ['c',false]);
    await page.evaluate(()=>{
      window.remoteText='Command key released';
      const host=document.querySelector('#desktop');
      host.dispatchEvent(new KeyboardEvent('keydown',{key:'c',code:'KeyC',metaKey:true,bubbles:true}));
      host.dispatchEvent(new KeyboardEvent('keyup',{key:'Meta',code:'MetaLeft',bubbles:true}));
    });
    await page.waitForFunction(()=>window.localText==='Command key released');
    const before=await page.evaluate(()=>window.requests.length);
    await page.evaluate(()=>window.sharing.setEnabled(false));
    await page.keyboard.press('Control+v');
    assert.equal(await page.evaluate(()=>window.requests.length), before);
    await page.evaluate(()=>window.sharing.setEnabled(true));
    await page.locator('#other').focus();
    await page.keyboard.press('Control+v');
    assert.equal(await page.evaluate(()=>window.requests.length), before);
    await page.locator('#desktop').focus();
    await page.evaluate(()=>window.denied=true);
    await page.keyboard.press('Control+v');
    await page.waitForFunction(()=>window.errors.at(-1)?.includes('permissions'));
    await page.evaluate(()=>{window.denied=false;window.hold=true;});
    await page.keyboard.press('Control+v');
    await page.waitForFunction(()=>window.complete);
    const shortcuts=await page.evaluate(()=>window.shortcuts.length);
    await page.evaluate(()=>{window.sharing.setEnabled(false);window.complete();});
    // Synchronize with the completed fetch rather than a time-based settle.
    await page.waitForFunction(()=>window.requests.length===4);
    assert.equal(await page.evaluate(()=>window.shortcuts.length), shortcuts);
    await page.evaluate(()=>window.sharing.dispose());
  } finally {await browser?.close();await server.close();}
});
