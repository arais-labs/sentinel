import ReactDOM from 'react-dom/client';
import { DesktopVideoView } from './DesktopVideoView';
import type { DesktopVideo } from '../../src/lib/desktop-video';

const endpoint = new URLSearchParams(location.search).get('stream');
if (!endpoint || !endpoint.startsWith('ws://127.0.0.1:')) throw new Error('A loopback test stream is required');
const connect = () => new WebSocket(endpoint);
const ready = (video: DesktopVideo) => {
  Object.assign(window, { desktopVideo: video });
};
ReactDOM.createRoot(document.getElementById('root')!).render(<DesktopVideoView connect={connect} onConnected={ready} />);
