import type { DesktopStatus, LogEntry, ManagedServiceStatus } from '../shared/ipc.js';

const api = window.sentinelDesktop;
let logs: LogEntry[] = [];

const el = <T extends HTMLElement>(id: string): T => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing element #${id}`);
  return node as T;
};

function pill(text: string, variant = ''): string {
  return `<span class="pill ${variant}">${escapeHtml(text)}</span>`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' })[char]!);
}

function render(status: DesktopStatus): void {
  el('supportPath').textContent = status.appSupportPath;
  el('qemuStatus').innerHTML = status.qemu.installed ? pill('Installed', 'ok') : pill('Missing', 'missing');
  el('qemuMessage').textContent = status.qemu.message || '';
  el('imageStatus').innerHTML = status.runtimeImage.present ? pill('Present', 'ok') : pill('Missing', 'missing');
  renderServices(status.services);
}

function renderServices(services: ManagedServiceStatus[]): void {
  const root = el('services');
  const hasRunningService = services.some((service) => service.state === 'running' || service.state === 'starting');
  el<HTMLButtonElement>('stopBtn').disabled = !hasRunningService;
  if (services.length === 0) {
    root.innerHTML = '<div class="empty-state">No managed services are running.</div>';
    return;
  }
  root.innerHTML = services
    .map(
      (service) => `
        <div class="row">
          <strong>${escapeHtml(service.name)}</strong>
          <div>${pill(service.state, service.state)}</div>
          <div class="subcopy">${service.pid ? `pid ${service.pid}` : ''}${service.port ? ` · :${service.port}` : ''}</div>
        </div>
      `,
    )
    .join('');
}

async function refresh(): Promise<void> {
  render(await api.getStatus());
  logs = await api.getLogs();
  renderLogs();
}

function renderLogs(): void {
  el('logs').textContent = logs.map((entry) => `[${entry.at}] ${entry.service}: ${entry.line}`).join('\n');
  el('logs').scrollTop = el('logs').scrollHeight;
}

el('refreshBtn').addEventListener('click', () => void refresh());
el('openBtn').addEventListener('click', () => void api.openSentinel());
el('revealBtn').addEventListener('click', () => void api.revealAppSupport());
el('stopBtn').addEventListener('click', async () => render(await api.stopServices()));

api.onStatus(render);
api.onLog((entry: LogEntry) => {
  logs.push(entry);
  if (logs.length > 2000) logs = logs.slice(-2000);
  renderLogs();
});

void refresh();
