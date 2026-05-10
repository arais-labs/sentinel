import type { DesktopStatus, InstanceSummary, LogEntry, ManagedServiceStatus } from '../shared/ipc.js';

const api = window.sentinelDesktop;
let latestStatus: DesktopStatus | undefined;
let logs: LogEntry[] = [];
let pendingAuthAction: { kind: 'create'; name: string } | { kind: 'reset'; name: string } | undefined;

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
    root.innerHTML = '<div class="empty-state">No instances yet. Create one to start local Sentinel.</div>';
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
            <button data-action="reset-auth" data-name="${escapeHtml(instance.name)}">Reset Auth</button>
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

function renderLogs(): void {
  el('logs').textContent = logs.map((entry) => `[${entry.at}] ${entry.service}: ${entry.line}`).join('\n');
  el('logs').scrollTop = el('logs').scrollHeight;
}

function showAuthModal(action: typeof pendingAuthAction): void {
  pendingAuthAction = action;
  const isCreate = action?.kind === 'create';
  el('authTitle').textContent = isCreate ? 'Create admin login' : 'Reset admin login';
  el('authDescription').textContent = isCreate
    ? `Set credentials for instance "${action?.name}".`
    : `Replace credentials for instance "${action?.name}".`;
  el('authSubmit').textContent = isCreate ? 'Create Instance' : 'Reset Auth';
  el<HTMLInputElement>('authUsername').value = 'admin';
  el<HTMLInputElement>('authPassword').value = '';
  el('authModal').classList.remove('hidden');
  el('authModal').setAttribute('aria-hidden', 'false');
  el<HTMLInputElement>('authPassword').focus();
}

function hideAuthModal(): void {
  pendingAuthAction = undefined;
  el('authModal').classList.add('hidden');
  el('authModal').setAttribute('aria-hidden', 'true');
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
    if (action === 'reset-auth' && name) {
      showAuthModal({ kind: 'reset', name });
    }
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
  showAuthModal({ kind: 'create', name });
});

el<HTMLFormElement>('authForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!pendingAuthAction) return;
  const username = el<HTMLInputElement>('authUsername').value;
  const password = el<HTMLInputElement>('authPassword').value;
  const action = pendingAuthAction;
  try {
    if (action.kind === 'create') {
      render(await api.createInstance({ name: action.name, username, password }));
    } else {
      render(await api.resetAuth(action.name, username, password));
    }
    hideAuthModal();
  } catch (error) {
    logs.push({ service: 'manager', line: String((error as Error).message || error), at: new Date().toISOString() });
    renderLogs();
  }
});

document.addEventListener('click', (event) => {
  const target = event.target as HTMLElement;
  if (target.dataset.modalClose === 'true') {
    hideAuthModal();
  }
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
