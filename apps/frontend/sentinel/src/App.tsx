import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Toaster } from 'sonner';

import { Logo } from './components/ui/Logo';
import { AdminPage } from './pages/AdminPage';
import { LoginPage } from './pages/LoginPage';
import { MemoryPage } from './pages/MemoryPage';
import { OnboardingPage } from './pages/OnboardingPage';
import { TelegramPage } from './pages/TelegramPage';
import { UiShowcasePage } from './pages/UiShowcasePage';
import { SessionsPage } from './pages/SessionsPage';
import { SettingsPage } from './pages/SettingsPage';
import { ToolsPage } from './pages/ToolsPage';
import { TriggerDetailPage } from './pages/TriggerDetailPage';
import { TriggersPage } from './pages/TriggersPage';
import { useAuthStore } from './store/auth-store';
import { useThemeStore } from './store/theme-store';
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
    // Don't redirect if already on onboarding
    if (location.pathname === '/onboarding') {
      setOnboardingChecked(true);
      return;
    }
    api.get<{ completed: boolean }>('/onboarding/status')
      .then(res => {
        if (!res.completed) {
          navigate('/onboarding', { replace: true });
        }
      })
      .catch(() => {/* on error, don't block the app */})
      .finally(() => setOnboardingChecked(true));
  }, [status]);

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
    return <Navigate to="/sessions" replace />;
  }

  return <ExternalGatewayRedirect />;
}

function ExternalGatewayRedirect() {
  useEffect(() => {
    window.location.assign('/');
  }, []);
  return null;
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
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/triggers" element={<TriggersPage />} />
          <Route path="/triggers/:id" element={<TriggerDetailPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/telegram" element={<TelegramPage />} />
          <Route path="/showcase" element={<UiShowcasePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/settings/admin" element={<AdminPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/sessions" replace />} />
      </Routes>
      <Toaster richColors closeButton position="top-right" theme={theme === 'dark' ? 'dark' : 'light'} />
    </>
  );
}
