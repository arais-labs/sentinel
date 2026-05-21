import type {
  BootstrapPhase,
  BootstrapProgress,
  DesktopStatus,
  FactoryResetScopes,
  LogEntry,
  ManagedServiceStatus,
  ReleaseChannel,
  RuntimeVersion,
  UpdateAvailable,
  UpdateFailure,
  UpdatePhase,
  UpdateProgress,
} from '../shared/ipc.js';

const api = window.sentinelDesktop;
let logs: LogEntry[] = [];
let logServiceFilter = 'all';
let logServicesKey = '';

const el = <T extends HTMLElement>(id: string): T => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing element #${id}`);
  return node as T;
};

function setPill(id: string, text: string, variant = ''): void {
  const node = el(id);
  node.className = `pill ${variant}`.trim();
  node.textContent = text;
}

function stateDot(text: string, variant: string): string {
  return `<span class="state-dot ${variant}"></span><span class="state-text">${escapeHtml(text)}</span>`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[char]!);
}

function render(status: DesktopStatus): void {
  el('supportPath').textContent = status.appSupportPath;
  renderServices(status.services);
  renderRuntime(status);
}

function renderServices(services: ManagedServiceStatus[]): void {
  const root = el('services');
  if (services.length === 0) {
    root.innerHTML = '<div class="empty-state">No managed services are running.</div>';
    setPill('servicesOverall', 'Offline', 'missing');
    return;
  }
  const hasFailure = services.some((service) => service.state === 'failed');
  const hasStarting = services.some((service) => service.state === 'starting' || service.state === 'stopping');
  setPill('servicesOverall', hasFailure ? 'Degraded' : hasStarting ? 'Starting' : 'Healthy', hasFailure ? 'failed' : hasStarting ? 'starting' : 'ok');
  root.innerHTML = services
    .map(
      (service) => `
        <div class="service-row">
          <div class="service-name">
            <strong>${escapeHtml(formatServiceName(service.name))}</strong>
            <span>${escapeHtml(serviceSubtitle(service))}</span>
          </div>
          <span class="service-state">${stateDot(serviceStateLabel(service.state), stateVariant(service.state))}</span>
          <span class="service-port">${escapeHtml(service.port ? `:${service.port}` : 'local')}</span>
          <span class="service-note">${escapeHtml(service.pid ? `pid ${service.pid}` : service.exitCode != null ? `exit ${service.exitCode}` : '')}</span>
        </div>
      `,
    )
    .join('');
}

function renderRuntime(status: DesktopStatus): void {
  const root = el('runtimeDetails');
  const overallVariant = status.runtime.configured ? 'ok' : 'missing';
  const overallLabel = status.runtime.configured ? 'Configured' : 'Missing';
  setPill('runtimeOverall', overallLabel, overallVariant);
  root.innerHTML = [
    detailRow('Provider', '<span class="mono">ssh</span>'),
    detailRow('Connection', stateDot(overallLabel, overallVariant)),
    detailRow('Host', `<span class="mono">${escapeHtml(formatRuntimeHost(status))}</span>`),
    detailRow('User', `<span class="mono">${escapeHtml(status.runtime.username || '-')}</span>`),
    detailRow('Auth', `<span class="mono">${escapeHtml(status.runtime.authMethod)}</span>`),
    detailRow('Workspaces', `<span class="mono">${escapeHtml(shortPath(status.runtime.workspacesDir))}</span>`),
    detailRow('Phase', `<span class="mono">${escapeHtml(runtimePhase(status))}</span>`),
  ].join('');
}

function formatRuntimeHost(status: DesktopStatus): string {
  if (!status.runtime.host) return '-';
  return `${status.runtime.host}:${status.runtime.port || 22}`;
}

function detailRow(label: string, value: string): string {
  return `
    <div class="detail-row">
      <div class="detail-label">${escapeHtml(label)}</div>
      <div class="detail-value">${value}</div>
    </div>
  `;
}

function serviceDescription(name: ManagedServiceStatus['name']): string {
  switch (name) {
    case 'backend':
      return 'FastAPI application server';
    case 'frontend':
      return 'Packaged Sentinel UI';
    case 'postgres':
      return 'Instance manager and app databases';
  }
}

function serviceSubtitle(service: ManagedServiceStatus): string {
  if (service.state === 'failed' && service.message) {
    return service.message.length > 96 ? `${service.message.slice(0, 93)}...` : service.message;
  }
  return serviceDescription(service.name);
}

