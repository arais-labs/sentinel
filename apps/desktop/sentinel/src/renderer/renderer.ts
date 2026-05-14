import type { DesktopStatus, LogEntry, ManagedServiceStatus } from '../shared/ipc.js';

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
  const imageVariant = status.runtimeImage.present ? 'ok' : recentQemuLine() ? 'starting' : 'missing';
  const imageLabel = status.runtimeImage.present ? 'Present' : recentQemuLine() ? 'Preparing' : 'Missing';
  const qemuVariant = status.qemu.installed ? 'ok' : 'missing';
  const qemuLabel = status.qemu.installed ? 'Installed' : 'Missing';
  const overallVariant = !status.qemu.installed ? 'missing' : status.runtimeImage.present ? 'ok' : 'starting';
  const overallLabel = !status.qemu.installed ? 'Missing' : status.runtimeImage.present ? 'Ready' : 'Preparing';
  setPill('runtimeOverall', overallLabel, overallVariant);
  root.innerHTML = [
    detailRow('Provider', '<span class="mono">qemu</span>'),
    detailRow('Protocol', stateDot(qemuLabel, qemuVariant)),
    detailRow('Base Image', stateDot(imageLabel, imageVariant)),
    detailRow('Image Path', `<span class="mono">${escapeHtml(shortPath(status.runtimeImage.imagePath))}</span>`),
    detailRow('Key Path', `<span class="mono">${escapeHtml(shortPath(status.runtimeImage.keyPath))}</span>`),
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
  const qemuLine = recentQemuLine();
  if (qemuLine) return qemuLine;
  if (!status.qemu.installed) return status.qemu.message || 'QEMU unavailable';
  return status.runtimeImage.present ? 'Base image ready' : 'Waiting for backend runtime preparation';
}

function recentQemuLine(): string | undefined {
  const entry = [...logs].reverse().find((item) => logCategory(item) === 'qemu');
  if (!entry) return undefined;
  return summarizeLogLine(entry.line);
}

function summarizeLogLine(line: string): string {
  const qemuPrefix = line.match(/qemu image build:\s*(.*)$/i);
  if (qemuPrefix) return qemuPrefix[1].trim();
  const runtimePrefix = line.match(/runtime[/.]qemu[^:]*:\s*(.*)$/i);
  if (runtimePrefix) return runtimePrefix[1].trim();
  return line.length > 92 ? `${line.slice(0, 89)}...` : line;
}

function shortPath(value: string): string {
  const marker = '/sentinel-desktop/';
  const index = value.indexOf(marker);
  return index >= 0 ? value.slice(index + marker.length) : value;
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
  output.innerHTML = visibleLogs.map(renderLogEntry).join('\n');
  output.scrollTop = output.scrollHeight;
}

function renderLogEntry(entry: LogEntry): string {
  const category = logCategory(entry);
  const level = logLevel(entry.line);
  return `<span class="log-line"><span class="log-time">[${escapeHtml(entry.at)}]</span> <span class="log-service ${escapeHtml(category)}">${escapeHtml(category)}</span>: <span class="log-level ${escapeHtml(level.variant)}">${escapeHtml(level.label)}</span> ${escapeHtml(stripLogLevel(entry.line))}</span>`;
}

function logCategory(entry: LogEntry): string {
  if (/qemu image build|runtime[/.]qemu|qemu-system|qemu-img|cloud-init/i.test(entry.line)) return 'qemu';
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

api.onStatus(render);
api.onLog((entry: LogEntry) => {
  logs.push(entry);
  if (logs.length > 2000) logs = logs.slice(-2000);
  void api.getStatus().then(render);
  renderServiceFilter();
  renderLogs();
});

void refresh();
