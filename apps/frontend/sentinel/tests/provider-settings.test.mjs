import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('provider cards are equal, ordered, and expand with styled Ollama controls', {timeout: 60000}, async () => {
  const server = await createServer({root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [{name: 'settings-fixture', enforce: 'pre',
      resolveId(id, importer) { if (id === './env' && importer?.endsWith('/lib/api.ts')) return '\0settings-env'; if (id === '\0settings-fixture') return id; },
      load(id) {
        if (id === '\0settings-env') return 'export const API_BASE_URL = "/api/v1";';
        if (id === '\0settings-fixture') return `import React from 'react'; import {createRoot} from 'react-dom/client';
          import {MemoryRouter} from 'react-router-dom'; import {WorkspaceProvider} from '/src/lib/workspace-context.tsx';
          import {SettingsPage} from '/src/pages/SettingsPage.tsx'; import '/src/index.css';
          createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,
            React.createElement(WorkspaceProvider,{instanceName:'test'},React.createElement(SettingsPage))));`;
      },
      configureServer(s) { s.middlewares.use(async (req, res, next) => {
        if (req.url !== '/instances/test/settings-preview') return next();
        res.setHeader('Content-Type', 'text/html');
        res.end(await s.transformIndexHtml(req.url, '<html class="dark"><body><div id="root" style="height:100vh"></div><script type="module" src="/@id/__x00__settings-fixture"></script></body></html>'));
      }); },
    }, react()], server: {host:'127.0.0.1', port:0}, logLevel:'error'});
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {})});
    const page = await browser.newPage({viewport:{width:1400,height:1000}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const mutations = [];
    let models = [{name:'qwen3:4b',size:2600000000}, {name:'unused:4b',size:2200000000}];
    let pull = {phase:'idle',model:'',detail:'',completed:0,total:0};
    let finishDownload = false;
    let speechInstalled = false;
    const voiceMutations = [];
    const chatRequests = [];
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      const body = method === 'GET' ? null : route.request().postDataJSON();
      if (path.includes('/sessions')) chatRequests.push(path);
      assert.ok(!path.includes('/ollama/local'));
      const status = {configured:true, auth_method:'oauth',auth_source:'cli',masked_key:'CLI · Auto-sync'};
      let json = {items:[]};
      if (path.endsWith('/voice/status')) json = {ready:speechInstalled,model:'qwen3:4b',provider:'ollama',provider_configured:true,issues:[],runtime:{installed:speechInstalled,installing:false,running:false,phase:'ready',error:'',path:'/data/voice'}};
      else if (path.endsWith('/models') && method === 'GET') json = {models:['fast','normal','hard'].map(tier => ({tier,label:{fast:'Fast',normal:'Normal',hard:'Deep Think'}[tier],description:'',primary_provider_id:'anthropic',primary_model_id:`${tier}-model`,provider_options:[
        {provider_id:'anthropic',model:`${tier}-model`,reasoning_levels:tier === 'normal' ? ['medium'] : ['low','medium','high'],reasoning_effort:'low',supports_fast_mode:tier === 'fast'},
        {provider_id:'ollama',model:'qwen3:4b',reasoning_levels:[],supports_fast_mode:false},
      ]}))};
      else if (path.endsWith('/voice/runtime/install')) {speechInstalled = true; voiceMutations.push(method); json = {installed:true};}
      else if (path.endsWith('/voice/runtime') && method === 'DELETE') {speechInstalled = false; voiceMutations.push(method); json = {installed:false};}
      else if (path.endsWith('/settings/api-keys/status')) json = {primary_provider:'openai',providers:{anthropic:status,openai:status,gemini:status}};
      else if (path.endsWith('/settings/ollama')) {
        json = {base_url:'http://127.0.0.1:11434',model:'qwen3:4b',configured:true,has_api_key:false};
        if (method === 'POST') mutations.push({method,path,body});
      }
      else if (path.endsWith('/settings/ollama/pull/status')) {
        if (finishDownload && pull.phase === 'running') {
          pull = {...pull,phase:'succeeded',detail:'Download complete',completed:pull.total};
          models.push({name:pull.model,size:1000000000});
        }
        json = pull;
      }
      else if (path.endsWith('/settings/ollama/pull')) {
        mutations.push({method,path,body});
        pull = {phase:'running',model:body.model,detail:'Downloading',completed:500000000,total:1000000000}; json = pull;
      }
      else if (path.endsWith('/settings/ollama/discover')) {
        if (body.base_url === 'https://offline.example') return route.fulfill({status:502,json:{detail:'Cannot reach this server. Check the address and that Ollama is running.'}});
        json = {models};
      }
      else if (path.endsWith('/settings/ollama/models')) {
        mutations.push({method,path,body}); models = models.filter(item => item.name !== body.model);
        json = {success:true,cleared_selection:body.model === 'qwen3:4b' && body.base_url === 'http://127.0.0.1:11434'};
      }
      await route.fulfill({json});
    });
    await page.goto(server.resolvedUrls.local[0] + 'instances/test/settings-preview');
    const cards = page.locator('.settings-provider');
    const assertInset = async (card, dividers) => {
      const bounds = await card.boundingBox();
      assert.ok(bounds);
      for (const divider of await dividers.all()) {
        const rect = await divider.boundingBox();
        assert.ok(rect && rect.x >= bounds.x + 15 && rect.x + rect.width <= bounds.x + bounds.width - 15,
          `Divider must stay inside card padding: ${JSON.stringify({bounds,rect})}`);
      }
    };
    await cards.nth(3).waitFor();
    assert.deepEqual(await cards.locator('.settings-provider-heading > span:first-of-type').allTextContents(), ['Anthropic','OpenAI','Ollama','Google Gemini']);
    assert.equal(await cards.locator('.settings-provider-heading .status-chip').count(), 0);
    assert.equal(await cards.locator('.settings-provider-badges').count(), 4);
    for (let index = 0; index < 4; index++) {
      const heading = await cards.nth(index).locator('.settings-provider-heading').boundingBox();
      const badges = await cards.nth(index).locator('.settings-provider-badges').boundingBox();
      assert.ok(badges.y >= heading.y + heading.height);
    }
    const heights = await cards.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
    assert.ok(heights.every(height => Math.abs(height - heights[0]) < 1), JSON.stringify(heights));
    const ollama = page.getByRole('region', {name:'Ollama provider'});
    assert.equal(await ollama.getByLabel('Server URL').count(), 0);
    await ollama.getByRole('button', {name:'Update',exact:true}).click();
    await ollama.getByLabel('Server URL').waitFor();
    await assertInset(ollama, ollama.locator('.settings-provider-editor'));
    await cards.nth(0).getByRole('button',{name:'Update',exact:true}).click();
    await assertInset(cards.nth(0), cards.nth(0).locator('.settings-provider-editor'));
    await cards.nth(0).getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(await ollama.getByRole('button', {name:'Close',exact:true}).getAttribute('aria-expanded'), 'true');
    assert.ok((await ollama.boundingBox()).height > heights[0] + 200);
    assert.ok(Math.abs((await cards.nth(0).boundingBox()).height - heights[0]) < 1);
    await ollama.getByRole('radio', {name:'qwen3:4b'}).waitFor();
    for (const name of ['Refresh models', 'Save provider', 'Add model']) {
      const button = ollama.getByRole('button', {name, exact:true});
      assert.ok(await button.evaluate(node => parseFloat(getComputedStyle(node).borderRadius) >= 10), name);
    }
    // Removal is explicit, and cancelling never mutates the server.
    await ollama.getByRole('button',{name:'Remove unused:4b',exact:true}).click();
    const confirm = ollama.getByRole('alertdialog');
    assert.match(await confirm.textContent(), /http:\/\/127.0.0.1:11434/);
    await confirm.getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(mutations.length, 0);
    await ollama.getByRole('button',{name:'Remove unused:4b',exact:true}).click();
    await confirm.getByRole('button',{name:'Remove model',exact:true}).click();
    await ollama.getByRole('radio',{name:'unused:4b'}).waitFor({state:'detached'});
    assert.equal(mutations[0].method, 'DELETE');
    assert.deepEqual(mutations[0].body, {base_url:'http://127.0.0.1:11434',model:'unused:4b'});

    // Removing the saved model clears its provider selection, not server credentials.
    await ollama.getByRole('button',{name:'Remove qwen3:4b',exact:true}).click();
    await confirm.getByRole('button',{name:'Remove model',exact:true}).click();
    await ollama.getByText('Not configured',{exact:true}).waitFor();
    assert.equal(await ollama.getByRole('button',{name:'Save provider',exact:true}).isDisabled(), true);

    // Remote endpoints use the exact same manager and retain downloads across closing.
    await ollama.getByLabel('Server URL').fill('https://remote.example/ollama');
    await ollama.getByRole('button',{name:'Connect to server'}).click();
    await ollama.getByRole('button',{name:'Add model'}).click();
    await ollama.getByLabel('Model name',{exact:true}).fill('new:4b');
    await ollama.getByRole('button',{name:'Download',exact:true}).click();
    await ollama.getByRole('progressbar').waitFor();
    assert.equal(await ollama.getByRole('progressbar').getAttribute('aria-valuenow'), '50');
    assert.deepEqual(mutations.at(-1).body, {base_url:'https://remote.example/ollama',model:'new:4b'});
    await ollama.getByRole('button', {name:'Close',exact:true}).click();
    assert.ok(Math.abs((await ollama.boundingBox()).height - heights[0]) < 1);
    await ollama.getByRole('button', {name:'Configure',exact:true}).click();
    await ollama.getByRole('progressbar').waitFor();
    finishDownload = true;
    await ollama.getByRole('radio',{name:'new:4b'}).waitFor();
    await ollama.getByRole('radio',{name:'new:4b'}).check();
    await page.screenshot({path:'/tmp/sentinel-ollama-model-manager.png',fullPage:true});
    await ollama.getByRole('button',{name:'Save provider',exact:true}).click();
    await ollama.getByRole('button',{name:'Update',exact:true}).waitFor();
    assert.deepEqual(mutations.at(-1).body, {base_url:'https://remote.example/ollama',model:'new:4b'});
    await ollama.getByRole('button',{name:'Update',exact:true}).click();
    await ollama.getByRole('radio',{name:'new:4b'}).waitFor();
    await page.setViewportSize({width:390,height:900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await ollama.getByLabel('Server URL').fill('https://offline.example');
    await ollama.getByRole('button',{name:'Connect to server'}).click();
    await ollama.getByRole('alert').filter({hasText:'Cannot reach this server'}).waitFor();
    await page.getByRole('navigation',{name:'Settings sections'}).getByRole('button',{name:'Voice',exact:true}).click();
    const voice = page.getByRole('region',{name:'Voice settings'});
    await voice.getByRole('button',{name:'Install local speech engine'}).click();
    await voice.getByText('Installed',{exact:true}).waitFor();
    // The only dropdown in Voice settings is the input device picker.
    assert.equal(await voice.getByRole('combobox').count(), 1);
    await voice.getByRole('combobox', {name:'Microphone'}).waitFor();
    const modelToggle = voice.getByRole('button',{name:'Model & reasoning',exact:true});
    assert.match(await modelToggle.textContent(), /fast-model.*Fast/);
    await modelToggle.click();
    const modelOptions = voice.getByRole('region',{name:'Voice model options'});
    assert.equal(await modelOptions.getByRole('button',{name:'Use default provider',exact:true}).getAttribute('aria-pressed'), 'true');
    await modelOptions.getByRole('button',{name:'Ollama',exact:true}).click();
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.voice-preferences')).state.providers.test), 'ollama');
    assert.equal(await modelOptions.getByRole('slider').count(), 0);
    assert.equal(await modelOptions.getByRole('switch').count(), 0);
    await modelOptions.getByRole('button',{name:'Claude',exact:true}).click();
    await modelOptions.getByRole('button',{name:'High',exact:true}).click();
    await modelOptions.getByRole('switch',{name:'Fast mode'}).click();
    let selection = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.voice-preferences')).state.selections.test);
    assert.deepEqual(selection, {tier:'fast',provider_id:'anthropic',reasoning_level:'high',fast_mode:true});
    await modelOptions.getByRole('button',{name:/^Deep Think/}).click();
    assert.equal(await modelOptions.getByRole('switch').count(), 0);
    selection = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.voice-preferences')).state.selections.test);
    assert.deepEqual(selection, {tier:'hard',provider_id:'anthropic',reasoning_level:'high',fast_mode:false});
    await modelOptions.getByRole('button',{name:/^Normal/}).click();
    selection = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.voice-preferences')).state.selections.test);
    assert.equal(selection.reasoning_level, undefined, 'Unsupported reasoning is cleared on tier change');
    await modelOptions.getByRole('button',{name:'Use default provider',exact:true}).click();
    selection = await page.evaluate(() => JSON.parse(localStorage.getItem('sentinel.voice-preferences')).state.selections.test);
    assert.deepEqual(selection, {tier:'normal',fast_mode:false});
    await modelOptions.getByRole('button',{name:/^Fast fast-model/}).click();
    assert.equal(await modelOptions.getByRole('button',{name:/^Fast fast-model/}).getAttribute('aria-pressed'), 'true');
    assert.equal(await modelOptions.getByRole('button',{name:/^Normal/}).getAttribute('aria-pressed'), 'false');
    assert.equal(await modelOptions.getByRole('button',{name:/^Deep Think/}).getAttribute('aria-pressed'), 'false');
    assert.match(await modelToggle.textContent(), /fast-model.*Fast/);
    assert.deepEqual(chatRequests, []);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.setViewportSize({width:1400,height:1000});
    // Settings panel plus the activity log; the panel itself stays compact.
    assert.equal(await voice.locator('.app-appearance-panel').count(), 2);
    assert.equal(await voice.locator('.voice-trace-log').count(), 1);
    assert.deepEqual(await voice.locator('.appearance-setting-row label').allTextContents(), ['Microphone', 'Speech speed', 'Sound cues']);
    assert.equal(await voice.getByText('Background listening',{exact:true}).count(), 0);
    assert.equal(await voice.getByText('Privacy',{exact:true}).count(), 0);
    assert.equal(await voice.locator('footer').textContent(), 'Voice stays connected when the panel closes; use Disconnect to stop listening. Audio stays local; transcripts and recent chat activity go to your selected provider.');
    await page.mouse.move(0, 0);
    await page.screenshot({path:'/tmp/sentinel-voice-settings-themed.png',fullPage:true,animations:'disabled'});
    await voice.getByRole('button',{name:'Remove local voice data…',exact:true}).click();
    assert.deepEqual(voiceMutations, ['POST']);
    await voice.getByRole('alertdialog').getByRole('button',{name:'Cancel',exact:true}).click();
    assert.deepEqual(voiceMutations, ['POST']);
    await voice.getByRole('button',{name:'Remove local voice data…',exact:true}).click();
    await voice.getByRole('alertdialog').getByRole('button',{name:'Remove local voice data',exact:true}).click();
    await voice.getByRole('button',{name:'Install local speech engine'}).waitFor();
    assert.deepEqual(voiceMutations, ['POST','DELETE']);
    for (const width of [1400,390]) {
      await page.setViewportSize({width,height:1000});
      await assertInset(voice.locator('.app-appearance-panel').first(), voice.locator('.app-appearance-panel-heading,.appearance-setting-row,.voice-model-setting'));
    }
    await page.getByRole('navigation',{name:'Settings sections'}).getByRole('button',{name:'Appearance',exact:true}).click();
    const appearance = page.getByRole('region',{name:'Appearance',exact:true});
    for (const width of [1400,390]) {
      await page.setViewportSize({width,height:1000});
      await assertInset(appearance.locator('.app-appearance-panel'), appearance.locator('.app-appearance-panel-heading,.appearance-quick-styles,.appearance-setting-row'));
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    await page.setViewportSize({width:1400,height:1000});
    await page.screenshot({path:'/tmp/sentinel-settings-inset-dividers.png',fullPage:true});
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
