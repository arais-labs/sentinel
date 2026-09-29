import { useEffect, useRef, useState } from 'react';
import { DesktopVideo, type VideoSocket } from '../../src/lib/desktop-video';
import { attachDesktopInput } from '../../src/lib/desktop-video-input';

interface Props {
  connect: () => VideoSocket;
  visible?: boolean;
  onConnected?: (video: DesktopVideo) => void;
}

export function DesktopVideoView({ connect, visible = true, onConnected }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const player = useRef<DesktopVideo | null>(null);
  const [status, setStatus] = useState('Connecting…');
  useEffect(() => {
    const socket = connect();
    let detach: (() => void) | undefined;
    try {
      const video = new DesktopVideo(canvas.current!, socket, () => setStatus(''), setStatus);
      player.current = video;
      detach = attachDesktopInput(canvas.current!, video);
      onConnected?.(video);
    } catch (error) { socket.close(); setStatus(error instanceof Error ? error.message : 'Desktop unavailable'); }
    return () => { detach?.(); player.current?.close(); player.current = null; };
  }, [connect, onConnected]);
  useEffect(() => { player.current?.setVisible(visible); }, [visible]);
  return <div style={{ position: 'relative', width: '100%', height: '100%', background: '#111' }}>
    <canvas ref={canvas} tabIndex={0} aria-label="Workspace desktop" style={{ display: 'block', width: '100%', height: '100%', objectFit: 'contain', outline: 'none' }} />
    {status && <div role="status" style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: '#ddd', pointerEvents: 'none' }}>{status}</div>}
  </div>;
}
