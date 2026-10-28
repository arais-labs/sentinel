import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { transformWithOxc } from 'vite';

async function moduleURL(name, replacements = {}) {
  let source = readFileSync(new URL('../src/lib/' + name + '.ts', import.meta.url), 'utf8');
  for (const [from, to] of Object.entries(replacements)) source = source.replace(from, to);
  const { code: js } = await transformWithOxc(source, name + '.ts');
  return 'data:text/javascript;base64,' + Buffer.from(js).toString('base64');
}
const protocolURL = await moduleURL('desktop-video-protocol');
const { VideoPacketReader, videoPacket, videoConfiguration, MAX_VIDEO_PACKET } = await import(protocolURL);
const audioURL = await moduleURL('desktop-audio');
const { DesktopVideo } = await import(await moduleURL('desktop-video', { './desktop-video-protocol': protocolURL, './desktop-audio': audioURL }));
const { attachDesktopInput } = await import(await moduleURL('desktop-video-input'));

function styles() {
  const values = new Map();
  return { values, setProperty: (name, value) => values.set(name, value), removeProperty: name => values.delete(name) };
}

test('guest cursor region excludes letterboxing and resets on exit and teardown', () => {
  const original = Object.getOwnPropertyDescriptors(globalThis);
  let resized;
  Object.assign(globalThis, { window: new EventTarget(), document: new EventTarget(),
    ResizeObserver: class { constructor(callback) { resized = callback; } observe() {} disconnect() {} } });
  const canvas = new EventTarget(), attributes = new Set();
  let rect = { left: 0, top: 0, width: 400, height: 400 };
  Object.assign(canvas, { width: 400, height: 200, getBoundingClientRect: () => rect, style: styles(),
    toggleAttribute: (key, enabled) => enabled ? attributes.add(key) : attributes.delete(key) });
  const enter = (x, y) => canvas.dispatchEvent(Object.assign(new Event('pointerenter'), { clientX: x, clientY: y }));
  const hidden = () => attributes.has('data-guest-pointer');
  let detach;
  try {
    detach = attachDesktopInput(canvas, { input() {} });
    enter(200, 50); assert.equal(hidden(), false, 'top margin');
    enter(200, 200); assert.equal(hidden(), true, 'desktop pixels');
    rect = { left: 0, top: 0, width: 1000, height: 200 };
    resized(); assert.equal(hidden(), false, 'resize restores cursor in a margin');
    enter(500, 100); assert.equal(hidden(), true);
    canvas.dispatchEvent(new Event('pointerleave')); assert.equal(hidden(), false);
    enter(500, 100); detach(); assert.equal(hidden(), false);
  } finally {
    detach?.();
    for (const name of ['window', 'document', 'ResizeObserver']) {
      if (original[name]) Object.defineProperty(globalThis, name, original[name]); else delete globalThis[name];
    }
  }
});

test('pointer moves go out at once; a click sends position and button in one report', () => {
  const original = Object.getOwnPropertyDescriptors(globalThis);
  Object.assign(globalThis, { window: new EventTarget(), document: new EventTarget(),
    ResizeObserver: class { observe() {} disconnect() {} } });
  const canvas = new EventTarget(), reports = [];
  Object.assign(canvas, { width: 400, height: 200, getBoundingClientRect: () => ({ left: 0, top: 0, width: 400, height: 200 }),
    style: styles(), focus() {}, setPointerCapture() {}, toggleAttribute() {} });
  let detach;
  const kinds = report => report.events.map(event => [event.type, event.code]);
  try {
    detach = attachDesktopInput(canvas, { input: (device, events) => reports.push({ device, events }) });
    canvas.dispatchEvent(Object.assign(new Event('pointermove'), { clientX: 90, clientY: 45 }));
    assert.equal(reports.length, 1, 'no wait for the next animation frame');
    assert.deepEqual(kinds(reports[0]), [[3, 0], [3, 1], [0, 0]]);
    canvas.dispatchEvent(Object.assign(new Event('pointerdown', { cancelable: true }),
      { button: 0, pointerId: 1, clientX: 100, clientY: 50 }));
    assert.equal(reports.length, 2);
    assert.equal(reports[1].device, 1);
    assert.deepEqual(kinds(reports[1]), [[3, 0], [3, 1], [1, 272], [0, 0]]);
    assert.deepEqual(reports[1].events.slice(0, 2).map(event => event.value), [16384, 16384]);
  } finally {
    detach?.();
    for (const name of ['window', 'document', 'ResizeObserver']) {
      if (original[name]) Object.defineProperty(globalThis, name, original[name]); else delete globalThis[name];
    }
  }
});