function serviceStateLabel(state: ManagedServiceStatus['state']): string {
  return state === 'running' ? 'Running' : state.charAt(0).toUpperCase() + state.slice(1);
}

function stateVariant(state: ManagedServiceStatus['state']): string {
  if (state === 'running') return 'ok';
  if (state === 'starting' || state === 'stopping') return 'starting';
  if (state === 'failed') return 'missing';
  return '';
}

function runtimePhase(status: DesktopStatus): string {
  return status.runtime.message || (status.runtime.configured ? 'SSH runtime configured' : 'SSH runtime not configured');
}

function summarizeLogLine(line: string): string {
  return line.length > 92 ? `${line.slice(0, 89)}...` : line;
}

function shortPath(value: string): string {
  const marker = '/sentinel-desktop/';
  const index = value.indexOf(marker);
  return index >= 0 ? value.slice(index + marker.length) : value;
}

function selectedFactoryResetScopes(): FactoryResetScopes {
  return {
    db: el<HTMLInputElement>('factoryResetDb').checked,
    runtimeData: el<HTMLInputElement>('factoryResetRuntime').checked,
    appRuntime: el<HTMLInputElement>('factoryResetAppRuntime').checked,
    logs: el<HTMLInputElement>('factoryResetLogs').checked,
  };
}

function hasFactoryResetScope(scopes = selectedFactoryResetScopes()): boolean {
  return scopes.db || scopes.runtimeData || scopes.appRuntime || scopes.logs;
}

function updateFactoryResetConfirm(): void {
  el<HTMLButtonElement>('factoryResetConfirmBtn').disabled = !hasFactoryResetScope();
}

function openFactoryResetDialog(): void {
  el<HTMLInputElement>('factoryResetDb').checked = false;
  el<HTMLInputElement>('factoryResetRuntime').checked = false;
  el<HTMLInputElement>('factoryResetAppRuntime').checked = false;
  el<HTMLInputElement>('factoryResetLogs').checked = false;
  el('factoryResetError').hidden = true;
  el('factoryResetModal').hidden = false;
  updateFactoryResetConfirm();
}

function closeFactoryResetDialog(): void {
  el('factoryResetModal').hidden = true;
}

async function refresh(): Promise<void> {
  logs = await api.getLogs();
  render(await api.getStatus());
  renderServiceFilter();
  renderLogs();
}

function formatServiceName(service: string): string {
  return service.charAt(0).toUpperCase() + service.slice(1);
}

function renderServiceFilter(): void {
  const select = el<HTMLSelectElement>('logServiceFilter');
  const services = Array.from(new Set(logs.map((entry) => logCategory(entry)))).sort();
  const servicesKey = services.join('\n');
  const current = services.includes(logServiceFilter) ? logServiceFilter : 'all';
  if (servicesKey !== logServicesKey) {
    select.innerHTML = [
      '<option value="all">All</option>',
      ...services.map((service) => `<option value="${escapeHtml(service)}">${escapeHtml(formatServiceName(service))}</option>`),
    ].join('');
    logServicesKey = servicesKey;
  }
  if (select.value !== current) select.value = current;
  logServiceFilter = current;
}

function renderLogs(): void {
  const visibleLogs = logServiceFilter === 'all' ? logs : logs.filter((entry) => logCategory(entry) === logServiceFilter);
  const output = el('logs');
  output.innerHTML = visibleLogs.map(renderLogEntry).join('');
  output.scrollTop = output.scrollHeight;
}

function renderLogEntry(entry: LogEntry): string {
  const category = logCategory(entry);
  const level = logLevel(entry.line);
  return `<span class="log-line"><span class="log-time">[${escapeHtml(entry.at)}]</span> <span class="log-service ${escapeHtml(category)}">${escapeHtml(category)}</span>: <span class="log-level ${escapeHtml(level.variant)}">${escapeHtml(level.label)}</span> ${escapeHtml(stripLogLevel(entry.line))}</span>`;
}

function logCategory(entry: LogEntry): string {
  return entry.service;
}

function logLevel(line: string): { label: string; variant: string } {
  if (/\b(ERROR|CRITICAL|failed|failure)\b/i.test(line)) return { label: 'ERROR', variant: 'error' };
  if (/\b(WARN|WAIT|waiting)\b/i.test(line)) return { label: 'WAIT', variant: 'warn' };
  const match = line.match(/\b(INFO|LOG|DEBUG)\b/);
  return { label: match?.[1] || 'INFO', variant: 'info' };
}

