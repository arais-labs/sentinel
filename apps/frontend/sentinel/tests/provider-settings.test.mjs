import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('providers persist routing and model settings and preserve Ollama controls', {timeout: 60000}, async () => {
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
    const usageRequests = [];
    const modelSettingsRequests = [];
    const modelOverrides = {};
    let modelLoadGate = null;
    let modelSaveFailure = false;
    let oauth = true;
    let usageFailure = false;
    let statusFailure = false;
    let routingFailure = false;
    let routingGate = null;
    let routing = {order:['openai','anthropic','gemini','ollama'],automatic:['openai','anthropic','gemini','ollama']};
    const routingRequests = [];
    let ollamaConfig = {base_url:'http://127.0.0.1:11434',model:'qwen3:4b',configured:true,has_api_key:false};
    await page.route('**/api/v1/**', async route => {
      const path = new URL(route.request().url()).pathname;
      const method = route.request().method();
      const body = method === 'GET' ? null : route.request().postDataJSON();
      if (path.includes('/sessions')) chatRequests.push(path);
      if (path.endsWith('/usage')) usageRequests.push(path);
      assert.ok(!path.includes('/ollama/local'));
      const status = {configured:true, auth_method:oauth ? 'oauth' : 'api_key',auth_source:oauth ? 'cli' : 'manual',masked_key:oauth ? 'CLI · Auto-sync' : 'sk-…test'};
      if (path.endsWith('/usage') && usageFailure) return route.fulfill({status:503,json:{detail:'Unavailable'}});
      if (path.endsWith('/settings/api-keys/status') && statusFailure) return route.fulfill({status:503,json:{detail:'Status refresh failed.'}});
      let json = {items:[]};
      if (/\/settings\/providers\/[^/]+\/models$/.test(path)) {
        const provider = path.split('/').at(-2);
        modelSettingsRequests.push({provider, method, body});
        if (method === 'GET' && modelLoadGate) await modelLoadGate;
        if (method === 'PUT' && modelSaveFailure) return route.fulfill({status:503,json:{detail:'Model save failed.'}});
        const defaults = provider === 'ollama' ? {fast:'qwen3:4b',normal:'qwen3:4b',hard:'qwen3:4b'} : {fast:`${provider}-fast`,normal:`${provider}-normal`,hard:`${provider}-hard`};
        const namespace = provider === 'openai' && oauth ? 'codex' : provider;
        if (method === 'PUT') modelOverrides[provider] = {fast:body.fast,normal:body.normal,hard:body.hard};
        const overrides = modelOverrides[provider] ?? {fast:null,normal:null,hard:null};
        json = {namespace,defaults,overrides,effective:Object.fromEntries(Object.entries(defaults).map(([tier,model]) => [tier,overrides[tier] ?? model]))};
      }
      else if (path.endsWith('/model-options')) {
        const provider = path.split('/').at(-2);
        json = {namespace:provider === 'openai' && oauth ? 'codex' : provider,models:provider === 'openai' ? ['gpt-6-luna','gpt-6.1-sol','gpt-6-astra'] : [],message:null};
      }
      else if (path.endsWith('/settings/desktop-codex-oauth/status')) json = {enabled:true,auth_file_found:true};
      else if (path.endsWith('/voice/status')) json = {ready:speechInstalled,model:'qwen3:4b',provider:'ollama',provider_configured:true,issues:[],runtime:{installed:speechInstalled,installing:false,running:false,phase:'ready',error:'',path:'/data/voice'}};
      else if (path.endsWith('/models') && method === 'GET') json = {models:['fast','normal','hard'].map(tier => ({tier,label:{fast:'Fast',normal:'Normal',hard:'Deep Think'}[tier],description:'',primary_provider_id:'anthropic',primary_model_id:`${tier}-model`,provider_options:[
        {provider_id:'anthropic',model:`${tier}-model`,reasoning_levels:tier === 'normal' ? ['medium'] : ['low','medium','high'],reasoning_effort:'low',supports_fast_mode:tier === 'fast'},
        {provider_id:'ollama',model:'qwen3:4b',reasoning_levels:[],supports_fast_mode:false},
      ]}))};
      else if (path.endsWith('/voice/runtime/install')) {speechInstalled = true; voiceMutations.push(method); json = {installed:true};}
      else if (path.endsWith('/voice/runtime') && method === 'DELETE') {speechInstalled = false; voiceMutations.push(method); json = {installed:false};}
      else if (path.endsWith('/settings/providers/routing')) {
        routingRequests.push(body);
        if (routingFailure) return route.fulfill({status:503,json:{detail:'Routing save failed.'}});
        if (routingGate) await routingGate;
        routing = body; json = routing;
      }
      else if (path.endsWith('/settings/api-keys/status')) json = {primary_provider:routing.order.find(id=>routing.automatic.includes(id)),routing,providers:Object.fromEntries(['anthropic','openai','gemini','ollama'].map(provider=>[provider,{
        ...status,...(provider === 'ollama' ? {configured:ollamaConfig.configured,auth_method:'api_key',auth_source:'manual',masked_key:null} : {}),
        models:Object.fromEntries(['fast','normal','hard'].map(tier=>[tier,modelOverrides[provider]?.[tier] ?? (provider === 'ollama' ? ollamaConfig.model : `${provider}-${tier}`)])),
      }]))};
      else if (path.endsWith('/usage')) json = {status:'available', checked_at:'2026-09-18T12:00:00Z', message:null, windows:
        (path.includes('/gemini/') ? ['Gemini Fast','Gemini Normal','Gemini Deep Think'] : path.includes('/anthropic/') ? ['Weekly','5 hours','Fable · Weekly'] : ['Weekly'])
          .map((label,index)=>({key:String(index),label,remaining_percent:73,resets_at:'2099-09-23T04:59:59Z'}))};
      else if (path.endsWith('/settings/ollama')) {
        if (method === 'POST') {mutations.push({method,path,body});ollamaConfig = {...ollamaConfig,...body,configured:true};}
        json = ollamaConfig;
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
        const cleared = body.model === ollamaConfig.model && body.base_url === ollamaConfig.base_url;
        if (cleared) ollamaConfig = {...ollamaConfig,model:'',configured:false};
        json = {success:true,cleared_selection:cleared};
      }
      await route.fulfill({json});
    });
    await page.goto(server.resolvedUrls.local[0] + 'instances/test/settings-preview');
    const cards = page.locator('.settings-provider');
    await cards.nth(3).waitFor();
    const cardOrder = () => cards.evaluateAll(nodes=>nodes.map(node=>node.dataset.provider));
    assert.deepEqual(await cardOrder(), routing.order);
    assert.equal(await page.getByRole('button',{name:'Set primary',exact:true}).count(), 0);
    assert.equal(await page.getByText('Automatic',{exact:true}).count(), 0);
    assert.equal(modelSettingsRequests.length, 0, 'Model editing loads only when opened');
    await page.getByRole('region', {name:'OpenAI account usage'}).getByText('73% left').waitFor();
    assert.equal(await page.getByText('Not reported',{exact:true}).count(), 0);
    const removeCard = cards.first();
    await removeCard.getByRole('button',{name:'Remove',exact:true}).click();
    const confirmation = removeCard.locator('.settings-provider-actions');
    await confirmation.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.getByRole('region',{name:'Anthropic account usage'}).getByRole('progressbar').nth(2).waitFor();
    await page.getByRole('region',{name:'Google Gemini account usage'}).getByRole('progressbar').nth(2).waitFor();
    const usageBeforeReordering = usageRequests.length;
    const writesBeforeReordering = routingRequests.length;
    let releaseRouting;
    routingGate = new Promise(resolve=>{releaseRouting=resolve;});
    const quickHandle = page.getByRole('button',{name:'Reorder OpenAI',exact:true});
    const firstRoutingWrite = page.waitForRequest(request=>request.method()==='PUT' && new URL(request.url()).pathname.endsWith('/settings/providers/routing'));
    await quickHandle.focus(); await quickHandle.press('ArrowRight');
    await firstRoutingWrite;
    assert.ok(await cards.locator('.provider-drag').evaluateAll(nodes=>nodes.every(node=>!node.disabled && node.draggable)), 'Drag stays usable while routing saves');
    assert.equal(await page.getByText('Loading usage…',{exact:true}).count(), 0, 'Moving cards never clears quota displays');
    await quickHandle.press('ArrowRight');
    assert.deepEqual(await cardOrder(), ['anthropic','gemini','openai','ollama'], 'A second move applies without waiting for the first save');
    await quickHandle.press('ArrowLeft');
    await quickHandle.press('ArrowLeft');
    assert.deepEqual(await cardOrder(), ['openai','anthropic','gemini','ollama']);
    assert.equal(routingRequests.length-writesBeforeReordering, 1, 'Only one routing write is in flight');
    releaseRouting(); routingGate=null;
    await page.waitForFunction(()=>document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy')==='false');
    assert.equal(routingRequests.length-writesBeforeReordering, 2, 'Intermediate moves coalesce into the final requested order');
    assert.deepEqual(routing.order, await cardOrder(), 'The final order persists, not a stale response');
    assert.equal(usageRequests.length, usageBeforeReordering, 'Routing changes do not re-fetch every provider quota');
    const openaiToggle = page.getByRole('switch',{name:'OpenAI use as fallback',exact:true});
    statusFailure = true;
    await openaiToggle.click();
    await page.waitForFunction(()=>!document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy').includes('true'));
    assert.deepEqual(routing.automatic, ['anthropic','gemini','ollama']);
    assert.equal((await cardOrder())[0], 'anthropic', 'First enabled provider is always the first card');
    assert.equal((await cardOrder()).at(-1), 'openai', 'Disabled cards move to the end');
    assert.equal(await cards.filter({has:page.getByText('OpenAI',{exact:true})}).getByText('Manual only',{exact:true}).count(), 1);
    await page.getByRole('alert').getByText('Provider order was saved, but status could not be refreshed.',{exact:true}).waitFor();
    assert.deepEqual(await cardOrder(), routing.order, 'Failed status refresh keeps the confirmed saved order');
    statusFailure = false;
    await openaiToggle.click();
    await page.waitForFunction(()=>document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy') === 'false');
    routingFailure = true;
    await page.getByRole('switch',{name:'Google Gemini use as fallback',exact:true}).click();
    await page.getByRole('alert').getByText('Routing save failed.',{exact:true}).waitFor();
    assert.equal(await page.getByRole('switch',{name:'Google Gemini use as fallback',exact:true}).getAttribute('aria-checked'), 'true', 'Failed routing writes roll back');
    routingFailure = false;
    await page.getByRole('button',{name:'Reorder Ollama',exact:true}).dragTo(cards.first());
    await page.waitForFunction(()=>document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy') === 'false');
    assert.deepEqual(await cardOrder(), ['ollama','anthropic','gemini','openai']);
    assert.deepEqual(routingRequests.at(-1).order, ['ollama','anthropic','gemini','openai']);
    const handle = page.getByRole('button',{name:'Reorder OpenAI',exact:true});
    await handle.focus(); await page.keyboard.press('ArrowLeft');
    await page.waitForFunction(()=>document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy') === 'false');
    assert.deepEqual(await cardOrder(), ['ollama','anthropic','openai','gemini']);
    await page.reload(); await cards.nth(3).waitFor();
    assert.deepEqual(await cardOrder(), routing.order, 'Reload retains provider order');
    for (const provider of ['Ollama','Anthropic','Google Gemini']) {
      await page.getByRole('switch',{name:provider+' use as fallback',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy') === 'false');
    }
    assert.equal(await openaiToggle.isDisabled(), true, 'Last enabled provider stays enabled');
    for (const provider of ['Ollama','Anthropic','Google Gemini']) {
      await page.getByRole('switch',{name:provider+' use as fallback',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('[aria-label="Providers in routing order"]').getAttribute('aria-busy') === 'false');
    }
    const showSettings = async name => {
      await page.getByRole('button',{name:name+' settings',exact:true}).click();
      await page.getByRole('button',{name:'All providers',exact:true}).waitFor();
      await page.waitForFunction(()=>!document.documentElement.hasAttribute('data-provider-transition'));
    };
    const back = async () => {
      await page.getByRole('button',{name:'All providers',exact:true}).click();
      await cards.nth(3).waitFor();
      await page.waitForFunction(()=>!document.documentElement.hasAttribute('data-provider-transition'));
    };
    let releaseModels;
    modelLoadGate = new Promise(resolve=>{releaseModels=resolve;});
    await showSettings('OpenAI');
    assert.equal(await cards.count(), 0, 'Settings replace the entire card grid');
    const advanced = page.getByRole('region',{name:'Tier models',exact:true});
    await advanced.getByLabel('Fast', {exact:true}).waitFor();
    assert.equal(await advanced.getByLabel('Fast', {exact:true}).isDisabled(), true);
    assert.equal(await advanced.getByRole('button', {name:'Save models',exact:true}).isDisabled(), true);
    releaseModels(); modelLoadGate = null;
    await page.waitForFunction(()=>!document.querySelector('input[aria-label="Fast"]').disabled);
    const connectionMethod = page.getByRole('group',{name:'Connection method'});
    assert.equal(await connectionMethod.getByRole('button',{name:'OAuth',exact:true}).getAttribute('aria-pressed'), 'true');
    await connectionMethod.getByRole('button',{name:'API Key',exact:true}).click();
    await page.getByRole('textbox',{name:'API key',exact:true}).waitFor();
    await connectionMethod.getByRole('button',{name:'OAuth',exact:true}).click();
    assert.equal(await advanced.getByLabel('Fast', {exact:true}).getAttribute('placeholder'), 'openai-fast');
    assert.equal(await advanced.getByRole('button', {name:'Save models',exact:true}).isDisabled(), true);
    assert.equal(await advanced.locator('datalist').count(), 0, 'No browser-native model popup');
    const fastModel = advanced.getByRole('combobox', {name:'Fast',exact:true});
    await fastModel.click();
    await page.getByRole('listbox', {name:'Fast models',exact:true}).getByRole('option', {name:'gpt-6-astra',exact:true}).waitFor();
    await fastModel.fill('astra');
    const suggestions = page.getByRole('listbox', {name:'Fast models',exact:true});
    assert.equal(await suggestions.getByRole('option').count(), 1, 'Typing filters model suggestions');
    await fastModel.press('Enter');
    assert.equal(await fastModel.inputValue(), 'gpt-6-astra', 'Keyboard selection chooses the model');
    await advanced.getByRole('button', {name:'Choose Fast model',exact:true}).click();
    await suggestions.getByRole('option', {name:'gpt-6-luna',exact:true}).click();
    assert.equal(await fastModel.inputValue(), 'gpt-6-luna', 'Pointer selection chooses the model');
    await fastModel.click();
    await fastModel.press('Escape');
    assert.equal(await fastModel.getAttribute('aria-expanded'), 'false');
    await advanced.getByLabel('Fast', {exact:true}).fill('gpt-6-luna');
    modelSaveFailure = true;
    await advanced.getByRole('button', {name:'Save models',exact:true}).click();
    await advanced.getByRole('alert').getByText('Model save failed.', {exact:true}).waitFor();
    assert.equal(await advanced.getByLabel('Fast', {exact:true}).inputValue(), 'gpt-6-luna');
    modelSaveFailure = false;
    await advanced.getByRole('button', {name:'Save models',exact:true}).click();
    await advanced.getByText('Models saved.', {exact:true}).waitFor();
    assert.deepEqual(modelSettingsRequests.find(item => item.method === 'PUT').body,
      {namespace:'codex',fast:'gpt-6-luna',normal:null,hard:null});
    await back();
    await cards.filter({has:page.getByText('OpenAI',{exact:true})}).getByText('GPT-6 Luna',{exact:true}).waitFor();
    await showSettings('OpenAI');
    await advanced.getByLabel('Fast', {exact:true}).waitFor();
    assert.equal(await advanced.getByLabel('Fast', {exact:true}).inputValue(), 'gpt-6-luna');
    await advanced.getByRole('button', {name:'Reset to defaults',exact:true}).click();
    await advanced.getByText('Models saved.', {exact:true}).waitFor();
    assert.deepEqual(modelSettingsRequests.at(-1).body, {namespace:'codex',fast:null,normal:null,hard:null});
    await back();
    await showSettings('Anthropic');
    await page.getByRole('region',{name:'Connection settings'}).getByRole('button',{name:'Auto-sync CLI',exact:true}).waitFor();
    await back();
    await showSettings('Ollama');
    const ollama = page.getByRole('region', {name:'Ollama provider settings',exact:true});
    await ollama.getByLabel('Server URL').waitFor();
    await ollama.getByRole('radio', {name:'qwen3:4b'}).waitFor();
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
    await ollama.getByRole('radio',{name:'qwen3:4b'}).waitFor({state:'detached'});
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
    await back();
    assert.equal(await ollama.getByRole('region', {name:'Ollama settings'}).count(), 0);
    await showSettings('Ollama');
    await ollama.getByLabel('Server URL').fill('https://remote.example/ollama');
    await ollama.getByRole('button',{name:'Connect to server'}).click();
    await ollama.getByRole('progressbar').waitFor();
    finishDownload = true;
    await ollama.getByRole('radio',{name:'new:4b'}).waitFor();
    await ollama.getByRole('radio',{name:'new:4b'}).check();
    await ollama.getByRole('button',{name:'Save provider',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('[aria-label="Ollama provider settings"] .ollama-footer button').disabled);
    assert.deepEqual(mutations.at(-1).body, {base_url:'https://remote.example/ollama',model:'new:4b'});
    await back();
    await showSettings('Ollama');
    await ollama.getByRole('radio',{name:'new:4b'}).waitFor();
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
    assert.equal(await voice.locator('.voice-trace-log').count(), 1);
    assert.deepEqual(await voice.locator('.appearance-setting-row label').allTextContents(), ['Microphone', 'Speech speed', 'Sound cues']);
    assert.equal(await voice.getByText('Background listening',{exact:true}).count(), 0);
    assert.equal(await voice.getByText('Privacy',{exact:true}).count(), 0);
    assert.equal(await voice.locator('footer').textContent(), 'Voice stays connected when the panel closes; use Disconnect to stop listening. Audio stays local; transcripts and recent chat activity go to your selected provider.');
    await voice.getByRole('button',{name:'Remove local voice data…',exact:true}).click();
    assert.deepEqual(voiceMutations, ['POST']);
    await voice.getByRole('alertdialog').getByRole('button',{name:'Cancel',exact:true}).click();
    assert.deepEqual(voiceMutations, ['POST']);
    await voice.getByRole('button',{name:'Remove local voice data…',exact:true}).click();
    await voice.getByRole('alertdialog').getByRole('button',{name:'Remove local voice data',exact:true}).click();
    await voice.getByRole('button',{name:'Install local speech engine'}).waitFor();
    assert.deepEqual(voiceMutations, ['POST','DELETE']);
    await page.getByRole('navigation',{name:'Settings sections'}).getByRole('button',{name:'LLM Providers',exact:true}).click();
    const openaiUsage = page.getByRole('region', {name:'OpenAI account usage'});
    await openaiUsage.getByText('73% left').waitFor();
    usageFailure = true;
    await openaiUsage.getByRole('button', {name:'Refresh OpenAI usage'}).click();
    await openaiUsage.getByText('Usage is temporarily unavailable.').waitFor();
    assert.equal(await openaiUsage.getByRole('progressbar').count(), 0);
    oauth = false;
    const usageCount = usageRequests.length;
    await page.reload();
    await page.getByText('API Key', {exact:true}).first().waitFor();
    assert.equal(await page.getByRole('region', {name:/account usage/}).count(), 0);
    assert.equal(usageRequests.length, usageCount, 'API-key cards must not request OAuth usage');
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