test('guest cursor becomes the native pointer at the drawn scale, hidden when the guest hides it', () => {
  const original = Object.getOwnPropertyDescriptors(globalThis);
  const drawn = [];
  const surface = () => ({ width: 0, height: 0,
    getContext: () => ({ putImageData() {}, drawImage: (...args) => drawn.push(args.slice(1)) }),
    toDataURL: () => 'data:image/png;base64,AA' });
  Object.assign(globalThis, { window: Object.assign(new EventTarget(), { devicePixelRatio: 2 }),
    document: Object.assign(new EventTarget(), { createElement: surface }),
    ImageData: class { constructor(data, width, height) { Object.assign(this, { data, width, height }); } },
    ResizeObserver: class { observe() {} disconnect() {} } });
  const canvas = new EventTarget(), style = styles();
  Object.assign(canvas, { width: 1920, height: 1080, style, toggleAttribute() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 960, height: 540 }) });
  const video = { input() {} };
  let detach;
  try {
    detach = attachDesktopInput(canvas, video);
    assert.equal(style.values.has('--guest-cursor'), false, 'hidden over the desktop until the guest sends a cursor');
    video.cursor = { hotX: 4, hotY: 10, width: 24, height: 24, pixels: new Uint8ClampedArray(24 * 24 * 4) };
    video.onCursor(video.cursor);
    assert.equal(style.values.get('--guest-cursor'), 'image-set(url("data:image/png;base64,AA") 2x) 2 5, default');
    assert.deepEqual(drawn.at(-1), [0, 0, 24, 24], 'half-size desktop on a 2x display: 12 CSS px, 24 device px');
    video.cursor = null; video.onCursor(null);
    assert.equal(style.values.get('--guest-cursor'), 'none');
    detach(); detach = undefined;
    assert.equal(video.onCursor, undefined);
  } finally {
    detach?.();
    for (const name of ['window', 'document', 'ImageData', 'ResizeObserver']) {
      if (original[name]) Object.defineProperty(globalThis, name, original[name]); else delete globalThis[name];
    }
  }
});

test('desktop protocol survives every split, coalesced packets and empty messages', () => {
  const packets = [videoPacket(1, Uint8Array.of(4, 5)), videoPacket(4), videoPacket(2, Uint8Array.of(6, 7, 8), 1, 123456), videoPacket(5, Uint8Array.of(0xf4))];
  const bytes = Buffer.concat(packets);
  for (let split = 0; split <= bytes.length; split++) {
    const received = [], reader = new VideoPacketReader(packet => received.push(packet));
    reader.push(bytes.subarray(0, split)); reader.push(bytes.subarray(split));
    assert.deepEqual(received.map(p => [p.type, p.flags, p.timestamp, [...p.data]]), [[1, 0, 0, [4, 5]], [4, 0, 0, []], [2, 1, 123456, [6, 7, 8]], [5, 0, 0, [0xf4]]]);
  }
  const received = [], reader = new VideoPacketReader(p => received.push(p));
  for (const byte of bytes) reader.push(Uint8Array.of(byte));
  assert.equal(received.length, 4);
});
test('desktop protocol rejects oversized and invalid headers before allocating the body', () => {
  for (const mutate of [
    bytes => new DataView(bytes.buffer).setUint32(4, MAX_VIDEO_PACKET + 1),
    bytes => { bytes[0] = 99; },
    bytes => { bytes[2] = 1; },
    bytes => new DataView(bytes.buffer).setBigUint64(8, 2n ** 60n),
  ]) {
    const header = videoPacket(4); mutate(header);
    assert.throws(() => new VideoPacketReader(() => assert.fail()).push(header), /Invalid desktop/);
  }
  assert.throws(() => videoPacket(2, new Uint8Array(MAX_VIDEO_PACKET + 1)), /Invalid/);
});
const config = new Uint8Array(15);
new DataView(config.buffer).setUint32(0, 1280);
new DataView(config.buffer).setUint32(4, 720);
config.set([1, 100, 0, 42, 255, 0, 0], 8);
test('desktop codec configuration describes AVC and rejects invalid dimensions', () => {
  assert.equal(videoConfiguration(config).codec, 'avc1.64002a');
  assert.equal(videoConfiguration(config).codedHeight, 720);
  const bad = config.slice(); new DataView(bad.buffer).setUint32(0, 9000);
  assert.throws(() => videoConfiguration(bad), /Unsupported/);
  assert.throws(() => videoConfiguration(config.subarray(0, 8)), /Invalid/);
});

