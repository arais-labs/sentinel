import { WTerm } from '@wterm/dom';
import { GhosttyCore } from '@wterm/ghostty';
import wasmPath from '@wterm/ghostty/ghostty-vt.wasm?url';
import type { PaneTerminal } from './terminal-view';
import { terminalTheme, type TerminalTheme } from './terminal-theme';
import '@wterm/dom/src/terminal.css';
import './terminal.css';

export const loadTerminalCore = () => GhosttyCore.load({ wasmPath, scrollbackLimit: 64 * 1024 * 1024 });

// wterm currently has no visibility API. Keep these version-specific hooks in
// this adapter so hidden DOM geometry cannot change its scrollback anchor.
type ViewInternals = {
  _isScrolledToBottom(): boolean;
  _doRender(): void;
  _shouldScrollToBottom: boolean;
  _pendingResizeScrollTop: number | null;
};

/** Ghostty parses synchronously; wterm coalesces painting into animation frames. */
export class TerminalRenderer implements PaneTerminal {
  readonly view: WTerm;
  private visible = true;
  private savedTop = 0;
  private pinned = true;
  private readonly onScroll = (event: Event) => {
    if (!this.visible || !this.element.clientHeight) {
      event.stopImmediatePropagation();
      return;
    }
    this.savedTop = this.element.scrollTop;
    this.pinned = this.element.scrollHeight - this.savedTop - this.element.clientHeight < 5;
  };
  private theme: TerminalTheme = terminalTheme();
  constructor(readonly element: HTMLElement, readonly core: GhosttyCore, cols: number, rows: number,
    private readonly onInput: (data: string) => void) {
    element.addEventListener('scroll', this.onScroll, true);
    this.view = new WTerm(element, { core, cols, rows, autoResize: false, cursorBlink: true, onData: onInput });
    const internals = this.view as unknown as ViewInternals;
    const atBottom = internals._isScrolledToBottom.bind(this.view);
    const render = internals._doRender.bind(this.view);
    internals._isScrolledToBottom = () => this.visible && element.clientHeight ? atBottom() : this.pinned;
    internals._doRender = () => { if (this.visible && element.clientHeight) render(); };
  }
  setVisible(visible: boolean) {
    if (visible === this.visible) return;
    this.visible = visible;
    if (!visible) return;
    const internals = this.view as unknown as ViewInternals;
    internals._shouldScrollToBottom = this.pinned;
    internals._pendingResizeScrollTop = this.pinned ? null : this.savedTop;
    internals._doRender();
  }
  async init() { await this.view.init(); this.setTheme(this.theme); }
  setTheme(theme: TerminalTheme) {
    this.theme = theme;
    this.element.style.setProperty('--term-fg', theme.foreground);
    this.element.style.setProperty('--term-bg', theme.background);
    this.element.style.setProperty('--term-cursor', theme.cursor);
    this.element.style.setProperty('--terminal-selection', theme.selection);
    // Set Ghostty's palette as well as CSS, including colors reported to programs.
    const palette = theme.palette.map((color, index) => `${index};${color}`).join(';');
    this.view.write(`\x1b]4;${palette}\x1b\\\x1b]10;${theme.foreground}\x1b\\\x1b]11;${theme.background}\x1b\\`);
  }
  write(data: string | Uint8Array, callback: () => void) {
    this.view.write(data);
    callback();
  }
  resize(cols: number, rows: number) {
    if (cols !== this.view.cols || rows !== this.view.rows) this.view.resize(cols, rows);
  }
  reset() {
    this.view.write('\x1bc\x1b[3J\x1b[2J\x1b[H');
    this.setTheme(this.theme);
    this.element.scrollTop = 0;
  }
  focus() { this.view.focus(); }
  paste(text: string) {
    const normalized = text.replace(/\r?\n/g, '\r');
    this.onInput(this.core.bracketedPaste()
      ? `\x1b[200~${normalized.replace(/\x1b/g, '')}\x1b[201~` : normalized);
    this.element.scrollTop = this.element.scrollHeight;
  }
  dispose() { this.element.removeEventListener('scroll', this.onScroll, true); this.view.destroy(); this.core.dispose(); }
}