function stripLogLevel(line: string): string {
  return line.replace(/^\S+\s+(INFO|LOG|DEBUG|WARNING|WARN|ERROR|CRITICAL)\s+/, '').trim();
}

el('openBtn').addEventListener('click', () => void api.openSentinel());
el('revealBtn').addEventListener('click', () => void api.revealAppSupport());
el('openLogFolderBtn').addEventListener('click', () => void api.openLogFolder());
el<HTMLSelectElement>('logServiceFilter').addEventListener('change', (event) => {
  logServiceFilter = (event.target as HTMLSelectElement).value;
  renderLogs();
});
el('resetAuthBtn').addEventListener('click', async () => {
  const confirmed = window.confirm('Reset desktop auth? You will need to create a new admin account on the next login screen.');
  if (!confirmed) return;
  render(await api.resetAuth());
});
el('factoryResetBtn').addEventListener('click', openFactoryResetDialog);
el('factoryResetCloseBtn').addEventListener('click', closeFactoryResetDialog);
el('factoryResetCancelBtn').addEventListener('click', closeFactoryResetDialog);
el('factoryResetModal').addEventListener('click', (event) => {
  if (event.target === event.currentTarget) closeFactoryResetDialog();
});
for (const id of ['factoryResetDb', 'factoryResetRuntime', 'factoryResetAppRuntime', 'factoryResetLogs']) {
  el<HTMLInputElement>(id).addEventListener('change', updateFactoryResetConfirm);
}
el('factoryResetConfirmBtn').addEventListener('click', async () => {
  const scopes = selectedFactoryResetScopes();
  if (!hasFactoryResetScope(scopes)) return;
  const button = el<HTMLButtonElement>('factoryResetConfirmBtn');
  const error = el('factoryResetError');
  button.disabled = true;
  button.textContent = 'Resetting...';
  error.hidden = true;
  try {
    await api.factoryReset(scopes);
  } catch (reason) {
    error.textContent = reason instanceof Error ? reason.message : String(reason);
    error.hidden = false;
    button.textContent = 'Reset Selected and Reboot';
    updateFactoryResetConfirm();
  }
});

let lastBackendState: string | undefined;
api.onStatus((status: DesktopStatus) => {
  render(status);
  const backend = status.services.find((s: ManagedServiceStatus) => s.name === 'backend');
  if (backend && backend.state === 'running' && lastBackendState !== 'running') {
    void refreshVersion();
    hideBootstrapOverlay();
  }
  lastBackendState = backend?.state;
});

// ---- Full-screen lock overlay (bootstrap + updates) ----
const BOOTSTRAP_PHASE_LABELS: Record<BootstrapPhase, string> = {
  'extract-python': 'Installing Python runtime',
  'extract-node': 'Installing Node runtime',
  'extract-source': 'Unpacking Sentinel source',
  'extract-node-modules': 'Restoring frontend packages',
  'uv-sync': 'Setting up Python environment',
  'npm-build': 'Building frontend',
  'done': 'Starting Sentinel',
};
const UPDATE_PHASE_LABELS: Record<UpdatePhase, string> = {
  snapshot: 'Snapshotting state',
  fetch: 'Fetching changes',
  checkout: 'Checking out new version',
  'uv-sync': 'Syncing Python dependencies',
  'npm-ci': 'Installing frontend packages',
  'npm-build': 'Building frontend',
  restart: 'Restarting backend',
  'health-check': 'Verifying backend',
  done: 'Done',
  rollback: 'Rolling back',
  'rollback-checkout': 'Rolling back: checkout',
  'rollback-uv-sync': 'Rolling back: Python dependencies',
  'rollback-npm-build': 'Rolling back: frontend',
  'rollback-restart': 'Rolling back: backend',
  'rollback-failed': 'Rollback failed',
};

let overlayVisible = false;

function showOverlay(title: string): void {
  if (!overlayVisible) {
    overlayVisible = true;
    el('bootstrapOverlay').hidden = false;
  }
  el('bootstrapTitle').textContent = title;
}

function hideOverlay(): void {
  if (!overlayVisible) return;
  overlayVisible = false;
  el('bootstrapOverlay').hidden = true;
}

