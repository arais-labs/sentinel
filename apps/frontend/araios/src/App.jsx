import { useCallback, useEffect, useRef, useState } from 'react';
import { checkSession, clearToken, api } from './lib/api';
import Toast from './components/Toast';
import { AppShell } from './components/AppShell';
import { IconRefresh, IconSettings } from './components/Icons';
import ModulePage from './components/ModulePage';
import SettingsModal from './components/SettingsModal';
import AuthGate from './components/AuthGate';

// System modules with custom pages
import TasksPage from './pages/TasksPage';
import ApprovalsPage from './pages/ApprovalsPage';
import PermissionsPage from './pages/PermissionsPage';
import CoordinationPage from './pages/CoordinationPage';
import DocumentsPage from './pages/DocumentsPage';

// System modules that always appear in nav (fixed order)
const SYSTEM_MODULES = [
  { id: 'tasks',        label: 'Tasks',        icon: 'GitBranch',    isSystem: true },
  { id: 'approvals',    label: 'Approvals',    icon: 'CheckCircle',  isSystem: true },
  { id: 'permissions',  label: 'Permissions',  icon: 'Lock',         isSystem: true },
  { id: 'coordination', label: 'Coordination', icon: 'MessageCircle',isSystem: true },
  { id: 'documents',    label: 'Documents',    icon: 'FileCode',     isSystem: true },
];

const LS_KEY = 'araios_active_module';

function App() {
  const [authed, setAuthed] = useState(false);
  const [activeModule, setActiveModule] = useState(() => localStorage.getItem(LS_KEY) || null);
  const [toast, setToast] = useState(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [dynamicModules, setDynamicModules] = useState([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const refreshRef = useRef(null);

  const notify = useCallback((message, type = 'ok', action = null) => {
    setToast({ message, type, id: Date.now(), action });
  }, []);

  const dismissToast = useCallback(() => setToast(null), []);

  const handleModuleChange = useCallback((mod) => {
    refreshRef.current = null;
    setActiveModule(mod);
    localStorage.setItem(LS_KEY, mod);
  }, []);

  const setRefresh = useCallback((fn) => {
    refreshRef.current = fn;
  }, []);

  const handleGlobalRefresh = useCallback(() => {
    if (refreshRef.current) refreshRef.current();
  }, []);

  // Load dynamic modules from API
  const loadModules = useCallback(async () => {
    try {
      const data = await api('/api/modules');
      const mods = (data.modules || []).map(m => ({
        id: m.name,
        label: m.label,
        icon: m.icon,
        type: m.type,
        order: m.order,
        isSystem: false,
      }));
      setDynamicModules(mods);
      setActiveModule(prev => {
        const valid = [...mods.map(m => m.id), ...SYSTEM_MODULES.map(m => m.id)];
        if (prev && valid.includes(prev)) return prev;
        const saved = localStorage.getItem(LS_KEY);
        if (saved && valid.includes(saved)) return saved;
        return mods[0]?.id || 'tasks';
      });
    } catch {
      setActiveModule(prev => prev || 'tasks');
    }
  }, []);

  useEffect(() => {
    checkSession().then(setAuthed).catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    if (!authed) return;
    loadModules();
  }, [authed, loadModules]);

  // Poll pending approvals count
  useEffect(() => {
    if (!authed) return;
    const loadCount = async () => {
      try {
        const data = await api('/api/approvals?status=pending');
        setPendingCount((data.approvals || []).length);
      } catch { /* ignore */ }
    };
    loadCount();
    const timer = setInterval(loadCount, 60000);
    return () => clearInterval(timer);
  }, [authed]);

  if (!authed) {
    return <AuthGate onAuth={() => setAuthed(true)} />;
  }

  const handleLogout = async () => {
    try {
      await fetch('/platform/auth/session', {
        method: 'DELETE',
        credentials: 'include',
      });
    } catch {
      // no-op
    }
    clearToken();
    window.location.assign('/');
  };

  const pageProps = { notify, setRefresh };

  const renderPage = () => {
    // System modules with custom pages
    switch (activeModule) {
      case 'tasks':        return <TasksPage {...pageProps} />;
      case 'approvals':    return <ApprovalsPage {...pageProps} onApprovalResolved={loadModules} />;
      case 'permissions':  return <PermissionsPage {...pageProps} />;
      case 'coordination': return <CoordinationPage {...pageProps} />;
      case 'documents':    return <DocumentsPage {...pageProps} />;
    }
    // Dynamic modules — any module registered in the engine
    if (!activeModule) return null;
    return <ModulePage key={activeModule} moduleName={activeModule} {...pageProps} />;
  };

  // Determine active module label + type for header
  const allModules = [...dynamicModules, ...SYSTEM_MODULES];
  const activeMod = allModules.find(m => m.id === activeModule);
  const activeLabel = activeMod?.label || activeModule;
  const activeType = activeMod?.type || (activeMod?.isSystem ? 'system' : null);

  const TYPE_BADGE = {
    data:   { label: 'data',   cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30' },
    page:   { label: 'page',   cls: 'bg-purple-500/15 text-purple-400 border-purple-500/30' },
    tool:   { label: 'tool',   cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
    system: { label: 'system', cls: 'bg-slate-500/15 text-slate-400 border-slate-500/30' },
  };

  return (
    <>
      <AppShell
        title={activeLabel}
        subtitle="Operator Control Plane"
        activeModule={activeModule}
        onModuleChange={handleModuleChange}
        onLogout={handleLogout}
        pendingCount={pendingCount}
        dynamicModules={dynamicModules}
        systemModules={SYSTEM_MODULES}
        actions={
          <>
            {activeType && TYPE_BADGE[activeType] && (
              <span className={`text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded border ${TYPE_BADGE[activeType].cls}`}>
                {TYPE_BADGE[activeType].label}
              </span>
            )}
          <button
            className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors rounded-md hover:bg-[color:var(--surface-2)]"
            onClick={handleGlobalRefresh}
            title="Refresh"
          >
            <IconRefresh size={16} />
          </button>
          <button
            className="p-2 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors rounded-md hover:bg-[color:var(--surface-2)]"
            onClick={() => setSettingsOpen(true)}
            title="Settings"
          >
            <IconSettings size={16} />
          </button>
          </>
        }
      >
        <div className="animate-in h-full overflow-hidden">
          {renderPage()}
        </div>
      </AppShell>

      <Toast toast={toast} onDismiss={dismissToast} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} notify={notify} />
    </>
  );
}

export default App;
