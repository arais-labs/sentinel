import { useState, useEffect, useMemo, useCallback } from 'react';
import { api } from '../lib/api';
import { GITHUB_TASK_STATUS, GITHUB_TASK_STATUS_ORDER, GITHUB_TASK_TYPE } from '../lib/constants';
import { shortDate } from '../lib/utils';
import ConfirmDialog from '../components/ConfirmDialog';
import ListCard from '../components/ListCard';
import { IconGitBranch, IconCopy } from '../components/Icons';

const STATUS_COLORS = {
  work_ready:  { bg: '#ecfdf5', icon: '#10b981' },
  in_analysis: { bg: '#eff6ff', icon: '#3b82f6' },
  queued:      { bg: '#fefce8', icon: '#f59e0b' },
  detected:    { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
  handed_off:  { bg: '#f5f3ff', icon: '#8b5cf6' },
  closed:      { bg: 'var(--surface-2)', icon: 'var(--text-muted)' },
};

export default function GithubTasksPage({ notify, setRefresh }) {
  const [githubTasks, setGithubTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [clientFilter, setClientFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedId, setSelectedId] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const { tasks: data } = await api('/api/github-tasks');
      setGithubTasks(Array.isArray(data) ? data : []);
      if (data?.length > 0 && !selectedId) setSelectedId(data[0].id);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [selectedId]);

  useEffect(() => { setRefresh(load); }, []);

  useEffect(() => { load(); }, [load]);

  const clientList = useMemo(() => {
    return Array.from(new Set(githubTasks.map(t => t.client).filter(Boolean))).sort();
  }, [githubTasks]);

  const filtered = useMemo(() => {
    return githubTasks
      .filter(t => clientFilter === 'all' || t.client === clientFilter)
      .filter(t => statusFilter === 'all' || t.status === statusFilter);
  }, [githubTasks, clientFilter, statusFilter]);

  const selected = githubTasks.find(t => t.id === selectedId) || null;

  const patchTask = async (taskId, patch, message = 'Saved') => {
    try {
      await api(`/api/github-tasks/${taskId}`, { method: 'PATCH', body: JSON.stringify(patch) });
      notify(message);
      load();
    } catch (err) {
      notify(err.message || 'Save failure', 'warn');
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="top-bar-actions-row">
        <h2 className="text-lg font-bold">Task Queue</h2>
      </div>

      <div className="sub-header">
        <div className="sub-header-left">
          {['all', 'detected', 'queued', 'in_analysis', 'work_ready'].map(s => (
            <div
              key={s}
              className={`stat-chip ${statusFilter === s ? 'active' : ''}`}
              onClick={() => setStatusFilter(s)}
            >
              <span className="stat-label">{s.replace('_', ' ')}</span>
              <strong className="stat-value">{s === 'all' ? githubTasks.length : githubTasks.filter(x => x.status === s).length}</strong>
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
            {clientList.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      </div>

      <div className="triage-layout">
        <section className="list-pane">
          {loading ? (
            <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">Scanning repositories...</div>
          ) : (
            <div className="lead-list">
              {filtered.map(t => {
                const colors = STATUS_COLORS[t.status] || STATUS_COLORS.detected;
                return (
                  <ListCard
                    key={t.id}
                    active={selectedId === t.id}
                    onClick={() => setSelectedId(t.id)}
                    avatarStyle={{ backgroundColor: colors.bg, color: colors.icon }}
                    avatarContent={<IconGitBranch size={16} />}
                    title={t.title}
                    subtitle={t.repo}
                    meta={shortDate(t.detectedAt)}
                    badge={<span className="badge badge-neutral">{t.status}</span>}
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
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
              <div className="detail-hero">
                <div className="detail-avatar" style={{ backgroundColor: (STATUS_COLORS[selected.status] || STATUS_COLORS.detected).bg, color: (STATUS_COLORS[selected.status] || STATUS_COLORS.detected).icon }}>
                  <IconGitBranch size={22} />
                </div>
                <div className="flex-1">
                  <h2 className="detail-name">{selected.title}</h2>
                  <p className="text-sm text-[color:var(--text-secondary)] font-mono">{selected.repo} · {selected.source}</p>
                </div>
                <div className="flex gap-2">
                  <span className="badge badge-info">{selected.type}</span>
                  <span className="badge badge-success">{selected.status}</span>
                </div>
              </div>
              <div className="detail-content">
                <section className="space-y-3">
                  <label className="form-label">Task Briefing</label>
                  <div className="p-4 rounded-xl bg-[color:var(--surface-1)] border border-[color:var(--border-subtle)] text-sm leading-relaxed text-[color:var(--text-primary)]">
                    {selected.summary}
                  </div>
                </section>

                <div className="detail-divider" />

                {['review', 'diff', 'prDescription'].map(key => (
                  <section key={key} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <label className="form-label">{key.replace(/([A-Z])/g, ' $1')}</label>
                      <button
                        className="text-[10px] font-bold text-[color:var(--accent-solid)] flex items-center gap-1 uppercase tracking-[0.1em] hover:underline"
                        onClick={() => { navigator.clipboard.writeText(selected.workPackage?.[key] || ''); notify('Copied to clipboard'); }}
                      >
                        <IconCopy size={12} /> Copy
                      </button>
                    </div>
                    <div className="editor-textarea" style={{ minHeight: '90px', fontSize: '12px', fontFamily: 'monospace', opacity: selected.workPackage?.[key] ? 1 : 0.4 }}>
                      {selected.workPackage?.[key] || 'Not yet compiled...'}
                    </div>
                  </section>
                ))}

                <div className="detail-divider" />

                <section className="space-y-2">
                  <label className="form-label">Handover Notes</label>
                  <textarea
                    className="editor-textarea"
                    style={{ minHeight: '140px' }}
                    defaultValue={selected.notes}
                    onBlur={(e) => patchTask(selected.id, { notes: e.target.value }, 'Notes saved')}
                  />
                </section>

                <div className="flex items-center gap-3 pt-2 pb-8">
                  <button className="btn-primary" style={{ flex: 1 }}>Promote to Work Ready</button>
                  <button className="btn-danger" style={{ flex: 1 }} onClick={() => setConfirmDeleteId(selected.id)}>Purge Task</button>
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
          onConfirm={() => {
            api(`/api/github-tasks/${confirmDeleteId}`, { method: 'DELETE' });
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
