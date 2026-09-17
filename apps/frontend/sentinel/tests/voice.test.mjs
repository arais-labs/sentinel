import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';

test('Voice keeps smoke moving during setup, installs locally, captures a turn, and releases its lease', async () => {
  const server = await createServer({ root: fileURLToPath(new URL('..', import.meta.url)), configFile: false,
    plugins: [react()], server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
  let browser;
  try {
    await server.listen();
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}), args: ['--autoplay-policy=no-user-gesture-required'] });
    const page = await browser.newPage({ viewport: { width: 700, height: 1050 }, locale: 'fr-FR' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const assertFitsPane = async state => {
      for (const viewport of [{width:700,height:1050}, {width:390,height:650}, {width:900,height:450}]) {
        await page.setViewportSize(viewport);
        const layout = await page.evaluate(() => {
          const pane = document.querySelector('.voice-pane');
          const bounds = pane.getBoundingClientRect();
          const footer = {top:bounds.bottom,bottom:bounds.bottom};
          const controls = [...document.querySelectorAll('.voice-controls, .voice-selectors, .voice-status')].map(node => node.getBoundingClientRect());
          return {
            overflow: getComputedStyle(pane).overflowY,
            extraHeight: pane.scrollHeight - pane.clientHeight,
            extraWidth: pane.scrollWidth - pane.clientWidth,
            documentOverflow: document.documentElement.scrollHeight - innerHeight,
            controlsFit: controls.every(rect => rect.top >= bounds.top && rect.bottom <= footer.top + 1),
            footerFits: footer.bottom <= bounds.bottom + 1,
          };
        });
        assert.equal(layout.overflow, 'hidden', state);
        assert.ok(layout.extraHeight <= 1, `${state} ${JSON.stringify(viewport)}: ${JSON.stringify(layout)}`);
        assert.ok(layout.extraWidth <= 1 && layout.documentOverflow <= 1 && layout.controlsFit && layout.footerFits, `${state}: ${JSON.stringify(layout)}`);
      }
      await page.setViewportSize({width:700,height:1050});
    };
    await page.addInitScript(() => {
      window.voiceRequests = []; window.voiceTracks = []; window.voiceStreamMessages = [];
      // The desktop socket bridge, faked: a Voice UI channel and the Voice session stream.
      const listeners = new Set();
      const sockets = new Map();
      const emit = event => { for (const listener of listeners) listener(event); };
      const later = (ms, fn) => setTimeout(fn, ms);
      window.sentinelDesktop = {
        onSocketEvent: listener => { listeners.add(listener); return () => listeners.delete(listener); },
        socketOpen: async (id, url) => {
          sockets.set(id, url);
          later(5, () => emit({ id, type: 'open' }));
          if (url.includes('/stream')) later(10, () => emit({ id, type: 'message', data: JSON.stringify({ type: 'connected', session_id: 'voice-session', run_active: false }) }));
        },
        socketSend: (id, payload) => {
          const url = sockets.get(id) ?? '';
          if (!url.includes('/stream')) return;
          let message; try { message = JSON.parse(String(payload)); } catch { return; }
          if (message.type !== 'message') return;
          window.voiceStreamMessages.push(message);
          const frames = [
            { type: 'run_state', run_active: true }, { type: 'agent_thinking' },
            { type: 'toolcall_start', tool_call: { id: 'call-1', name: 'chats', arguments: { action: 'list' } } },
            { type: 'done', stop_reason: 'tool_use' },
            { type: 'tool_result', tool_result: { tool_call_id: 'call-1', tool_name: 'chats', content: { chats: [] }, is_error: false, metadata: {} } },
            { type: 'text_delta', delta: 'No chats ' }, { type: 'text_delta', delta: 'are running.' },
            { type: 'done', stop_reason: 'end_turn' }, { type: 'run_state', run_active: false },
          ];
          frames.forEach((frame, index) => later(20 + index * 15, () => { if (sockets.has(id)) emit({ id, type: 'message', data: JSON.stringify({ session_id: 'voice-session', ...frame }) }); }));
        },
        socketClose: (id, code = 1000, reason = '') => { sockets.delete(id); later(1, () => emit({ id, type: 'close', code, reason })); },
      };
      let installed = false, ready = false;
      window.makeVoiceReady = () => { ready = true; };
      window.installTestVoice = () => { installed = true; };
      const originalFetch = window.fetch;
      window.fetch = async (input, options = {}) => {
        const url = String(input);
        if (!url.startsWith('sentinel:')) return originalFetch(input, options);
        window.voiceRequests.push({ url, method: options.method, body: typeof options.body === 'string' ? options.body : null });
        const response = body => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
        if (url.endsWith('/status')) return response({ ready: installed && ready, model: 'qwen3:4b', provider: 'ollama', provider_configured: ready, issues: [], runtime: { installed, installing: false, running: false, phase: 'ready', error: '', path: '/data/voice' } });
        if (url.endsWith('/models')) return response({models:[{tier:'fast',provider_options:[{provider_id:'ollama',model:'qwen3:4b'}]}]});
        if (url.endsWith('/runtime/install')) { installed = true; return response({ installed }); }
        if (url.endsWith('/runtime/connect')) return response({ lease_id: 'lease' });
        if (url.endsWith('/voice/session')) return response({ session_id: 'voice-session' });
        if (url.endsWith('/sessions/voice-session/workspace')) return response(null);
        if (url.endsWith('/workspaces') || url.endsWith('/machines')) return response([]);
        if (url.endsWith('/voice/session/reset')) return response({ session_id: 'voice-session-2', previous_session_id: 'voice-session' });
        if (url.endsWith('/sessions/voice-session/stop')) return response({ status: 'stopping' });
        if (url.includes('/runtime/leases/')) return response({ ok: true });
        if (url.includes('/sessions?')) return response({ items: [] });
        if (url.includes('/transcribe?')) {
          const audio = new DataView(await options.body.arrayBuffer());
          if (audio.getUint32(24, true) !== 16000) throw new Error('Unexpected sample rate');
          return response({ text: 'What is running?' });
        }
        if (url.endsWith('/speak')) {
          // Two seconds of real PCM WAV: exercise playback without OS speech.
          const bytes = new Uint8Array(44 + 24000 * 2 * 2), wav = new DataView(bytes.buffer);
          for (const [offset, text] of [[0, 'RIFF'], [8, 'WAVE'], [12, 'fmt '], [36, 'data']])
            [...text].forEach((char, i) => bytes[offset + i] = char.charCodeAt(0));
          wav.setUint32(4, bytes.length - 8, true); wav.setUint32(16, 16, true);
          wav.setUint16(20, 1, true); wav.setUint16(22, 1, true); wav.setUint32(24, 24000, true);
          wav.setUint32(28, 48000, true); wav.setUint16(32, 2, true); wav.setUint16(34, 16, true);
          wav.setUint32(40, bytes.length - 44, true);
          return response({audio: btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join('')), mime_type: 'audio/wav'});
        }
        throw new Error('Unexpected voice request: ' + url);
      };
      navigator.mediaDevices.getUserMedia = async () => {
        const context = new AudioContext();
        await context.resume();
        const destination = context.createMediaStreamDestination();
        window.voiceTracks.push(...destination.stream.getTracks());
        window.sayTestPhrase = () => {
          const oscillator = context.createOscillator(), gain = context.createGain();
          oscillator.frequency.value = 220; gain.gain.value = .1;
          oscillator.connect(gain).connect(destination); oscillator.start(); oscillator.stop(context.currentTime + .5);
        };
        return destination.stream;
      };
      speechSynthesis.speak = () => { throw new Error('System speech must not be used'); };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/voice.html`);
    await page.getByRole('heading', { name: 'Local voice setup' }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Settings → Voice' }).count(), 1);
    assert.equal(await page.getByRole('combobox').count(), 0);
    await assertFitsPane('setup');
    const before = await page.locator('.voice-orbit').screenshot();
    await page.waitForTimeout(350);
    const after = await page.locator('.voice-orbit').screenshot();
    assert.notDeepEqual(before, after, 'Smoke must animate even before setup');
    await page.evaluate(() => { window.installTestVoice(); window.makeVoiceReady(); });
    await page.getByRole('button', { name: 'Check again' }).click();
    await page.getByRole('heading', { name: 'I’m listening' }).waitFor();
    await assertFitsPane('listening');
    await page.evaluate(() => window.setVoiceProvider('ollama'));
    await page.evaluate(() => window.sayTestPhrase());
    await page.locator('.voice-activity li', { hasText: 'chats' }).filter({ hasText: 'list' }).waitFor();
    await page.getByText('No chats are running.', { exact: true }).waitFor();
    assert.equal(await page.locator('.voice-activity li[data-status=done]', { hasText: 'chats' }).count(), 1, 'Tool calls are shown with their outcome');
    assert.match(await page.locator('.voice-activity li .voice-activity-meta').first().textContent(), /Completed · \d\.\ds/, 'Finished calls show their duration');
    assert.equal(await page.locator('.voice-activity li .voice-activity-action').first().textContent(), 'list');
    await page.waitForFunction(() => window.voiceRequests.some(r => r.url.endsWith('/speak')));
    assert.deepEqual(await page.evaluate(() => JSON.parse(window.voiceRequests.find(r => r.url.endsWith('/speak')).body)),
      { text: 'No chats are running.', lease_id: 'lease', speed: 1.05 });
    // Speaking over playback must become the next request without clicking stop.
    await page.getByRole('button', {name: 'Interrupt reply', exact: true}).waitFor();
    await page.evaluate(() => window.sayTestPhrase());
    await page.waitForFunction(() => window.voiceStreamMessages.length === 2);
    await page.waitForFunction(() => window.voiceRequests.filter(r => r.url.endsWith('/speak')).length === 2);
    await page.getByRole('button', {name: 'Interrupt reply', exact: true}).click();
    await page.getByRole('heading', {name: 'I’m listening'}).waitFor();
    await assertFitsPane('completed transcript');
    await page.setViewportSize({width:900,height:450});
    assert.ok(await page.locator('.voice-caption-line').evaluate(node => node.scrollHeight <= node.clientHeight && node.scrollWidth <= node.clientWidth));
    await page.screenshot({path:'/tmp/sentinel-voice-short-pane.png'});
    await page.setViewportSize({width:700,height:1050});
    assert.equal(await page.evaluate(() => window.voiceStreamMessages.length), 2);
    assert.deepEqual(await page.evaluate(() => { const m = window.voiceStreamMessages[0]; return [m.type, m.content, m.agent_mode, m.provider_id, m.tier]; }), ['message', 'What is running?', 'voice', 'ollama', 'fast']);
    assert.equal(await page.evaluate(() => window.voiceRequests.some(r => r.url.endsWith('/sessions/voice-session/stop'))), true, 'Interrupting stops the Voice run');
    assert.equal(await page.evaluate(() => window.voiceRequests.some(r => r.url.endsWith('/sessions') && r.method === 'POST')), false);
    await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
    await page.getByRole('heading', { name: 'Voice is disconnected' }).waitFor();
    await page.waitForFunction(() => window.voiceTracks.every(track => track.readyState === 'ended'));
    assert.equal(await page.evaluate(() => window.voiceRequests.some(r => r.url.endsWith('/runtime/leases/lease') && r.method === 'DELETE')), true);
    // A denied native permission should guide recovery, never start Whisper or
    // produce a dead-end raw browser error. Returning from settings reconnects.
    await page.evaluate(() => {
      window.nativeMicState = 'denied'; window.nativeMicRequests = 0; window.micSettingsOpened = false;
      const permission = () => ({ status: window.nativeMicState, platform: 'darwin', appName: 'Electron', canOpenSettings: true });
      Object.assign(window.sentinelDesktop, {
        getMicrophonePermission: async () => permission(),
        requestMicrophonePermission: async () => { window.nativeMicRequests++; return permission(); },
        openMicrophoneSettings: async () => { window.micSettingsOpened = true; },
      });
    });
    const connections = await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/connect')).length);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('heading', { name: 'Microphone access', exact: true }).waitFor();
    await assertFitsPane('microphone permission');
    assert.equal(await page.getByText('Electron', { exact: true }).isVisible(), true);
    assert.equal(await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/connect')).length), connections);
    await page.getByRole('button', { name: 'Open microphone settings' }).click();
    assert.equal(await page.evaluate(() => window.micSettingsOpened), true);
    await page.evaluate(() => { window.nativeMicState = 'granted'; window.dispatchEvent(new Event('focus')); });
    await page.getByRole('heading', { name: 'I’m listening' }).waitFor();
    await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
    // Browser-level denial after a native grant must release the worker lease.
    await page.evaluate(() => {
      window.originalMicrophone = navigator.mediaDevices.getUserMedia;
      navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Permission denied', 'NotAllowedError'); };
    });
    const releases = await page.evaluate(() => window.voiceRequests.filter(r => r.url.includes('/runtime/leases/') && r.method === 'DELETE').length);
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('heading', { name: 'Microphone access', exact: true }).waitFor();
    assert.equal(await page.getByText('Permission denied', { exact: true }).count(), 0);
    await page.waitForFunction(count => window.voiceRequests.filter(r => r.url.includes('/runtime/leases/') && r.method === 'DELETE').length > count, releases);
    await page.evaluate(() => { navigator.mediaDevices.getUserMedia = window.originalMicrophone; });
    await page.getByRole('button', { name: 'Not now', exact: true }).click();
    await page.getByRole('button', { name: 'Connect', exact: true }).click();
    await page.getByRole('heading', { name: 'I’m listening' }).waitFor();
    await page.evaluate(() => window.hideVoice());
    await page.waitForFunction(() => window.voiceTracks.every(track => track.readyState === 'ended'));

    // The same voice loop is an app-level anchored popover, never a session pane.
    await page.setViewportSize({width:1320,height:900});
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/voice.html?overlay=1`);
    await page.getByRole('button', {name:'Voice · Paused',exact:true}).waitFor();
    assert.equal(await page.evaluate(() => window.workspaceTabs.includes('voice')), false);
    assert.equal(await page.locator('.topbar-voice-trigger canvas').count(), 0);
    assert.equal(await page.evaluate(() => window.voiceRequests.length), 0, 'Closed Voice does not request a microphone or start its runtime');
    const voiceTrigger = page.locator('.topbar-voice-trigger');
    await voiceTrigger.click();
    const dialog = page.getByRole('dialog',{name:'Voice',exact:true});
    const island = page.locator('.topbar-voice-island');
    await dialog.getByRole('heading',{name:'Local voice setup'}).waitFor();
    await page.waitForTimeout(300);
    const box = await dialog.boundingBox(), anchor = await voiceTrigger.boundingBox();
    assert.ok(box.width <= 1320 / 3 + 1);
    // The island keeps its trigger off-center; the stage centers on the screen.
    assert.ok(Math.abs(box.x + box.width / 2 - 1320 / 2) < 1, `Popover must center on the screen: ${JSON.stringify({ box, anchor })}`);
    assert.ok(box.y >= anchor.y + anchor.height && box.height < 550);
    assert.equal(await dialog.evaluate(node => node.scrollHeight <= node.clientHeight + 1), true);
    await page.screenshot({path:'/tmp/sentinel-voice-popover-setup.png'});
    await page.keyboard.press('Escape');
    await dialog.waitFor({state:'hidden'});
    assert.equal(await voiceTrigger.getAttribute('aria-expanded'), 'false');
    await voiceTrigger.click();
    await page.evaluate(() => { window.installTestVoice(); window.makeVoiceReady(); });
    await dialog.getByRole('button',{name:'Check again'}).click();
    await dialog.getByRole('heading',{name:'I’m listening'}).waitFor();
    assert.equal(await page.getByRole('button',{name:'Voice · Listening',exact:true}).getAttribute('data-phase'), 'listening');
    const beforeSwitch = await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/connect')).length);
    await page.evaluate(() => { window.focusAnotherPane(); window.switchChat(); });
    await dialog.getByRole('heading',{name:'I’m listening'}).waitFor();
    assert.equal(await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/connect')).length), beforeSwitch);
    await page.waitForTimeout(400);
    // The ring is the only visual in the stage; the caption sits centered below it and
    // the controls live in the top-bar island while the popover is open.
    const geometry = await dialog.evaluate(node => {
      const orbit = node.querySelector('.voice-orbit').getBoundingClientRect();
      const status = node.querySelector('.voice-status').getBoundingClientRect();
      return {
        textX: Math.abs(status.x + status.width / 2 - (orbit.x + orbit.width / 2)),
        textBelow: status.top >= orbit.bottom,
        iconHidden: getComputedStyle(node.querySelector('.voice-waveform')).display === 'none',
        controlsInDialog: node.querySelectorAll('.voice-controls').length,
      };
    });
    assert.ok(geometry.textX < 1 && geometry.textBelow && geometry.iconHidden && geometry.controlsInDialog === 0, JSON.stringify(geometry));
    assert.equal(await page.locator('.topbar-voice-island .voice-controls').count(), 1);
    assert.equal(await island.getByRole('button', { name: 'Attach a workspace' }).count(), 1, 'Voice attaches a workspace from the island');
    await page.screenshot({path:'/tmp/sentinel-voice-popover-listening.png'});
    assert.equal(await dialog.getByRole('combobox').count(), 0);
    assert.equal(await dialog.locator('.voice-footer').count(), 0);
    await island.getByRole('button',{name:'Disable spoken replies'}).click();
    const releasesBeforeClose = await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/leases/lease') && r.method === 'DELETE').length);
    await page.getByRole('button',{name:'Workspace remains interactive'}).click();
    await dialog.waitFor({state:'hidden'});
    assert.equal(await page.evaluate(() => window.voiceTracks.every(track => track.readyState === 'live')), true);
    assert.equal(await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/leases/lease') && r.method === 'DELETE').length), releasesBeforeClose);
    await page.evaluate(() => window.setVoiceSelection({tier:'hard',provider_id:'anthropic',reasoning_level:'high',fast_mode:true}));
    assert.equal(await page.evaluate(() => window.voiceRequests.filter(r => r.url.endsWith('/runtime/connect')).length), beforeSwitch, 'Selection changes must not interrupt the connection');
    await page.evaluate(() => window.sayTestPhrase());
    await page.waitForFunction(() => window.voiceStreamMessages.length > 0);
    const backgroundTurn = await page.evaluate(() => window.voiceStreamMessages.at(-1));
    assert.equal(backgroundTurn.agent_mode, 'voice');
    assert.equal(backgroundTurn.tier, 'hard');
    assert.equal(backgroundTurn.provider_id, 'anthropic');
    assert.equal(backgroundTurn.reasoning_level, 'high');
    assert.equal(backgroundTurn.fast_mode, true);
    await voiceTrigger.click();
    await dialog.getByRole('heading',{name:'I’m listening'}).waitFor();
    await island.getByRole('button',{name:'Close Voice',exact:true}).click();
    await dialog.waitFor({state:'hidden'});
    assert.equal(await voiceTrigger.evaluate(node => node === document.activeElement), true);
    assert.equal(await voiceTrigger.getAttribute('data-phase'), 'listening');
    await voiceTrigger.click();
    await island.getByRole('button',{name:'Disconnect',exact:true}).click();
    await page.waitForFunction(() => window.voiceTracks.every(track => track.readyState === 'ended'));

    // Captions are fitted sentences beneath the ring, never simultaneous speakers or a scroll area.
    await page.goto(`${server.resolvedUrls.local[0]}tests/fixtures/voice.html?captions=1`);
    await page.setViewportSize({width:390,height:650});
    const line = page.locator('.voice-caption-line');
    await page.getByRole('heading', {name:'I’m listening'}).waitFor();
    const assertCaptionFits = async () => {
      assert.equal(await page.locator('.voice-orbit-center h2, .voice-orbit-center p').count(), 1);
      const fit = await line.evaluate(node => {
        const style = getComputedStyle(node);
        return { fits:node.scrollWidth <= node.clientWidth && node.scrollHeight <= node.clientHeight, scrolls:['auto','scroll'].includes(style.overflowY) };
      });
      assert.ok(fit.fits && !fit.scrolls, JSON.stringify(fit));
    };
    await page.evaluate(() => window.setVoiceCaption({label:'Thinking',heard:'Please check my chats.',reply:''}));
    await page.getByRole('heading',{name:'Please check my chats.'}).waitFor();
    assert.equal(await line.getAttribute('data-tone'), 'user');
    const userStyle = await line.evaluate(node => { const style = getComputedStyle(node); return `${style.color}|${style.fontStyle}`; });
    await assertCaptionFits();
    const longReply = 'Your first agent is checking the tests while the second agent reviews the latest changes.';
    await page.evaluate(reply => window.setVoiceCaption({label:'I’m listening',heard:'Please check my chats.',reply}), longReply);
    await page.waitForFunction(() => document.querySelector('.voice-caption-line').dataset.tone === 'assistant');
    // The assistant's caption is visibly distinct from the user's (color or italics).
    assert.notEqual(await line.evaluate(node => { const style = getComputedStyle(node); return `${style.color}|${style.fontStyle}`; }), userStyle);
    const pieces = [];
    while (await line.getAttribute('data-tone') === 'assistant') {
      const text = await line.textContent();
      pieces.push(text);
      await assertCaptionFits();
      await page.waitForFunction(previous => document.querySelector('.voice-caption-line').textContent !== previous, text);
    }
    assert.equal(pieces.join(' '), longReply, 'Every phrase advances in order without dropping words');
    // A reply that grows sentence by sentence continues; it never replays what was shown.
    await page.evaluate(() => {
      window.captionLog = [];
      new MutationObserver(() => window.captionLog.push(document.querySelector('.voice-caption-line').textContent))
        .observe(document.querySelector('.voice-status'), { subtree: true, childList: true, characterData: true });
      window.setVoiceCaption({label:'I’m listening', heard:'Please check my chats.', reply:'The build passed.'});
    });
    await page.getByRole('heading', {name:'The build passed.'}).waitFor();
    await page.evaluate(() => window.setVoiceCaption({label:'I’m listening', heard:'Please check my chats.', reply:'The build passed. Lint is still running.'}));
    await page.getByRole('heading', {name:'Lint is still running.'}).waitFor();
    await page.evaluate(() => window.setVoiceCaption({label:'I’m listening', heard:'Please check my chats.', reply:'The build passed. Lint is still running. Deploy can start.'}));
    await page.getByRole('heading', {name:'Deploy can start.'}).waitFor();
    assert.deepEqual(await page.evaluate(() => window.captionLog.filter(text => /passed|Lint|Deploy/.test(text))),
      ['The build passed.', 'Lint is still running.', 'Deploy can start.'], 'Sentences animate once, in order');
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.evaluate(() => window.setVoiceCaption({label:'Thinking',heard:'AnUnbrokenModelIdentifier'.repeat(8),reply:''}));
    await page.waitForFunction(() => document.querySelector('.voice-caption-line').dataset.tone === 'user');
    await assertCaptionFits();
    await page.setViewportSize({width:280,height:650});
    await page.waitForTimeout(100);
    await assertCaptionFits();
    assert.deepEqual(errors, []);
  } finally { await browser?.close(); await server.close(); }
});
