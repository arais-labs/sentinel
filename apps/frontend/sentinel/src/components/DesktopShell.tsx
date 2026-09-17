import { useEffect, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Settings2 } from 'lucide-react';
import { NotificationControls } from './NotificationControls';
import { GlobalSessionSelector } from './session/GlobalSessionSelector';
import { TopBarVoice } from './TopBarVoice';
import { InstancePickerPage } from '../pages/InstancePickerPage';
import { DesktopManagement } from './DesktopManagement';
import { WorkspacePreparation } from './WorkspacePreparation';
import { shouldShowDesktopPreparation } from './desktop-preparation-state';
import { useProductTourMenu } from './onboarding/useProductTourMenu';
import { useThemeStore } from '../store/theme-store';
import type { DesktopStatus } from '../../../../desktop/sentinel/src/shared/ipc';

export function DesktopShell({ children }: { children: ReactNode }) {
  const api = window.sentinelDesktop;
  const navigate = useNavigate();
  const location = useLocation();
  const [status, setStatus] = useState<DesktopStatus>();
  const [startupError, setStartupError] = useState('');
  const [instancePath, setInstancePath] = useState<string>();
  const initializeTheme = useThemeStore(s => s.initializeTheme);
  useEffect(() => { initializeTheme(); }, [initializeTheme]);
  useEffect(() => { if (location.pathname.startsWith('/instances/')) setInstancePath(location.pathname); }, [location.pathname]);
  useProductTourMenu(status?.ready, instancePath);
  useEffect(() => {
    if (!api) return;
    let active = true;
    const off = [api.onStatus(setStatus), api.onNavigate(navigate), api.onPayloadInstalled(() => window.location.reload())];
    void api.getStatus().then(value => { if (active) setStatus(value); }).catch(error => { if (active) setStartupError(String(error)); });
    return () => { active = false; off.forEach(fn => fn()); };
  }, [api, navigate]);
  const instance = location.pathname.match(/^\/instances\/([^/]+)/)?.[1];
  const onboarding = location.pathname.includes('/onboarding');
  if (!api) return instance && !onboarding ? <div className="desktop-frame"><div className="desktop-titlebar">
    <TopBarVoice key={instance} instanceName={decodeURIComponent(instance)} />
  </div><div className="desktop-content">{children}</div></div> : <>{children}</>;
  if (!status || shouldShowDesktopPreparation(status)) {
    return <WorkspacePreparation preparing={Boolean(status && !status.development && (!status.payload.installed || (status.payloadProgress && status.payloadProgress.phase !== 'done')))}
      progress={status?.payloadProgress} error={startupError || status?.error}
      onRetry={() => { setStartupError(''); void api.startServices().then(setStatus).catch(error => setStartupError(String(error))); }} />;
  }
  const home = location.pathname === '/' || location.pathname.startsWith('/desktop');
  const settingsInstance = instance ?? instancePath?.match(/^\/instances\/([^/]+)/)?.[1];
  const showRecovery = !status.ready || (home && !['/', '/desktop', '/desktop/instances'].includes(location.pathname));
  return <div className="desktop-frame"><div className="desktop-titlebar">
    {status.ready && instance && !onboarding && <GlobalSessionSelector key={instance} instanceName={decodeURIComponent(instance)} />}
    {status.ready && instance && !onboarding && <TopBarVoice key={`voice-${instance}`} instanceName={decodeURIComponent(instance)} />}
    <div className="desktop-titlebar-actions">{status.ready && <NotificationControls />}<button type="button" className="desktop-settings-toggle" aria-label="Settings" title="Settings" disabled={!settingsInstance} onClick={() => navigate(`/instances/${settingsInstance}/settings`)}><Settings2 size={15} /></button></div>
  </div><div className="desktop-content">
    {showRecovery ? <main className="desktop-home-services settings-pane"><button className="btn-secondary" onClick={() => navigate('/desktop/instances')} disabled={!status.ready}>INSTANCES</button><DesktopManagement sectionId={location.pathname.endsWith('/updates') ? 'updates' : 'services'} /></main> : home ? <main className="desktop-instance-home"><InstancePickerPage /></main> : children}
  </div></div>;
}
