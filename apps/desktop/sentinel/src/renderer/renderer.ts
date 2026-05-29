import type {
  DesktopStatus,
  FactoryResetScopes,
  LogEntry,
  ManagedServiceStatus,
  PayloadFailure,
  PayloadInfo,
  PayloadPhase,
  PayloadProgress,
  PayloadUpdate,
  ReleaseChannel,
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
  renderPayload(status.payload);
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
    detailRow('Auth', `<span class="mono">${escapeHtml(status.runtime.authMethod)}</span>`),
    detailRow('Phase', `<span class="mono">${escapeHtml(runtimePhase(status))}</span>`),
  ].join('');
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

api.onStatus((status: DesktopStatus) => {
  render(status);
});

// ---- Full-screen lock overlay (payload install/update) ----
const PAYLOAD_PHASE_LABELS: Record<PayloadPhase, string> = {
  download: 'Downloading payload',
  verify: 'Verifying download',
  extract: 'Installing app files',
  swap: 'Swapping payload',
  restart: 'Restarting Sentinel',
  'health-check': 'Verifying backend',
  done: 'Done',
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

api.onLog((entry: LogEntry) => {
  logs.push(entry);
  if (logs.length > 2000) logs = logs.slice(-2000);
  void api.getStatus().then(render);
  renderServiceFilter();
  renderLogs();
});

// ---- Payload version, install & updates ----
let pendingUpdate: PayloadUpdate | null = null;
let payloadBusy = false;

function setUpdateStatusPill(text: string, variant = ''): void {
  setPill('updateStatusPill', text, variant);
}

function renderPayload(payload: PayloadInfo): void {
  if (!payload.installed) {
    el('currentVersion').textContent = 'not installed';
    if (!payloadBusy) {
      setUpdateStatusPill('No payload', 'missing');
      showUpdateBanner('No app payload installed. Use “Install from File…” to load one, or “Check for Updates” to download.');
    }
    return;
  }
  const short = payload.commit ? payload.commit.slice(0, 12) : 'unknown';
  const channel = payload.channel ?? 'stable';
  el('currentVersion').textContent = `${payload.version ?? short} · ${channel}`;
  // The dropdown reflects the user's chosen *update* channel (persisted), which
  // may differ from the installed payload's channel, so we don't override it.
}

function showUpdateBanner(text: string): void {
  el('updateBannerText').textContent = text;
  el('updateBanner').hidden = false;
}

function hideUpdateBanner(): void {
  el('updateBanner').hidden = true;
}

function setPayloadUiBusy(busy: boolean): void {
  payloadBusy = busy;
  el<HTMLButtonElement>('installFromFileBtn').disabled = busy;
  el<HTMLButtonElement>('checkUpdatesBtn').disabled = busy;
  el<HTMLButtonElement>('applyUpdateBtn').disabled = busy;
  el<HTMLSelectElement>('channelSelect').disabled = busy;
}

el('installFromFileBtn').addEventListener('click', async () => {
  if (payloadBusy) return;
  setPayloadUiBusy(true);
  hideUpdateBanner();
  setUpdateStatusPill('Installing…', 'pending');
  showOverlay('Installing Sentinel');
  setOverlayProgress('Starting…', '');
  try {
    const installed = await api.installFromFile();
    if (!installed) {
      hideOverlay();
      setUpdateStatusPill('Idle', '');
      render(await api.getStatus());
    }
  } catch (error) {
    hideOverlay();
    setUpdateStatusPill('Install failed', 'error');
    showUpdateBanner(error instanceof Error ? error.message : String(error));
  } finally {
    setPayloadUiBusy(false);
  }
});

function presentUpdate(result: PayloadUpdate): void {
  pendingUpdate = result;
  const migrationNote = result.hasNewMigrations ? ' (includes database migrations)' : '';
  showUpdateBanner(`Update available · ${result.version} · ${result.commit.slice(0, 12)}${migrationNote}`);
  const applyBtn = el<HTMLButtonElement>('applyUpdateBtn');
  applyBtn.hidden = false;
  applyBtn.textContent = result.hasNewMigrations ? 'Apply Update (migrations)' : 'Apply Update';
  setUpdateStatusPill('Update available', 'ready');
}

// silent=true is the launch auto-check: it surfaces an available update but
// stays quiet (no pill flicker, no "up to date" banner) when there's nothing
// new, so it never stomps the no-payload guidance.
async function runUpdateCheck(silent: boolean): Promise<void> {
  if (payloadBusy) return;
  const channel = el<HTMLSelectElement>('channelSelect').value as ReleaseChannel;
  if (!silent) {
    hideUpdateBanner();
    el<HTMLButtonElement>('applyUpdateBtn').hidden = true;
    pendingUpdate = null;
    setPayloadUiBusy(true);
    setUpdateStatusPill('Checking…', 'pending');
  }
  try {
    const result = await api.checkForUpdate(channel);
    if (result) {
      presentUpdate(result);
    } else if (!silent) {
      setUpdateStatusPill('Up to date', 'ok');
      showUpdateBanner('You are running the latest payload on this channel.');
    }
  } catch (error) {
    if (!silent) {
      setUpdateStatusPill('Check failed', 'error');
      showUpdateBanner(error instanceof Error ? error.message : String(error));
    }
  } finally {
    if (!silent) setPayloadUiBusy(false);
  }
}

el('checkUpdatesBtn').addEventListener('click', () => void runUpdateCheck(false));

el('applyUpdateBtn').addEventListener('click', async () => {
  if (payloadBusy || !pendingUpdate) return;
  const update = pendingUpdate;
  setPayloadUiBusy(true);
  hideUpdateBanner();
  setUpdateStatusPill('Updating…', 'pending');
  showOverlay('Updating Sentinel');
  setOverlayProgress('Starting…', '');
  try {
    await api.applyUpdate(update);
  } catch (error) {
    hideOverlay();
    setUpdateStatusPill('Update failed', 'error');
    showUpdateBanner(error instanceof Error ? error.message : String(error));
    setPayloadUiBusy(false);
  }
});

api.onPayloadProgress((progress: PayloadProgress) => {
  // The terminal 'done' event can arrive after onPayloadInstalled already hid
  // the overlay; treat it as a hide so we never re-show a finished install.
  if (progress.phase === 'done') {
    hideOverlay();
    return;
  }
  showOverlay(payloadBusy ? 'Updating Sentinel' : 'Installing Sentinel');
  const label = PAYLOAD_PHASE_LABELS[progress.phase] ?? progress.phase;
  setOverlayProgress(label, progress.message, progress.fractionComplete);
});

api.onPayloadInstalled(async (info: PayloadInfo) => {
  hideOverlay();
  pendingUpdate = null;
  el<HTMLButtonElement>('applyUpdateBtn').hidden = true;
  setUpdateStatusPill('Up to date', 'ok');
  showUpdateBanner(`Installed ${info.version ?? 'app'}${info.channel ? ` on ${info.channel}` : ''}.`);
  setPayloadUiBusy(false);
  render(await api.getStatus());
});

api.onPayloadFailed((failure: PayloadFailure) => {
  hideOverlay();
  setUpdateStatusPill('Failed', 'error');
  showUpdateBanner(`Payload ${failure.phase} failed: ${failure.reason}`);
  setPayloadUiBusy(false);
});

// Persist the chosen update channel so it survives relaunch and drives the
// launch auto-check.
const CHANNEL_STORAGE_KEY = 'sentinel.updateChannel';

function restoreChannel(): void {
  const stored = localStorage.getItem(CHANNEL_STORAGE_KEY);
  if (stored === 'stable' || stored === 'beta') {
    el<HTMLSelectElement>('channelSelect').value = stored;
  }
}

el<HTMLSelectElement>('channelSelect').addEventListener('change', (event) => {
  localStorage.setItem(CHANNEL_STORAGE_KEY, (event.target as HTMLSelectElement).value);
});

restoreChannel();

void refresh().then(() => runUpdateCheck(true));
