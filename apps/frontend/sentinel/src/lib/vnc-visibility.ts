import RFB from '@novnc/novnc';

type Client = RFB & {
  _sock: unknown; _fbWidth: number; _fbHeight: number;
  _supportsContinuousUpdates: boolean; _enabledContinuousUpdates: boolean;
  _rfbConnectionState: string;
  _framebufferUpdate(): boolean;
  _updateContinuousUpdates(): void;
};
const messages = (RFB as typeof RFB & { messages: {
  enableContinuousUpdates(socket: unknown, enabled: boolean, x: number, y: number, w: number, h: number): void;
  fbUpdateRequest(socket: unknown, incremental: boolean, x: number, y: number, w: number, h: number): void;
} }).messages;

// Keep the framebuffer and socket, but stop asking the server for hidden frames.
export function controlVncVisibility(rfb: RFB) {
  const client = rfb as Client;
  const frame = client._framebufferUpdate;
  const continuous = client._updateContinuousUpdates;
  let visible = true;
  client._framebufferUpdate = function () {
    const complete = frame.call(this);
    // noVNC sends its next pull immediately after this returns. Suppress it
    // while hidden; resume explicitly requests a complete framebuffer.
    if (!visible) this._enabledContinuousUpdates = true;
    return complete;
  };
  client._updateContinuousUpdates = function () { if (visible) continuous.call(this); };
  return {
    setVisible(value: boolean) {
      if (visible === value) return;
      visible = value;
      if (client._rfbConnectionState !== 'connected') return;
      if (client._supportsContinuousUpdates) {
        messages.enableContinuousUpdates(client._sock, value, 0, 0, client._fbWidth, client._fbHeight);
      }
      client._enabledContinuousUpdates = !value || client._supportsContinuousUpdates;
      if (value) messages.fbUpdateRequest(client._sock, false, 0, 0, client._fbWidth, client._fbHeight);
    },
    dispose() { client._framebufferUpdate = frame; client._updateContinuousUpdates = continuous; },
  };
}
