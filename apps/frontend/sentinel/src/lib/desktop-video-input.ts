import type { DesktopVideo } from './desktop-video';

// DOM physical keys → Linux evdev codes, independent of the host keyboard layout.
const keys: Record<string, number> = {
  Escape: 1, Digit1: 2, Digit2: 3, Digit3: 4, Digit4: 5, Digit5: 6, Digit6: 7, Digit7: 8, Digit8: 9, Digit9: 10, Digit0: 11,
  Minus: 12, Equal: 13, Backspace: 14, Tab: 15, KeyQ: 16, KeyW: 17, KeyE: 18, KeyR: 19, KeyT: 20, KeyY: 21, KeyU: 22,
  KeyI: 23, KeyO: 24, KeyP: 25, BracketLeft: 26, BracketRight: 27, Enter: 28, ControlLeft: 29,
  KeyA: 30, KeyS: 31, KeyD: 32, KeyF: 33, KeyG: 34, KeyH: 35, KeyJ: 36, KeyK: 37, KeyL: 38,
  Semicolon: 39, Quote: 40, Backquote: 41, ShiftLeft: 42, Backslash: 43, KeyZ: 44, KeyX: 45, KeyC: 46, KeyV: 47,
  KeyB: 48, KeyN: 49, KeyM: 50, Comma: 51, Period: 52, Slash: 53, ShiftRight: 54, NumpadMultiply: 55,
  AltLeft: 56, Space: 57, CapsLock: 58, F1: 59, F2: 60, F3: 61, F4: 62, F5: 63, F6: 64, F7: 65, F8: 66,
  F9: 67, F10: 68, NumLock: 69, ScrollLock: 70, Numpad7: 71, Numpad8: 72, Numpad9: 73, NumpadSubtract: 74,
  Numpad4: 75, Numpad5: 76, Numpad6: 77, NumpadAdd: 78, Numpad1: 79, Numpad2: 80, Numpad3: 81, Numpad0: 82,
  NumpadDecimal: 83, IntlBackslash: 86, F11: 87, F12: 88, NumpadEnter: 96, ControlRight: 97, NumpadDivide: 98,
  PrintScreen: 99, AltRight: 100, Home: 102, ArrowUp: 103, PageUp: 104, ArrowLeft: 105, ArrowRight: 106,
  End: 107, ArrowDown: 108, PageDown: 109, Insert: 110, Delete: 111, Pause: 119, MetaLeft: 125, MetaRight: 126, ContextMenu: 127,
};
const sync = { type: 0, code: 0, value: 0 };

export function attachDesktopInput(canvas: HTMLCanvasElement, video: DesktopVideo) {
  const abort = new AbortController();
  const pressed = new Set<number>(), buttons = new Set<number>();
  const options = { signal: abort.signal };
  let motion: PointerEvent | undefined, animation = 0;
  let pointer: PointerEvent | undefined;
  const sendKey = (device: number, code: number, value: number) => video.input(device, [{ type: 1, code, value }, sync]);
  const release = () => {
    for (const code of pressed) sendKey(2, code, 0);
    for (const code of buttons) sendKey(1, code, 0);
    pressed.clear(); buttons.clear();
  };
  const coordinates = (event: PointerEvent) => {
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width / canvas.width, rect.height / canvas.height);
    const width = canvas.width * scale, height = canvas.height * scale;
    if (!width || !height) return;
    const x = (event.clientX - rect.left - (rect.width - width) / 2) / width;
    const y = (event.clientY - rect.top - (rect.height - height) / 2) / height;
    return { x, y };
  };
  const updateCursor = () => {
    const point = pointer && coordinates(pointer);
    // object-contain leaves margins inside the canvas element. Only the
    // streamed desktop pixels contain a guest cursor; keep the host elsewhere.
    canvas.toggleAttribute('data-guest-pointer', !!point && point.x >= 0 && point.x < 1 && point.y >= 0 && point.y < 1);
  };
  const forgetPointer = () => { pointer = undefined; updateCursor(); };
  for (const type of ['pointerenter', 'pointermove'] as const) canvas.addEventListener(type, event => {
    pointer = event; updateCursor();
  }, options);
  canvas.addEventListener('pointerleave', forgetPointer, options);
  canvas.addEventListener('pointercancel', forgetPointer, options);
  const resize = new ResizeObserver(updateCursor);
  resize.observe(canvas);
  const positionEvents = (event: PointerEvent) => {
    const point = coordinates(event);
    if (!point) return;
    const { x, y } = point;
    return [{ type: 3, code: 0, value: Math.round(Math.max(0, Math.min(1, x)) * 65535) },
      { type: 3, code: 1, value: Math.round(Math.max(0, Math.min(1, y)) * 65535) }];
  };
  const position = (event: PointerEvent) => {
    const events = positionEvents(event);
    if (events) video.input(1, [...events, sync]);
  };
  const flush = () => { animation = 0; if (motion) position(motion); motion = undefined; };
  canvas.addEventListener('pointermove', event => {
    motion = event;
    if (!animation) animation = requestAnimationFrame(flush);
  }, options);
  for (const type of ['pointerdown', 'pointerup'] as const) canvas.addEventListener(type, event => {
    const code = [272, 274, 273, 275, 276][event.button];
    if (!code) return;
    event.preventDefault(); canvas.focus({ preventScroll: true });
    if (animation) cancelAnimationFrame(animation);
    animation = 0;
    motion = undefined;
    const down = type === 'pointerdown';
    if (down && event.isTrusted) video.enableAudio();
    if (down) { canvas.setPointerCapture(event.pointerId); buttons.add(code); } else buttons.delete(code);
    // Position and button belong to one input report. Separate reports can
    // click at the compositor's previous position during motion/coalescing.
    video.input(1, [...(positionEvents(event) ?? []), { type: 1, code, value: down ? 1 : 0 }, sync]);
  }, options);
  canvas.addEventListener('pointercancel', release, options);
  canvas.addEventListener('contextmenu', event => event.preventDefault(), options);
  canvas.addEventListener('wheel', event => {
    event.preventDefault();
    const events = [];
    if (event.deltaY) events.push({ type: 2, code: 8, value: -Math.sign(event.deltaY) });
    if (event.deltaX) events.push({ type: 2, code: 6, value: Math.sign(event.deltaX) });
    if (events.length) video.input(0, [...events, sync]);
  }, { ...options, passive: false });
  for (const type of ['keydown', 'keyup'] as const) canvas.addEventListener(type, event => {
    const code = keys[event.code];
    if (!code) return;
    event.preventDefault(); event.stopPropagation();
    if (type === 'keydown' && event.isTrusted) video.enableAudio();
    if (type === 'keydown') pressed.add(code); else pressed.delete(code);
    sendKey(2, code, type === 'keyup' ? 0 : event.repeat ? 2 : 1);
  }, options);
  canvas.addEventListener('blur', release, options);
  window.addEventListener('blur', release, options);
  document.addEventListener('visibilitychange', release, options);
  return Object.assign(() => { release(); abort.abort(); resize.disconnect(); forgetPointer(); if (animation) cancelAnimationFrame(animation); }, {
    release,
    shortcut(key: 'c' | 'x' | 'v', shift = false) {
      release();
      sendKey(2, 29, 1);
      if (shift) sendKey(2, 42, 1);
      sendKey(2, keys[`Key${key.toUpperCase()}`], 1);
      sendKey(2, keys[`Key${key.toUpperCase()}`], 0);
      if (shift) sendKey(2, 42, 0);
      sendKey(2, 29, 0);
    },
  });
}