test('desktop decoder paints on output, requests recovery, closes frames and preserves pane visibility', () => {
  const original = Object.getOwnPropertyDescriptors(globalThis);
  const document = new EventTarget(); document.hidden = false;
  const decoders = [];
  class Decoder {
    state = 'unconfigured'; decodeQueueSize = 0; chunks = [];
    constructor(callbacks) { this.callbacks = callbacks; decoders.push(this); }
    configure(value) { this.config = value; this.state = 'configured'; }
    decode(chunk) { this.chunks.push(chunk); }
    close() { assert.notEqual(this.state, 'closed'); this.state = 'closed'; }
  }
  class Socket extends EventTarget {
    readyState = 1; sent = []; closed = 0;
    send(bytes) { this.sent.push(bytes); }
    close() { this.closed++; }
    packet(bytes) { this.dispatchEvent(new MessageEvent('message', { data: bytes.buffer })); }
  }
  Object.assign(globalThis, { document, VideoDecoder: Decoder, EncodedVideoChunk: class { constructor(value) { Object.assign(this, value); } } });
  try {
    const drawn = [], errors = [], socket = new Socket();
    let ready = 0;
    let contextOptions;
    const canvas = { width: 0, height: 0, getContext: (_, options) => { contextOptions = options; return { drawImage: frame => drawn.push(frame) }; } };
    const video = new DesktopVideo(canvas, socket, () => ready++, error => errors.push(error));
    socket.packet(videoPacket(5, Uint8Array.of(0xf4)));
    assert.deepEqual(errors, [], 'optional audio before activation never blocks video');
    const frame = () => ({ closed: 0, displayWidth: 1280, displayHeight: 720, close() { this.closed++; } });
    socket.packet(videoPacket(1, config));
    socket.packet(videoPacket(2, Uint8Array.of(1), 0, 1));
    assert.equal(decoders[0].chunks.length, 0, 'deltas are discarded before the first keyframe');
    socket.packet(videoPacket(2, Uint8Array.of(1), 1, 2));
    assert.equal(decoders[0].chunks.length, 1);
    socket.packet(videoPacket(1, config));
    assert.equal(decoders.length, 1, 'periodic config must not reset the decoder');
    assert.equal(contextOptions.desynchronized, true, 'frames reach the screen without compositor sync');
    const first = frame(), latest = frame();
    decoders[0].callbacks.output(first);
    assert.deepEqual(drawn, [first], 'a decoded frame is painted at once'); assert.equal(first.closed, 1);
    decoders[0].callbacks.output(latest);
    assert.deepEqual(drawn, [first, latest]); assert.equal(latest.closed, 1); assert.equal(ready, 1);
    assert.equal(video.stats.dropped, 1, 'only the initial undecodable delta was dropped');
    const burst = [frame(), frame(), frame()];
    burst.forEach(value => decoders[0].callbacks.output(value));
    assert.deepEqual(drawn.slice(-3), burst, 'batched decoder callbacks are all painted');
    assert.ok(burst.every(value => value.closed === 1));
    assert.deepEqual([canvas.width, canvas.height], [1280, 720]);
    decoders[0].decodeQueueSize = 4;
    socket.packet(videoPacket(2, Uint8Array.of(1), 0, 3));
    assert.equal(decoders[0].state, 'closed'); assert.equal(decoders[1].chunks.length, 0);
    assert.equal(socket.sent.at(-1)[0], 4, 'backlog requests one keyframe');
    socket.packet(videoPacket(2, Uint8Array.of(1), 1, 4));
    assert.equal(decoders[1].chunks.length, 1);
    video.setVisible(false);
    const painted = drawn.length;
    const hidden = [frame(), frame()]; hidden.forEach(value => decoders[1].callbacks.output(value));
    assert.ok(hidden.every(value => value.closed === 1)); assert.equal(drawn.length, painted, 'hidden panes paint nothing');
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange'));
    socket.packet(videoPacket(2, Uint8Array.of(1), 0, 5));
    assert.equal(decoders[1].chunks.length, 1, 'window visibility cannot re-enable a hidden pane');
    video.input(2, [{ type: 1, code: 42, value: 0 }]);
    assert.equal(socket.sent.at(-1)[0], 3, 'hidden panes can release held keys');
    video.setVisible(true);
    assert.equal(socket.sent.at(-1)[0], 4);
    const pending = [frame(), frame()]; pending.forEach(value => decoders.at(-1).callbacks.output(value));
    video.close(); video.close();
    assert.ok(pending.every(value => value.closed === 1)); assert.equal(socket.closed, 1);
    assert.deepEqual(errors, []);
  } finally {
    for (const name of ['document', 'VideoDecoder', 'EncodedVideoChunk']) {
      if (original[name]) Object.defineProperty(globalThis, name, original[name]); else delete globalThis[name];
    }
  }
});