function setOverlayProgress(phase: string, message: string, fraction?: number): void {
  el('bootstrapPhase').textContent = phase;
  el('bootstrapMessage').textContent = message;
  const fill = el<HTMLDivElement>('bootstrapProgressFill');
  if (typeof fraction === 'number') {
    fill.classList.remove('indeterminate');
    fill.style.width = `${Math.max(0, Math.min(1, fraction)) * 100}%`;
  } else {
    fill.classList.add('indeterminate');
  }
}

// Back-compat alias kept for the onStatus dismiss hook.
const hideBootstrapOverlay = hideOverlay;

api.onBootstrapProgress((progress: BootstrapProgress) => {
  showOverlay('Setting up Sentinel');
  const label = BOOTSTRAP_PHASE_LABELS[progress.phase] ?? progress.phase;
  setOverlayProgress(label, progress.message, progress.phase === 'done' ? undefined : progress.fractionComplete);
});
api.onLog((entry: LogEntry) => {
  logs.push(entry);
  if (logs.length > 2000) logs = logs.slice(-2000);
  void api.getStatus().then(render);
  renderServiceFilter();
  renderLogs();
});

// ---- Version & Updates ----
let pendingUpdate: UpdateAvailable | null = null;
let updateInProgress = false;
let currentChannel: ReleaseChannel | 'dev' = 'dev';

function setUpdateStatusPill(text: string, variant = ''): void {
  setPill('updateStatusPill', text, variant);
}

function renderVersion(version: RuntimeVersion): void {
  const short = version.commit ? version.commit.slice(0, 12) : 'unknown';
  const channel = version.channel || 'dev';
  el('currentVersion').textContent = `${short} · ${channel}`;
  currentChannel = version.channel;
  if (version.channel === 'stable' || version.channel === 'beta') {
    el<HTMLSelectElement>('channelSelect').value = version.channel;
  }
}

function showUpdateBanner(text: string): void {
  el('updateBannerText').textContent = text;
  el('updateBanner').hidden = false;
}

function hideUpdateBanner(): void {
  el('updateBanner').hidden = true;
}

function showUpdateProgress(progress: UpdateProgress): void {
  el('updateProgress').hidden = false;
  el('updateProgressPhase').textContent = progress.phase.toUpperCase();
  el('updateProgressMessage').textContent = progress.message;
}

function hideUpdateProgress(): void {
  el('updateProgress').hidden = true;
}

function setUpdateUiBusy(busy: boolean): void {
  updateInProgress = busy;
  el<HTMLButtonElement>('checkUpdatesBtn').disabled = busy;
  el<HTMLButtonElement>('applyUpdateBtn').disabled = busy;
  el<HTMLSelectElement>('channelSelect').disabled = busy;
}

async function refreshVersion(): Promise<void> {
  try {
    const version = await api.getVersion();
    renderVersion(version);
  } catch {
    el('currentVersion').textContent = 'unavailable';
  }
}

el('checkUpdatesBtn').addEventListener('click', async () => {
  if (updateInProgress) return;
  const channel = el<HTMLSelectElement>('channelSelect').value as ReleaseChannel;
  hideUpdateBanner();
  el<HTMLButtonElement>('applyUpdateBtn').hidden = true;
  pendingUpdate = null;
  setUpdateUiBusy(true);
  setUpdateStatusPill('Checking…', 'pending');
  try {
    const result = await api.checkForUpdates(channel);
    if (result) {
      pendingUpdate = result;
      showUpdateBanner(`Update available · ${result.targetCommit.slice(0, 12)} — ${result.subject}`);
      el<HTMLButtonElement>('applyUpdateBtn').hidden = false;
      setUpdateStatusPill('Update available', 'ready');
    } else {
      setUpdateStatusPill('Up to date', 'ok');
      showUpdateBanner('You are running the latest commit on this channel.');
    }
  } catch (error) {
    setUpdateStatusPill('Check failed', 'error');
    showUpdateBanner(error instanceof Error ? error.message : String(error));
  } finally {
    setUpdateUiBusy(false);
  }
});

