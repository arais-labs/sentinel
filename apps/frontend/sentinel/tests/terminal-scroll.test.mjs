import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from 'playwright';

test('terminal keeps reading position and bottom pin while hidden output arrives', {timeout:60000}, async()=>{
 const server=await createServer({root:fileURLToPath(new URL('..',import.meta.url)),configFile:false,plugins:[{name:'terminal-scroll',configureServer(s){s.middlewares.use((req,res,next)=>{
 if(req.url!=='/scroll-test')return next();res.setHeader('Content-Type','text/html');res.end(`<div id="host" style="height:400px;width:800px;overflow:auto"></div><script type="module">
 import {TerminalRenderer,loadTerminalCore} from '/src/components/session/terminal-renderer.ts';
 const host=document.getElementById('host');window.term=new TerminalRenderer(host,await loadTerminalCore(),80,20,()=>{});await term.init();
 term.write(Array.from({length:150},(_,i)=>'Line '+i+'\\r\\n').join(''),()=>{});window.ready=true;
 </script>`);
 });}}],server:{host:'127.0.0.1',port:0},logLevel:'error'});
 let browser;
 try{
 await server.listen();browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(server.resolvedUrls.local[0]+'scroll-test');await page.waitForFunction(()=>window.ready);await page.waitForTimeout(200);
 await page.evaluate(()=>{host.scrollTop=200;});await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>host.scrollTop),200);
 await page.evaluate(()=>{term.setVisible(false);host.style.display='none';term.write('hidden output\r\n',()=>{});});await page.waitForTimeout(100);
 await page.evaluate(()=>{host.style.display='';term.setVisible(true);});await page.waitForTimeout(100);
 assert.equal(await page.evaluate(()=>host.scrollTop),200);
 await page.evaluate(()=>{host.scrollTop=host.scrollHeight;});await page.waitForTimeout(100);
 await page.evaluate(()=>{term.setVisible(false);host.style.display='none';term.write('more hidden output\r\n'.repeat(20),()=>{});});await page.waitForTimeout(100);
 await page.evaluate(()=>{host.style.display='';term.setVisible(true);});await page.waitForTimeout(100);
 assert.ok(await page.evaluate(()=>host.scrollHeight-host.scrollTop-host.clientHeight<5));assert.deepEqual(errors,[]);
 await page.evaluate(()=>term.dispose());
 }finally{await browser?.close();await server.close();}
});
