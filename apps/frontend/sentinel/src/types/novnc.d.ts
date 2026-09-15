declare module '@novnc/novnc' {
  export interface RFBCredentials {
    password?: string;
  }

  export interface RFBOptions {
    credentials?: RFBCredentials;
  }

  export default class RFB extends EventTarget {
    scaleViewport: boolean;
    resizeSession: boolean;
    viewOnly: boolean;
    focusOnClick: boolean;
    background: string;

    constructor(target: HTMLElement, url: string | WebSocket, options?: RFBOptions);
    disconnect(): void;
    focus(): void;
    clipboardPasteFrom(text: string): void;
    sendKey(keysym: number, code: string, down?: boolean): void;
  }
}
