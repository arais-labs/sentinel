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

    constructor(target: HTMLElement, url: string, options?: RFBOptions);
    disconnect(): void;
    focus(): void;
  }
}
