import { useState, useEffect, useMemo, useCallback } from 'react';
import { api } from '../lib/api';
import { TASK_STATUS, TASK_STATUS_ORDER, TASK_TYPE } from '../lib/constants';
import { shortDate } from '../lib/utils';
import ConfirmDialog from '../components/ConfirmDialog';
import ListCard from '../components/ListCard';
import { IconGitBranch, IconCopy } from '../components/Icons';

const STATUS_COLORS = {
  backlog:     { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  todo:        { bg: '#eff6ff', icon: '#3b82f6' },
  in_progress: { bg: '#fefce8', icon: '#f59e0b' },
  in_review:   { bg: '#eef2ff', icon: '#6366f1' },
  handoff:     { bg: '#f5f3ff', icon: '#8b5cf6' },
  done:        { bg: '#ecfdf5', icon: '#10b981' },
  cancelled:   { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },

  // Legacy
  open:        { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  work_ready:  { bg: '#ecfdf5', icon: '#10b981' },
  in_analysis: { bg: '#eff6ff', icon: '#3b82f6' },
  queued:      { bg: '#fefce8', icon: '#f59e0b' },
  detected:    { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  handed_off:  { bg: '#f5f3ff', icon: '#8b5cf6' },
  blocked:     { bg: '#fff1f2', icon: '#ef4444' },
  closed:      { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
};

const WORK_PACKAGE_FIELDS = [
  { key: 'objective', label: 'Objective' },
  { key: 'plan', label: 'Execution Plan' },
  { key: 'deliverable', label: 'Deliverable' },
  { key: 'links', label: 'Links / References' },
  { key: 'prDescription', label: 'PR Description (GitHub)' },
  { key: 'diff', label: 'Diff Summary (GitHub)' },
  { key: 'review', label: 'Review Notes (GitHub)' },
];

export default function TasksPage({ notify, setRefresh }) {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [clientFilter, setClientFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedId, setSelectedId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [newTask, setNewTask] = useState({
    title: '',
    summary: '',
    client: '',
    repo: '',
    source: 'manual',
    type: 'task',
    status: 'todo',
    priority: 'medium',
    owner: '',
    handoffTo: '',
  });

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const { tasks: data } = await api('/api/tasks');
      setTasks(Array.isArray(data) ? data : []);
      if (data?.length > 0 && !selectedId) setSelectedId(data[0].id);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [selectedId]);

  useEffect(() => { setRefresh(load); }, []);

  useEffect(() => { load(); }, [load]);

  const clientList = useMemo(() => {
    return Array.from(new Set(tasks.map((task) => task.client).filter(Boolean))).sort();
  }, [tasks]);

  const filtered = useMemo(() => {
    return tasks
      .filter((task) => clientFilter === 'all' || task.client === clientFilter)
      .filter((task) => statusFilter === 'all' || task.status === statusFilter);
  }, [tasks, clientFilter, statusFilter]);
  const statusChips = useMemo(() => {
    const dynamicStatuses = Array.from(
      new Set(tasks.map((task) => task.status).filter(Boolean)),
    ).filter((status) => !TASK_STATUS_ORDER.includes(status));
    return ['all', ...TASK_STATUS_ORDER, ...dynamicStatuses];
  }, [tasks]);
  const statusOptions = useMemo(
    () => Array.from(new Set([
      ...TASK_STATUS_ORDER,
      'open',
      'detected',
      'queued',
      'in_analysis',
      'work_ready',
      'handed_off',
      'closed',
    ])),
    [],
  );

  const selected = tasks.find((task) => task.id === selectedId) || null;

  const patchTask = async (taskId, patch, message = 'Saved') => {
    try {
      await api(`/api/tasks/${taskId}`, { method: 'PATCH', body: JSON.stringify(patch) });
      notify(message);
      load();
    } catch (err) {
      notify(err.message || 'Save failure', 'warn');
    }
  };

  const asNullable = (value) => {
    const trimmed = (value || '').trim();
    return trimmed || null;
  };

  const createTask = async () => {
    const title = (newTask.title || '').trim();
    if (!title) {
      notify('Task title is required', 'warn');
      return;
    }
    try {
      const payload = {
        title,
        summary: asNullable(newTask.summary),
        client: asNullable(newTask.client),
        repo: asNullable(newTask.repo),
        source: asNullable(newTask.source),
        type: asNullable(newTask.type),
        status: newTask.status || 'todo',
        priority: newTask.priority || 'medium',
        owner: asNullable(newTask.owner),
        handoffTo: asNullable(newTask.handoffTo),
      };
      const created = await api('/api/tasks', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      notify('Task created');
      setCreating(false);
      setNewTask({
        title: '',
        summary: '',
        client: '',
        repo: '',
        source: 'manual',
        type: 'task',
        status: 'todo',
        priority: 'medium',
        owner: '',
        handoffTo: '',
      });
      setSelectedId(created?.id || null);
      load();
    } catch (err) {
      notify(err.message || 'Failed to create task', 'warn');
    }
  };

  const statusLabel = (value) => TASK_STATUS[value]?.label || value || 'Unknown';
  const typeLabel = (value) => TASK_TYPE[value]?.label || value || 'Task';
  const priorityLabel = (value) => (value || 'medium').replace('_', ' ');
  const ownerLabel = (task) => task.owner || task.handoffTo || task.updatedBy || 'unassigned';

  return (
    <div className="tasks-page" style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="top-bar-actions-row">
        <h2 className="text-lg font-bold">Tasks</h2>
        <span className="text-xs text-[color:var(--text-muted)]">
          Generic workflow tasks with optional GitHub metadata.
        </span>
        <div className="flex items-center gap-2">
          <button
            className="btn-secondary"
            onClick={() => setCreating((v) => !v)}
          >
            {creating ? 'Cancel' : 'New Task'}
          </button>
        </div>
      </div>

      {creating && (
        <section className="panel m-3 p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <label className="form-label">Title</label>
              <input
                className="input-field"
                value={newTask.title}
                onChange={(e) => setNewTask((prev) => ({ ...prev, title: e.target.value }))}
                placeholder="Task title"
              />
            </div>
            <div className="space-y-2">
              <label className="form-label">Type</label>
              <select
                className="input-field"
                value={newTask.type}
                onChange={(e) => setNewTask((prev) => ({ ...prev, type: e.target.value }))}
              >
                {Object.keys(TASK_TYPE).map((value) => (
                  <option key={value} value={value}>{TASK_TYPE[value].label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-2">
              <label className="form-label">Status</label>
              <select
                className="input-field"
                value={newTask.status}
                onChange={(e) => setNewTask((prev) => ({ ...prev, status: e.target.value }))}
              >
                {TASK_STATUS_ORDER.map((value) => (
                  <option key={value} value={value}>{statusLabel(value)}</option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="form-label">Priority</label>
              <select
                className="input-field"
                value={newTask.priority}
                onChange={(e) => setNewTask((prev) => ({ ...prev, priority: e.target.value }))}
              >
                {['low', 'medium', 'high', 'critical'].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="form-label">Source</label>
              <input
                className="input-field"
                value={newTask.source}
                onChange={(e) => setNewTask((prev) => ({ ...prev, source: e.target.value }))}
                placeholder="manual"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <label className="form-label">Client</label>
              <input
                className="input-field"
                value={newTask.client}
                onChange={(e) => setNewTask((prev) => ({ ...prev, client: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <label className="form-label">Project / Repository</label>
              <input
                className="input-field"
                value={newTask.repo}
                onChange={(e) => setNewTask((prev) => ({ ...prev, repo: e.target.value }))}
                placeholder="optional"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <label className="form-label">Owner</label>
              <input
                className="input-field"
                value={newTask.owner}
                onChange={(e) => setNewTask((prev) => ({ ...prev, owner: e.target.value }))}
              />
            </div>
            <div className="space-y-2">
              <label className="form-label">Handoff To</label>
              <input
                className="input-field"
                value={newTask.handoffTo}
                onChange={(e) => setNewTask((prev) => ({ ...prev, handoffTo: e.target.value }))}
              />
            </div>
          </div>
          <div className="space-y-2">
            <label className="form-label">Summary</label>
            <textarea
              className="editor-textarea"
              style={{ minHeight: '120px' }}
              value={newTask.summary}
              onChange={(e) => setNewTask((prev) => ({ ...prev, summary: e.target.value }))}
            />
          </div>
          <div className="flex items-center gap-3">
            <button className="btn-primary" onClick={createTask}>Create Task</button>
            <button className="btn-secondary" onClick={() => setCreating(false)}>Close</button>
          </div>
        </section>
      )}

      <div className="sub-header">
        <div className="sub-header-left">
          {statusChips.map((status) => (
            <div
              key={status}
              className={`stat-chip ${statusFilter === status ? 'active' : ''}`}
              onClick={() => setStatusFilter(status)}
            >
              <span className="stat-label">{status === 'all' ? 'all' : statusLabel(status)}</span>
              <strong className="stat-value">
                {status === 'all' ? tasks.length : tasks.filter((item) => item.status === status).length}
              </strong>
            </div>
          ))}
        </div>
        <div className="sub-header-right">
          <select
            className="stat-chip bg-transparent outline-none border-none text-xs font-bold uppercase tracking-widest"
            style={{ cursor: 'pointer' }}
            value={clientFilter}
            onChange={(e) => setClientFilter(e.target.value)}
          >
            <option value="all">All Clients</option>
            {clientList.map((client) => <option key={client} value={client}>{client}</option>)}
          </select>
        </div>
      </div>

      <div className="triage-layout">
        <section className="list-pane">
          {loading ? (
            <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">Loading tasks...</div>
          ) : (
            <div className="lead-list">
              {filtered.map((task) => {
                const colors = STATUS_COLORS[task.status] || STATUS_COLORS.detected;
                return (
                  <ListCard
                    key={task.id}
                    active={selectedId === task.id}
                    onClick={() => setSelectedId(task.id)}
                    avatarStyle={{ backgroundColor: colors.bg, color: colors.icon }}
                    avatarContent={<IconGitBranch size={16} />}
                    title={task.title || `Task ${task.id}`}
                    subtitle={`${task.client || task.repo || 'general'} · ${ownerLabel(task)}`}
                    meta={shortDate(task.updatedAt || task.detectedAt)}
                    badge={<span className="badge badge-neutral">{statusLabel(task.status)}</span>}
                  />
                );
              })}
            </div>
          )}
        </section>

        <aside className="detail-pane">
          {!selected ? (
            <div className="flex items-center justify-center h-full opacity-50">Select task node</div>
          ) : (
            <div className="task-detail-shell">
              <div className="detail-hero task-hero">
                <div className="detail-avatar" style={{ backgroundColor: (STATUS_COLORS[selected.status] || STATUS_COLORS.detected).bg, color: (STATUS_COLORS[selected.status] || STATUS_COLORS.detected).icon }}>
                  <IconGitBranch size={22} />
                </div>
                <div className="flex-1 min-w-0">
                  <input
                    key={`title-inline-${selected.id}`}
                    className="input-field task-title-input"
                    defaultValue={selected.title || `Task ${selected.id}`}
                    onBlur={(e) => patchTask(selected.id, { title: asNullable(e.target.value) }, 'Title updated')}
                  />
                  <p className="task-meta-line">
                    {selected.client || selected.repo || 'general'} · owner: {ownerLabel(selected)} · updated by: {selected.updatedBy || 'unknown'}
                  </p>
                </div>
                <div className="task-hero-badges">
                  <span className="badge badge-info">{typeLabel(selected.type)}</span>
                  <span className="badge badge-success">{statusLabel(selected.status)}</span>
                  <span className="badge badge-neutral">{priorityLabel(selected.priority)}</span>
                </div>
              </div>
              <div className="detail-content task-detail-content">
                <section className="panel task-quick-card">
                  <p className="form-label">Quick Controls</p>
                  <div className="task-quick-grid">
                    <div className="task-control">
                      <label className="form-label">Status</label>
                      <select
                        key={`status-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.status || 'todo'}
                        onChange={(e) => patchTask(selected.id, { status: e.target.value }, 'Status updated')}
                      >
                        {statusOptions.map((value) => (
                          <option key={value} value={value}>{statusLabel(value)}</option>
                        ))}
                      </select>
                    </div>
                    <div className="task-control">
                      <label className="form-label">Priority</label>
                      <select
                        key={`priority-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.priority || 'medium'}
                        onChange={(e) => patchTask(selected.id, { priority: e.target.value }, 'Priority updated')}
                      >
                        {['low', 'medium', 'high', 'critical'].map((value) => (
                          <option key={value} value={value}>{value}</option>
                        ))}
                      </select>
                    </div>
                    <div className="task-control">
                      <label className="form-label">Type</label>
                      <select
                        key={`type-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.type || ''}
                        onChange={(e) => patchTask(selected.id, { type: asNullable(e.target.value) }, 'Type updated')}
                      >
                        <option value="">Unset</option>
                        {Object.keys(TASK_TYPE).map((value) => (
                          <option key={value} value={value}>{TASK_TYPE[value].label}</option>
                        ))}
                      </select>
                    </div>
                    <div className="task-control">
                      <label className="form-label">Owner</label>
                      <input
                        key={`owner-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.owner || ''}
                        onBlur={(e) => patchTask(selected.id, { owner: asNullable(e.target.value) }, 'Owner updated')}
                      />
                    </div>
                    <div className="task-control">
                      <label className="form-label">Handoff To</label>
                      <input
                        key={`handoff-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.handoffTo || ''}
                        onBlur={(e) => patchTask(selected.id, { handoffTo: asNullable(e.target.value) }, 'Handoff target updated')}
                      />
                    </div>
                  </div>
                </section>

                <div className="task-editor-grid">
                  <section className="panel task-editor-card">
                    <label className="form-label">Task Summary</label>
                    <textarea
                      key={`summary-${selected.id}`}
                      className="editor-textarea"
                      style={{ minHeight: '160px' }}
                      defaultValue={selected.summary || ''}
                      onBlur={(e) => patchTask(selected.id, { summary: asNullable(e.target.value) }, 'Summary updated')}
                    />
                  </section>

                  <section className="panel task-editor-card">
                    <label className="form-label">Handover Notes</label>
                    <textarea
                      key={`notes-${selected.id}`}
                      className="editor-textarea"
                      style={{ minHeight: '160px' }}
                      defaultValue={selected.notes || ''}
                      onBlur={(e) => patchTask(selected.id, { notes: asNullable(e.target.value) }, 'Notes saved')}
                    />
                  </section>
                </div>

                <details className="panel task-collapsible">
                  <summary className="task-collapsible-summary">
                    Extended Metadata
                  </summary>
                  <div className="task-collapsible-grid">
                    <section className="task-control">
                      <label className="form-label">Source</label>
                      <input
                        key={`source-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.source || ''}
                        onBlur={(e) => patchTask(selected.id, { source: asNullable(e.target.value) }, 'Source updated')}
                      />
                    </section>
                    <section className="task-control">
                      <label className="form-label">Client</label>
                      <input
                        key={`client-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.client || ''}
                        onBlur={(e) => patchTask(selected.id, { client: asNullable(e.target.value) }, 'Client updated')}
                      />
                    </section>
                    <section className="task-control task-control-wide">
                      <label className="form-label">Project / Repository</label>
                      <input
                        key={`repo-${selected.id}`}
                        className="input-field"
                        defaultValue={selected.repo || ''}
                        onBlur={(e) => patchTask(selected.id, { repo: asNullable(e.target.value) }, 'Repository updated')}
                      />
                    </section>
                  </div>
                </details>

                <details className="panel task-collapsible">
                  <summary className="task-collapsible-summary">
                    Work Package Fields (Optional)
                  </summary>
                  <div className="task-collapsible-grid task-collapsible-grid-wide">
                    {WORK_PACKAGE_FIELDS.map((field) => (
                      <section key={field.key} className="panel task-editor-card">
                        <div className="task-copy-row">
                          <label className="form-label">{field.label}</label>
                          <button
                            className="task-copy-btn"
                            onClick={() => { navigator.clipboard.writeText(selected.workPackage?.[field.key] || ''); notify('Copied to clipboard'); }}
                          >
                            <IconCopy size={12} /> Copy
                          </button>
                        </div>
                        <textarea
                          key={`${field.key}-${selected.id}`}
                          className="editor-textarea"
                          style={{ minHeight: '62px', fontSize: '12px', fontFamily: 'monospace' }}
                          defaultValue={selected.workPackage?.[field.key] || ''}
                          onBlur={(e) => patchTask(
                            selected.id,
                            { workPackage: { [field.key]: e.target.value } },
                            `${field.label} updated`,
                          )}
                        />
                      </section>
                    ))}
                  </div>
                </details>

                <div className="task-danger-row">
                  <button className="btn-danger" onClick={() => setConfirmDeleteId(selected.id)}>
                    Delete Task
                  </button>
                </div>
              </div>
            </div>
          )}
        </aside>
      </div>

      {confirmDeleteId && (
        <ConfirmDialog
          title="Purge Task Node"
          message="This action is irreversible. Permanently decommission this node?"
          onConfirm={async () => {
            await api(`/api/tasks/${confirmDeleteId}`, { method: 'DELETE' });
            setSelectedId(null);
            load();
            setConfirmDeleteId(null);
          }}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </div>
  );
}
