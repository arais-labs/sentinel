import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { transformWithOxc } from 'vite';

let source = readFileSync(new URL('../src/lib/desktop-audio.ts', import.meta.url), 'utf8');
source = source.replace("new URL('./desktop-audio-worklet.js', import.meta.url).href", "'worklet.js'");
const { code } = await transformWithOxc(source, 'desktop-audio.ts');
const { DesktopAudio } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));

function worklet() {
  let Processor;
  runInNewContext(readFileSync(new URL('../src/lib/desktop-audio-worklet.js', import.meta.url), 'utf8'), {
    Float32Array,
    AudioWorkletProcessor: class { port = {}; },
    registerProcessor: (name, constructor) => { assert.equal(name, 'sentinel-desktop-audio'); Processor = constructor; },
  });
  const instance = new Processor();
  return {
    instance,
    push(left, right = left) {
      const samples = new Float32Array(960);
      samples.fill(left, 0, 480); samples.fill(right, 480);
      instance.port.onmessage({ data: samples });
    },
    render(size = 128) {
      const channels = [new Float32Array(size), new Float32Array(size)];
      const active = instance.process([], [channels]);
      return { active, channels };
    },
  };
}

test('desktop audio buffers stereo, bounds lag, resets on underrun and shuts down', () => {
  const player = worklet();
  player.push(.25, -.25);
  assert(player.render().channels[0].every(value => value === 0), 'starts with a 20 ms cushion');
  player.push(.5, -.5);
  const output = player.render(480);
  assert(output.channels[0].every(value => value === .25));
  assert(output.channels[1].every(value => value === -.25));
  player.render(480);
  assert(player.render().channels[0].every(value => value === 0), 'underrun emits silence');
  assert.equal(player.instance.primed, false);
  player.instance.port.onmessage({ data: 'reset' });
  for (let index = 0; index < 9; index++) player.push(index / 16);
  assert.equal(player.instance.length, 960, 'late bursts retain only the newest 20 ms');
  assert(player.render(480).channels[0].every(value => value === 7 / 16));
  player.instance.port.onmessage({ data: 'close' });
  assert.equal(player.render().active, false);
});

function playback(t) {
  const original = Object.getOwnPropertyDescriptors(globalThis);
  const decoders = [], contexts = [], nodes = [];
  class Context {
    state = 'suspended'; destination = {};
    audioWorklet = { addModule: async () => {} };
    constructor() { contexts.push(this); }
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
  }
  class Decoder {
    state = 'unconfigured'; decodeQueueSize = 0; chunks = []; resets = 0;
    constructor(callbacks) { this.callbacks = callbacks; decoders.push(this); }
    configure(config) { this.config = config; this.state = 'configured'; }
    decode(chunk) { this.chunks.push(chunk); }
    reset() { this.resets++; this.decodeQueueSize = 0; this.state = 'unconfigured'; }
    close() { this.state = 'closed'; }
  }
  class Node {
    messages = [];
    port = { postMessage: data => this.messages.push(data), close() {} };
    constructor() { nodes.push(this); }
    connect() {}
    disconnect() {}
  }
  Object.assign(globalThis, { AudioContext: Context, AudioDecoder: Decoder, AudioWorkletNode: Node,
    EncodedAudioChunk: class { constructor(options) { Object.assign(this, options); } } });
  t.after(() => {
    for (const name of ['AudioContext', 'AudioDecoder', 'AudioWorkletNode', 'EncodedAudioChunk']) {
      if (original[name]) Object.defineProperty(globalThis, name, original[name]); else delete globalThis[name];
    }
  });
  return { decoders, contexts, nodes };
}

test('audio waits for user activation, drops hidden sound and bounds decode backlog', async t => {
  const { decoders, contexts, nodes } = playback(t), errors = [];
  const audio = new DesktopAudio(error => errors.push(error));
  audio.receive(Uint8Array.of(0xf4));
  assert.equal(contexts.length, 0);
  await audio.enable(); await audio.enable();
  assert.equal(contexts.length, 1);
  audio.receive(Uint8Array.of(0xf4));
  assert.equal(decoders[0].chunks.length, 1);
  audio.setVisible(false); audio.receive(Uint8Array.of(0xf4));
  assert.equal(decoders[0].chunks.length, 1);
  audio.setVisible(true);
  decoders[0].decodeQueueSize = 8;
  audio.receive(Uint8Array.of(0xf4));
  assert.equal(decoders[0].resets, 1);
  let closed = 0;
  decoders[0].callbacks.output({ numberOfFrames: 480, numberOfChannels: 2, sampleRate: 48000,
    copyTo: (data, options) => data.fill(options.planeIndex ? -.5 : .5), close: () => closed++ });
  const samples = nodes[0].messages.at(-1);
  assert(samples.subarray(0, 480).every(value => value === .5));
  assert(samples.subarray(480).every(value => value === -.5));
  assert.equal(closed, 1);
  audio.close(); audio.close();
  assert.equal(contexts[0].state, 'closed');
  assert.equal(decoders[0].state, 'closed');
  assert.deepEqual(errors, []);
});

test('audio failure releases resources and reports once without escaping the player', async t => {
  const { decoders, contexts } = playback(t), errors = [];
  const audio = new DesktopAudio(error => errors.push(error));
  await audio.enable();
  audio.receive(new Uint8Array(1276));
  audio.receive(new Uint8Array(1276));
  assert.deepEqual(errors, ['Invalid desktop audio packet']);
  assert.equal(decoders[0].state, 'closed');
  assert.equal(contexts[0].state, 'closed');
});

test('closing during audio initialization cannot recreate output resources', async t => {
  const { contexts, nodes } = playback(t);
  const audio = new DesktopAudio();
  const starting = audio.enable();
  audio.close();
  await starting;
  assert.equal(contexts[0].state, 'closed');
  assert.equal(nodes.length, 0);
});
