export interface PaneTerminal {
  write(data: string | Uint8Array, callback: () => void): void;
  resize(cols: number, rows: number): void;
  reset(): void;
  focus(): void;
}

export interface Pane {
  pane_id: string; window_id: string; title: string; command: string;
  width: number; height: number; left: number; top: number;
  cursor_x: number; cursor_y: number;
  dead: boolean; active: boolean; alternate: boolean; cursor: boolean;
  application_cursor: boolean; bracketed_paste: boolean;
}
export interface TerminalWindow {
  window_id: string; name: string; width: number; height: number; panes: Pane[];
}
export type PaneMessage = { type: 'pane_snapshot'; pane: Pane; data: string } |
  { type: 'pane_output'; pane_id: string; data: string } |
  { type: 'pane_resize'; pane_id: string; cols: number; rows: number };
const decode = (data: string) => Uint8Array.from(atob(data), char => char.charCodeAt(0));

/** Keep geometry changes behind queued writes, so output uses its original size. */
export class PaneWriter {
  private queue = Promise.resolve();
  private disposed = false;
  constructor(readonly terminal: PaneTerminal) {}
  private write(data: string | Uint8Array) {
    return new Promise<void>(resolve => this.terminal.write(data, resolve));
  }
  resize(cols: number, rows: number) {
    this.queue = this.queue.then(() => { if (!this.disposed) this.terminal.resize(cols, rows); });
  }
  push(message: PaneMessage) {
    this.queue = this.queue.then(async () => {
      if (this.disposed) return;
      if (message.type === 'pane_resize') {
        this.terminal.resize(message.cols, message.rows);
      } else if (message.type === 'pane_snapshot') {
        const pane = message.pane;
        this.terminal.reset();
        this.terminal.resize(pane.width, pane.height);
        if (pane.alternate) await this.write('\x1b[?1049h');
        await this.write(decode(message.data));
        await this.write(`\x1b[${pane.cursor_y + 1};${pane.cursor_x + 1}H` +
          `\x1b[?25${pane.cursor ? 'h' : 'l'}` +
          `\x1b[?1${pane.application_cursor ? 'h' : 'l'}` +
          `\x1b[?2004${pane.bracketed_paste ? 'h' : 'l'}`);
      } else await this.write(decode(message.data));
    });
  }
  async drained() { await this.queue; }
  dispose() { this.disposed = true; }
}
