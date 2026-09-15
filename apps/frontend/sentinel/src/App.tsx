import { applyChatAppearance } from './components/ChatAppearanceControls';
import { InstancePickerPage } from './pages/InstancePickerPage';
import { ProductTourHost } from './components/onboarding/ProductTourHost';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { NotificationHost } from './components/NotificationHost';

import { FormWindow } from './components/session/FormWindow';
import { DesktopShell } from './components/DesktopShell';
import { AppShell } from './components/AppShell';
import { Workspace } from './components/workspace/Workspace';
import { Logo } from './components/ui/Logo';
import { AuditPage } from './pages/AuditPage';
import { OnboardingHost } from './components/onboarding/OnboardingHost';
import { UiShowcasePage } from './pages/UiShowcasePage';
import { openWorkspaceTab } from './lib/workspace-navigation';
import { WORKSPACE_TAB_IDS, type WorkspaceTabId } from './lib/workspace-tabs';
import { useThemeStore } from './store/theme-store';
import { useWorkspaceStore } from './store/workspace-store';
import { api } from './lib/api';

function FullPageLoader() {
  return (
    <div className="app-loading-screen min-h-full w-full flex flex-col items-center justify-center bg-(--app-bg) gap-4" role="status" aria-label="Loading Sentinel">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-(--accent-solid) text-(--app-bg) shadow-lg shadow-black/10">
        <Logo size={24} />
      </div>
      <Loader2 className="animate-spin text-(--text-muted)" size={20} />
    </div>
  );
}

function WorkspaceOutlet() {
  const location = useLocation();
  const navigate = useNavigate();
  const [onboardingChecked, setOnboardingChecked] = useState(false);

  useEffect(() => {
    const instanceMatch = location.pathname.match(/^\/instances\/([^/]+)/);
    if (!instanceMatch?.[1]) {
      setOnboardingChecked(true);
      return;
    }
    const instancePrefix = instanceMatch?.[1] ? `/instances/${instanceMatch[1]}` : '';
    const onboardingPath = instancePrefix ? `${instancePrefix}/onboarding` : '/onboarding';
    if (location.pathname === onboardingPath) {
      setOnboardingChecked(true);
      return;
    }
    api.get<{ completed: boolean }>('/onboarding/status')
      .then(res => {
        if (!res.completed) {
          navigate(onboardingPath, { replace: true });
        }
      })
      .catch(() => {/* on error, don't block the app */})
      .finally(() => setOnboardingChecked(true));
  }, [location.pathname, navigate]);

  if (!onboardingChecked) return <FullPageLoader />;

  return <Outlet />;
}

/** Deep links select a pane; they never render a second standalone interface. */
function WorkspaceTabRedirect({ tabId }: { tabId: WorkspaceTabId }) {
  const { instanceName, id } = useParams<{ instanceName: string; id?: string }>();
  const navigate = useNavigate();
  useEffect(() => {
    if (instanceName) openWorkspaceTab(navigate, instanceName, tabId, { sessionId: tabId === 'sessions' ? id : undefined, replace: true });
  }, [instanceName, id, tabId, navigate]);
  return null;
}

/**
 * Instance workspace: the tiling container fills the AppShell content area and
 * the left nav acts as a tab launcher (see AppShell launcher mode). The header
 * is hidden so dockview owns the full content region; the persisted layout is
 * rehydrated by <Workspace/> itself on mount.
 */
function WorkspaceRoute() {
  const { instanceName } = useParams<{ instanceName?: string }>();
  const location = useLocation();
  const openSessions = Boolean((location.state as { openSessions?: boolean } | null)?.openSessions);

  // Onboarding can explicitly open Sessions; ordinary mounts restore the layout.
  useEffect(() => {
    if (!openSessions) return;

    let cancelled = false;
    let frame = 0;
    const trySeed = () => {
      if (cancelled) return;
      const current = useWorkspaceStore.getState();
      const paneId = current.openTab('sessions');
      if (paneId === null) {
        // No api bound yet; try again next frame (cap to avoid a runaway loop).
        if (frame < 60) {
          frame += 1;
          requestAnimationFrame(trySeed);
        }
      }
    };
    requestAnimationFrame(trySeed);
    return () => {
      cancelled = true;
    };
  }, [instanceName, openSessions]);

  return (
    <AppShell
      title="Instance"
      subtitle={instanceName}
      hideHeader
      contentClassName="h-full p-0! overflow-hidden"
    >
      <Workspace key={instanceName} instanceName={instanceName} />
    </AppShell>
  );
}

function ApplicationRoutes() {
  const initializeTheme = useThemeStore((state) => state.initializeTheme);

  useEffect(() => {
    initializeTheme();
    applyChatAppearance();
  }, [initializeTheme]);

  return (
    <>
      <Routes>
        <Route path="/desktop/*" element={<main className="desktop-instance-home"><InstancePickerPage /></main>} />
        <Route element={<WorkspaceOutlet />}>
          <Route path="/" element={<Navigate to="/desktop/instances" replace />} />
          <Route path="/instances/:instanceName" element={<Navigate to="workspace" replace />} />
          <Route path="/instances/:instanceName/workspace" element={<WorkspaceRoute />} />
          {WORKSPACE_TAB_IDS.map(tabId => (
            <Route key={tabId} path={`/instances/:instanceName/${tabId}`} element={<WorkspaceTabRedirect tabId={tabId} />} />
          ))}
          <Route path="/instances/:instanceName/sessions/:id" element={<WorkspaceTabRedirect tabId="sessions" />} />
          <Route path="/instances/:instanceName/onboarding" element={<OnboardingHost />} />
          <Route path="/instances/:instanceName/triggers/:id" element={<WorkspaceTabRedirect tabId="triggers" />} />
          <Route path="/instances/:instanceName/showcase" element={<UiShowcasePage />} />
          <Route path="/instances/:instanceName/settings/activity" element={<AuditPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ProductTourHost />
    </>
  );
}

export default function App() {
  const location = useLocation();
  if (location.pathname === '/form') return <FormWindow />;
  return <><NotificationHost /><DesktopShell><ApplicationRoutes /></DesktopShell></>;
}