el('applyUpdateBtn').addEventListener('click', async () => {
  if (updateInProgress || !pendingUpdate) return;
  const target = pendingUpdate.targetCommit;
  if (pendingUpdate.hasNewMigrations) {
    const proceed = window.confirm(
      `This update includes database schema changes.\n\n` +
        `If the update fails after the schema migrates, automatic rollback ` +
        `may not be able to restore a working backend, and you may need to ` +
        `use Factory Reset > Database to recover (this will lose instance data).\n\n` +
        `Continue with the update?`,
    );
    if (!proceed) {
      setUpdateStatusPill('Cancelled', 'ready');
      return;
    }
  }
  setUpdateUiBusy(true);
  hideUpdateBanner();
  setUpdateStatusPill('Updating…', 'pending');
  showOverlay('Updating Sentinel');
  setOverlayProgress('Starting…', '');
  try {
    await api.applyUpdate(target);
  } catch (error) {
    hideOverlay();
    setUpdateStatusPill('Update failed', 'error');
    showUpdateBanner(error instanceof Error ? error.message : String(error));
    setUpdateUiBusy(false);
  }
});

el<HTMLSelectElement>('channelSelect').addEventListener('change', async (event) => {
  if (updateInProgress) return;
  const select = event.target as HTMLSelectElement;
  const channel = select.value as ReleaseChannel;
  if (currentChannel === channel) return;

  const revertSelect = () => {
    if (currentChannel === 'stable' || currentChannel === 'beta') {
      select.value = currentChannel;
    }
  };

  setUpdateStatusPill('Checking channel…', 'pending');
  let probe: UpdateAvailable | null;
  try {
    probe = await api.checkForUpdates(channel);
  } catch (error) {
    setUpdateStatusPill('Check failed', 'error');
    showUpdateBanner(error instanceof Error ? error.message : String(error));
    revertSelect();
    return;
  }

  const migrationWarning =
    probe?.hasNewMigrations
      ? `\n\nThis switch includes database schema changes. If the switch fails ` +
        `after the schema migrates, automatic rollback may not be able to ` +
        `restore a working backend, and you may need to use Factory Reset > ` +
        `Database to recover (this will lose instance data).`
      : '';
  const confirmed = window.confirm(
    `Switch to ${channel} channel? Sentinel will fetch and apply the latest ` +
      `${channel} commit, restarting the backend.${migrationWarning}\n\nContinue?`,
  );
  if (!confirmed) {
    revertSelect();
    setUpdateStatusPill('Cancelled', 'ready');
    return;
  }

  setUpdateUiBusy(true);
  hideUpdateBanner();
  setUpdateStatusPill('Switching channel…', 'pending');
  showOverlay(`Switching to ${channel}`);
  setOverlayProgress('Starting…', '');
  try {
    await api.switchChannel(channel);
  } catch (error) {
    hideOverlay();
    setUpdateStatusPill('Switch failed', 'error');
    showUpdateBanner(error instanceof Error ? error.message : String(error));
    setUpdateUiBusy(false);
  }
});

// Set on initial failure, cleared on rollback's terminal event. Keeps
// controls disabled so a second update can't race the rollback.
let rollbackInProgress = false;

api.onUpdateProgress((progress: UpdateProgress) => {
  showUpdateProgress(progress);
  const isRollback = progress.phase.startsWith('rollback');
  showOverlay(isRollback ? 'Rolling back' : 'Updating Sentinel');
  const label = UPDATE_PHASE_LABELS[progress.phase] ?? progress.phase;
  setOverlayProgress(label, progress.message);
});

api.onUpdateApplied(async (version: RuntimeVersion) => {
  hideUpdateProgress();
  hideOverlay();
  pendingUpdate = null;
  el<HTMLButtonElement>('applyUpdateBtn').hidden = true;
  if (rollbackInProgress) {
    setUpdateStatusPill('Rolled back', 'error');
    rollbackInProgress = false;
  } else {
    setUpdateStatusPill('Up to date', 'ok');
    showUpdateBanner(`Updated to ${version.commit?.slice(0, 12) ?? 'unknown'} on ${version.channel}.`);
  }
  renderVersion(version);
  setUpdateUiBusy(false);
});

api.onUpdateFailed((failure: UpdateFailure) => {
  if (failure.phase === 'rollback-failed') {
    hideUpdateProgress();
    hideOverlay();
    setUpdateStatusPill('Rollback failed', 'error');
    showUpdateBanner(`${failure.reason} Use Factory Reset to recover.`);
    rollbackInProgress = false;
    setUpdateUiBusy(false);
    return;
  }
  setUpdateStatusPill('Update failed', 'error');
  showUpdateBanner(`Update failed during ${failure.phase}: ${failure.reason} Rolling back...`);
  rollbackInProgress = true;
});

void refreshVersion();

void refresh();
