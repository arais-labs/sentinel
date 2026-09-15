import type RFB from '@novnc/novnc';

type PointerClient = RFB & {
  _mousePos: { x: number; y: number };
  _mouseButtonMask: number;
  _mouseMoveTimer: ReturnType<typeof setTimeout> | null;
  _mouseLastMoveTime: number;
  _handleMouseMove(x: number, y: number): void;
  _sendMouse(x: number, y: number, mask: number): void;
};

// Browser mouse events already follow the display cadence. noVNC's additional
// 17 ms timer reduces a 120 Hz drag to ~56 Hz. Keep its coordinate conversion,
// button handling and connection/view-only guards, but forward each move now.
export function forwardVncPointer(rfb: RFB): () => void {
  const client = rfb as PointerClient;
  const original = client._handleMouseMove;
  if (client._mouseMoveTimer !== null) {
    clearTimeout(client._mouseMoveTimer);
    client._mouseMoveTimer = null;
  }
  client._handleMouseMove = function (x, y) {
    this._mousePos = { x, y };
    this._mouseLastMoveTime = Date.now();
    this._sendMouse(x, y, this._mouseButtonMask);
  };
  return () => { client._handleMouseMove = original; };
}
