import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('Files pane uploads, background progress, binary fallback layout and inline PDFs', {timeout:60000}, async()=>{
  const cacheDir=await mkdtemp(path.join(os.tmpdir(),'sentinel-upload-vite-'));
  const server=await createServer({cacheDir,root:fileURLToPath(new URL('..',import.meta.url)),configFile:false,
    plugins:[{name:'upload-fixture',enforce:'pre',
      resolveId(id,importer){if(id==='\0upload-fixture')return id;if(id==='./env'&&importer?.endsWith('/lib/api.ts'))return '\0upload-env';},
      load(id){
        if(id==='\0upload-env')return 'export const API_BASE_URL="/api/v1";';
        if(id==='\0upload-fixture')return `
          import React from 'react';import {createRoot} from 'react-dom/client';
          import {WorkspaceBrowser} from '/src/components/files/WorkspaceBrowser.tsx';
          import {droppedItems} from '/src/components/files/useFileUploads.ts';import '/src/index.css';
          const workspaces=[{id:'local',name:'Local',directory:'/project'},{id:'remote',name:'Remote',directory:'/project'}];
          window.droppedItems=droppedItems;
          function App(){const[id,setId]=React.useState('local');window.setWorkspace=setId;return React.createElement(WorkspaceBrowser,{key:id,instance:'test',workspace:workspaces.find(w=>w.id===id),workspaces,onWorkspace:setId,pinned:true,followingAttachment:false,attachmentLoading:false,onPin:()=>{}});}
          createRoot(document.getElementById('root')).render(React.createElement(App));`;
      },configureServer(s){s.middlewares.use(async(req,res,next)=>{if(req.url!=='/upload-test')return next();res.setHeader('Content-Type','text/html');res.end(await s.transformIndexHtml(req.url,'<html class="dark"><body><div id="root" style="height:650px"></div><script type="module" src="/@id/__x00__upload-fixture"></script></body></html>'));});},
    },react()],server:{host:'127.0.0.1',port:0},logLevel:'error'});
  let browser;
  try{
    await server.listen();browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
    const page=await browser.newPage({viewport:{width:1000,height:720}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
    const requests=[];let hold=false,fail=false,release,ack=2;
    await page.route('**/api/v1/**',async route=>{
      const r=route.request(),u=new URL(r.url());
      if(r.method()==='POST'){
        requests.push({path:u.pathname,params:Object.fromEntries(u.searchParams),body:r.postDataBuffer()});
        if(hold)await new Promise(resolve=>{release=resolve;});
        await route.fulfill(fail?{status:422,json:{detail:'No space left on device'}}:{json:{ok:true,name:'saved.bin'}}).catch(()=>{});
      }else if(u.pathname.includes('/uploads/'))await route.fulfill({json:{received:ack}});
      else if(u.pathname.endsWith('/content'))await route.fulfill({contentType:'application/pdf',body:r.method()==='HEAD'?undefined:Buffer.from('%PDF-1.7\n'+'%padding\n'.repeat(30000)+'%%EOF')});
      else if(u.pathname.endsWith('/file'))await route.fulfill({json:{binary:true,truncated:true,content:'',max_bytes:200000,media_type:u.searchParams.get('path')==='report.pdf'?'application/pdf':'application/octet-stream'}});
      else{const op=u.pathname.split('/').at(-1);await route.fulfill({json:op==='repositories'?{roots:[],errors:[]}:op==='context'?{repository:null,worktrees:[]}:{entries:[{name:'folder',path:'folder',kind:'directory'},{name:'report.pdf',path:'report.pdf',kind:'file'},{name:'archive.bin',path:'archive.bin',kind:'file'}],truncated:false}});}
    });
    await page.goto(server.resolvedUrls.local[0]+'upload-test');
    const folder=page.getByRole('treeitem',{name:'folder',exact:true});await folder.waitFor();
    async function drop(locator,name='file.bin'){
      const data=await page.evaluateHandle(name=>{const d=new DataTransfer();d.items.add(new File([new Uint8Array([0,1,255,42])],name));return d;},name);
      await locator.dispatchEvent('dragover',{dataTransfer:data});await page.locator('.project-upload-overlay').waitFor();
      await locator.dispatchEvent('drop',{dataTransfer:data});await data.dispose();
    }
    await drop(folder);await page.getByText('Uploaded saved.bin',{exact:true}).waitFor();
    assert.equal(requests[0].path,'/api/v1/instances/test/workspaces/local/browse/upload');assert.equal(requests[0].params.path,'folder');assert.deepEqual([...requests[0].body],[0,1,255,42]);
    hold=true;await folder.waitFor();await drop(folder,'ongoing.bin');
    await page.waitForFunction(()=>document.querySelector('progress')?.value===2);
    assert.equal(await page.getByRole('progressbar').getAttribute('max'),'4');
    if(process.env.SENTINEL_SCREENSHOT_DIR) {
      for(const theme of ['dark','light']) {
        await page.evaluate(theme=>document.documentElement.className=theme,theme);
        await page.screenshot({path:process.env.SENTINEL_SCREENSHOT_DIR+'/file-upload-progress-'+theme+'.png'});
      }
    }
    // A chat/workspace switch unmounts the pane, while its upload remains active.
    await page.evaluate(()=>window.setWorkspace('remote'));await folder.waitFor();
    assert.equal(await page.getByRole('progressbar').count(),0);
    await page.evaluate(()=>window.setWorkspace('local'));await page.getByRole('progressbar').waitFor();
    assert.equal(await page.getByRole('progressbar').getAttribute('value'),'2');
    release();hold=false;await page.getByText('Uploaded saved.bin',{exact:true}).waitFor();
    await page.evaluate(()=>window.setWorkspace('remote'));await folder.waitFor();
    await drop(page.locator('.project-preview'),'root.bin');await page.getByText('Uploaded saved.bin',{exact:true}).waitFor();
    assert.equal(requests[2].path,'/api/v1/instances/test/workspaces/remote/browse/upload');assert.equal(requests[2].params.path,'');
    fail=true;await page.locator('input[type=file]').setInputFiles({name:'chosen.txt',mimeType:'text/plain',buffer:Buffer.from('chosen')});
    await page.getByRole('alert').filter({hasText:'No space left'}).waitFor();
    fail=false;hold=true;await folder.waitFor();await drop(folder,'cancel.bin');await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.getByText('Upload cancelled. 0 items saved.',{exact:true}).waitFor();release?.();
    const collected=await page.evaluate(async()=>{
      const file={name:'child.txt',isFile:true,file:resolve=>resolve(new File(['hi'],'child.txt'))};let batch=0;
      const directory={name:'nested',isDirectory:true,createReader:()=>({readEntries:resolve=>resolve(batch++===0?[file]:batch===2?[{name:'empty',isDirectory:true,createReader:()=>({readEntries:r=>r([])})}]:[])})};
      return(await window.droppedItems({items:[{kind:'file',webkitGetAsEntry:()=>directory,getAsFile:()=>null}]})).map(i=>({name:i.name,size:i.file?.size??null}));
    });
    assert.deepEqual(collected,[{name:'nested',size:null},{name:'nested/child.txt',size:2},{name:'nested/empty',size:null}]);
    if(process.env.SENTINEL_SCREENSHOT_DIR)await page.screenshot({path:process.env.SENTINEL_SCREENSHOT_DIR+'/file-uploads.png'});
    await page.getByRole('treeitem',{name:'archive.bin',exact:true}).click();
    const fallback=page.locator('.project-file-state');
    await fallback.getByText('This binary file cannot be previewed as text.',{exact:true}).waitFor();
    assert.equal(await page.getByText(/Preview limited to 200 KB/).count(),0);
    const paragraph=await fallback.locator('p').boundingBox();
    const download=await fallback.getByRole('button',{name:'Download file',exact:true}).boundingBox();
    assert.ok(download.y-paragraph.y-paragraph.height < 20,'Fallback action stays next to its explanation');
    for(const theme of ['dark','light']) {
      await page.evaluate(theme=>document.documentElement.className=theme,theme);
      if(process.env.SENTINEL_SCREENSHOT_DIR)await page.screenshot({path:process.env.SENTINEL_SCREENSHOT_DIR+'/binary-preview-'+theme+'.png'});
    }
    await page.getByRole('treeitem',{name:'report.pdf',exact:true}).click();
    const pdf=page.getByTitle('PDF preview: report.pdf',{exact:true});await pdf.waitFor();
    const source=new URL(await pdf.getAttribute('src'),page.url());
    assert.equal(source.pathname,'/api/v1/instances/test/workspaces/remote/browse/content');
    assert.equal(source.searchParams.get('path'),'report.pdf');
    assert.equal(source.hash,'#view=FitH&navpanes=0');
    assert.equal(await page.getByText('This binary file cannot be previewed as text.').count(),0);
    assert.equal(await page.getByText(/Preview limited to 200 KB/).count(),0);
    assert.ok((await pdf.boundingBox()).height > 300);
    assert.deepEqual(errors,[]);
  }finally{await browser?.close();await server.close();await rm(cacheDir,{recursive:true,force:true});}
});
