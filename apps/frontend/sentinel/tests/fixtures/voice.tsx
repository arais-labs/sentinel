import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { WorkspaceProvider } from '../../src/lib/workspace-context';
import { RetainedSessionContext } from '../../src/lib/workspace-context-values';
import { VoicePage } from '../../src/pages/VoicePage';
import { TopBarVoice } from '../../src/components/TopBarVoice';
import { VoiceOrbitText } from '../../src/components/VoiceOrbitText';
import { useFocusModeStore } from '../../src/store/focus-mode-store';
import { useActiveSessionStore } from '../../src/store/active-session-store';
import { WORKSPACE_TAB_IDS } from '../../src/lib/workspace-tabs';
import { useVoicePreferences, type VoiceModelSelection } from '../../src/store/voice-preferences-store';
import '../../src/index.css';

function Harness() {
  const [visible, setVisible] = useState(true);
  const [mounted, setMounted] = useState(true);
  const [caption, setCaption] = useState({ label: 'I’m listening', heard: '', reply: '' });
  Object.assign(window, { setVoiceCaption: setCaption });
  Object.assign(window, { hideVoice: () => setVisible(false), closeVoice: () => setMounted(false), setVoiceProvider: (provider: string) => useVoicePreferences.getState().setProvider('test', provider) });
  Object.assign(window, { setVoiceSelection: (selection: VoiceModelSelection) => useVoicePreferences.getState().setSelection('test', selection) });
  if (new URLSearchParams(location.search).has('captions')) {
    return <div className="voice-pane" style={{ width:'min(440px, 100vw)', height:440 }}><div className="voice-stage"><div className="voice-orbit"><VoiceOrbitText {...caption} /></div></div></div>;
  }
  if (new URLSearchParams(location.search).has('overlay')) {
    Object.assign(window, {
      focusAnotherPane: () => useFocusModeStore.setState({ paneId: 'other-pane' }),
      switchChat: () => useActiveSessionStore.getState().setActiveSession('test', '00000000-0000-0000-0000-000000000123'),
      workspaceTabs: WORKSPACE_TAB_IDS,
    });
    return <div className="desktop-frame"><div className="desktop-titlebar"><TopBarVoice instanceName="test" /></div>
      <main><button style={{ margin: 80 }}>Workspace remains interactive</button></main></div>;
  }
  return <WorkspaceProvider instanceName="test"><RetainedSessionContext.Provider value={{ sessionId: null, visible }}>
    {mounted && <div style={{ height: '100vh' }}><VoicePage /></div>}
  </RetainedSessionContext.Provider></WorkspaceProvider>;
}
createRoot(document.getElementById('root')!).render(<MemoryRouter initialEntries={['/instances/test/workspace']}><Harness /></MemoryRouter>);
