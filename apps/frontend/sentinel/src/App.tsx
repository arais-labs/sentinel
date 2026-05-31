import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import { Toaster } from 'sonner';

import { AppShell } from './components/AppShell';
import { Workspace } from './components/workspace/Workspace';
import { Logo } from './components/ui/Logo';
import { AdminPage } from './pages/AdminPage';
import { LogsPage } from './pages/LogsPage';
import { LoginPage } from './pages/LoginPage';
import { MemoryPage } from './pages/MemoryPage';
import { OnboardingPage } from './pages/OnboardingPage';
import { TelegramPage } from './pages/TelegramPage';
import { UiShowcasePage } from './pages/UiShowcasePage';
import { InstancePickerPage } from './pages/InstancePickerPage';
import { SessionsPage } from './pages/SessionsPage';
import { SettingsPage } from './pages/SettingsPage';
import { GitPage } from './pages/GitPage';
import { TriggersPage } from './pages/TriggersPage';
import { ModulesPage } from './pages/ModulesPage';
import { instanceRouteFromPath } from './lib/routes';
import { useAuthStore } from './store/auth-store';
import { useThemeStore } from './store/theme-store';
import { useWorkspaceStore } from './store/workspace-store';
import { api } from './lib/api';

function FullPageLoader() {
  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center bg-[color:var(--app-bg)] gap-4">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[color:var(--accent-solid)] text-[color:var(--app-bg)] shadow-lg shadow-black/10">
        <Logo size={24} />
      </div>
      <Loader2 className="animate-spin text-[color:var(--text-muted)]" size={20} />
    </div>
  );
}

function AuthenticatedOutlet() {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();
  const navigate = useNavigate();
  const [onboardingChecked, setOnboardingChecked] = useState(false);

  useEffect(() => {
    if (status !== 'authenticated') return;
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
  }, [location.pathname, navigate, status]);

  if (status === 'loading' || (status === 'authenticated' && !onboardingChecked)) {
    return <FullPageLoader />;
  }

  if (status !== 'authenticated') {
    return <Navigate to="/login" replace />;
  }

  return <Outlet />;
}

function PublicLoginRoute() {
  const status = useAuthStore((state) => state.status);

  if (status === 'loading') {
    return <FullPageLoader />;
  }

  if (status === 'authenticated') {
    return <Navigate to="/" replace />;
  }

  return <LoginPage />;
}

function TriggerDetailRedirect() {
  const location = useLocation();
  return <Navigate to={instanceRouteFromPath(location.pathname, 'triggers')} replace />;
}

/**
 * Instance workspace: the tiling container fills the AppShell content area and
 * the left nav acts as a tab launcher (see AppShell launcher mode). The header
 * is hidden so dockview owns the full content region; the persisted layout is
 * rehydrated by <Workspace/> itself on mount.
 */
function WorkspaceRoute() {
  const { instanceName } = useParams<{ instanceName?: string }>();

  // Seed a sensible default layout (a single Sessions pane) the first time the
  // workspace is opened with nothing persisted. <Workspace/> restores any saved
  // layout in its onReady; we only step in when there is none. openTab needs the
  // live DockviewApi to be bound, so retry across frames until it takes.
  useEffect(() => {
    const store = useWorkspaceStore.getState();
    // A persisted layout (even empty) means the user has a saved arrangement —
    // never override it.
    if (store.layout) return;

    let cancelled = false;
    let frame = 0;
    const trySeed = () => {
      if (cancelled) return;
      const current = useWorkspaceStore.getState();
      // The user (or restore) may have created panes between frames; bail if so.
      if (current.layout || Object.keys(current.openTabs).length > 0) return;
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
  }, []);

  return (
    <AppShell
      title="Workspace"
      subtitle={instanceName}
      hideHeader
      contentClassName="h-full !p-0 overflow-hidden"
    >
      <Workspace instanceName={instanceName} />
    </AppShell>
  );
}

export default function App() {
  const initialize = useAuthStore((state) => state.initialize);
  const initializeTheme = useThemeStore((state) => state.initializeTheme);
  const theme = useThemeStore((state) => state.theme);

  useEffect(() => {
    initialize();
    initializeTheme();
  }, [initialize, initializeTheme]);

  return (
    <>
      <Routes>
        <Route path="/login" element={<PublicLoginRoute />} />

        <Route element={<AuthenticatedOutlet />}>
          <Route path="/" element={<InstancePickerPage />} />
          <Route path="/instances/:instanceName" element={<Navigate to="workspace" replace />} />
          <Route path="/instances/:instanceName/workspace" element={<WorkspaceRoute />} />
          <Route path="/instances/:instanceName/sessions" element={<SessionsPage />} />
          <Route path="/instances/:instanceName/sessions/:id" element={<SessionsPage />} />
          <Route path="/instances/:instanceName/onboarding" element={<OnboardingPage />} />
          <Route path="/instances/:instanceName/logs" element={<LogsPage />} />
          <Route path="/instances/:instanceName/memory" element={<MemoryPage />} />
          <Route path="/instances/:instanceName/triggers" element={<TriggersPage />} />
          <Route path="/instances/:instanceName/triggers/:id" element={<TriggerDetailRedirect />} />
          <Route path="/instances/:instanceName/modules" element={<ModulesPage />} />
          <Route path="/instances/:instanceName/approvals" element={<ModulesPage />} />
          <Route path="/instances/:instanceName/permissions" element={<ModulesPage />} />
          <Route path="/instances/:instanceName/git" element={<GitPage />} />
          <Route path="/instances/:instanceName/telegram" element={<TelegramPage />} />
          <Route path="/instances/:instanceName/showcase" element={<UiShowcasePage />} />
          <Route path="/instances/:instanceName/settings" element={<SettingsPage />} />
          <Route path="/instances/:instanceName/settings/admin" element={<AdminPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster richColors closeButton position="top-right" theme={theme === 'dark' ? 'dark' : 'light'} />
    </>
  );
}
