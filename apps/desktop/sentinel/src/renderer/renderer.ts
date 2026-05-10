import type { DesktopStatus, InstanceSummary, LogEntry, ManagedServiceStatus } from '../shared/ipc.js';

const api = window.sentinelDesktop;
let latestStatus: DesktopStatus | undefined;
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
  latestStatus = status;
  el('supportPath').textContent = status.appSupportPath;
  el('qemuStatus').innerHTML = status.qemu.installed ? pill('Installed', 'ok') : pill('Missing', 'missing');
  el('qemuMessage').textContent = status.qemu.message || '';
  el('imageStatus').innerHTML = status.runtimeImage.present ? pill('Present', 'ok') : pill('Missing', 'missing');
  renderInstances(status.instances);
  renderServices(status.services);
}

function renderInstances(instances: InstanceSummary[]): void {
  const root = el('instances');
  if (instances.length === 0) {
    root.innerHTML = '<p class="subcopy">No instances yet. Create one to start local Sentinel.</p>';
    return;
  }
  root.innerHTML = instances
    .map(
      (instance) => `
        <div class="row">
          <div>
            <strong>${escapeHtml(instance.name)}</strong>
            <div class="subcopy">${escapeHtml(instance.workspacePath)}</div>
          </div>
          <div>${pill(instance.state, instance.state === 'running' ? 'running' : '')}</div>
          <div class="row-actions">
            <button data-action="start" data-name="${escapeHtml(instance.name)}">Start</button>
            <button data-action="restart" data-name="${escapeHtml(instance.name)}">Restart</button>
            <button data-action="backup" data-name="${escapeHtml(instance.name)}">Backup</button>
            <button data-action="delete" data-name="${escapeHtml(instance.name)}" class="danger">Delete</button>
          </div>
        </div>
      `,
    )
    .join('');
}

function renderServices(services: ManagedServiceStatus[]): void {
  const root = el('services');
  if (services.length === 0) {
    root.innerHTML = '<p class="subcopy">No managed services are running.</p>';
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

function renderLogs(): void {
  el('logs').textContent = logs.map((entry) => `[${entry.at}] ${entry.service}: ${entry.line}`).join('\n');
  el('logs').scrollTop = el('logs').scrollHeight;
}

async function refresh(): Promise<void> {
  render(await api.getStatus());
  logs = await api.getLogs();
  renderLogs();
}

document.addEventListener('click', async (event) => {
  const target = event.target as HTMLElement;
  const button = target.closest('button') as HTMLButtonElement | null;
  if (!button) return;
  const action = button.dataset.action;
  const name = button.dataset.name;
  try {
    button.disabled = true;
    if (action === 'start' && name) render(await api.startInstance(name));
    if (action === 'restart' && name) render(await api.restartInstance(name));
    if (action === 'backup' && name) {
      const backupPath = await api.backupInstance(name);
      logs.push({ service: 'manager', line: `Backup written to ${backupPath}`, at: new Date().toISOString() });
      renderLogs();
    }
    if (action === 'delete' && name && confirm(`Delete instance ${name}?`)) render(await api.deleteInstance(name));
  } catch (error) {
    logs.push({ service: 'manager', line: String((error as Error).message || error), at: new Date().toISOString() });
    renderLogs();
  } finally {
    button.disabled = false;
  }
});

el<HTMLFormElement>('createForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const name = el<HTMLInputElement>('instanceName').value;
  render(await api.createInstance({ name }));
});

el('refreshBtn').addEventListener('click', () => void refresh());
el('openBtn').addEventListener('click', () => void api.openSentinel());
el('revealBtn').addEventListener('click', () => void api.revealAppSupport());
el('stopBtn').addEventListener('click', async () => render(await api.stopInstance()));
el('buildImageBtn').addEventListener('click', async () => {
  await api.buildQemuImage();
  await refresh();
});
el('validateImageBtn').addEventListener('click', async () => {
  await api.validateQemuImage();
  await refresh();
});

api.onStatus(render);
api.onLog((entry: LogEntry) => {
  logs.push(entry);
  if (logs.length > 2000) logs = logs.slice(-2000);
  renderLogs();
});

void refresh();
