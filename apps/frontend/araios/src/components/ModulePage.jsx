import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { initials, avatarHue, shortDate } from '../lib/utils';
import ListCard from './ListCard';
import ConfirmDialog from './ConfirmDialog';
import DynamicDetailPane from './DynamicDetailPane';
import DynamicForm from './DynamicForm';
import PageModule from './PageModule';
import ApiModule from './ApiModule';
import { IconCopy } from './Icons';

function buildDataPrompt(config) {
  const BASE_URL = window.location.origin;
  const base = `${BASE_URL}/api/modules/${config.name}`;
  const fields = (config.fields || []).map(f => `${f.key}${f.required ? '*' : ''} (${f.type}${f.options ? ': ' + f.options.join('|') : ''})`).join(', ');
  const standaloneActions = (config.actions || []).filter(a => (a.placement || 'standalone') === 'standalone');
  const detailActions = (config.actions || []).filter(a => a.placement === 'detail');
  const lines = [
    `Module: ${config.label} (${config.name}) — ${config.description || ''}`,
    `Type: data — stores records, full CRUD available.`,
    '',
    `Auth: Use your araiOS token in every request: Authorization: Bearer <your_araios_token>`,
    `If you do not have an araiOS token, ask the user to provide one before proceeding.`,
    '',
    'Endpoints:',
    `  GET    ${base}/records              — list all (optional ?filter_field=&filter_value=)`,
    `  POST   ${base}/records              — create record`,
    `  GET    ${base}/records/:id          — get one`,
    `  PATCH  ${base}/records/:id          — update (partial)`,
    `  DELETE ${base}/records/:id          — delete`,
    '',
    `Fields: ${fields || 'none'}`,
  ];
  if (standaloneActions.length) {
    lines.push('');
    lines.push('Standalone Actions (POST without a record):');
    for (const a of standaloneActions) {
      lines.push(`  ${a.id} — ${a.description || a.label}`);
      lines.push(`    POST ${base}/action/${a.id}`);
      lines.push(`    Body: { ${(a.params || []).map(p => `"${p.key}"${p.required ? '*' : ''}: "${p.placeholder || p.type}"`).join(', ')} }`);
    }
  }
  if (detailActions.length) {
    lines.push('');
    lines.push('Record Actions (POST with a record id):');
    for (const a of detailActions) {
      lines.push(`  ${a.id} — ${a.description || a.label}`);
      lines.push(`    POST ${base}/records/:id/action/${a.id}`);
    }
  }
  return lines.join('\n');
}

/**
 * Generic config-driven triage page for any module.
 * Routes to PageModule or ApiModule based on config.type.
 * For data modules that also have standalone actions, renders a tab bar
 * so both Records and Actions are accessible.
 *
 * @param {string}   moduleName  - Module slug (e.g. "leads")
 * @param {function} notify      - Toast notification callback
 * @param {function} setRefresh  - Register global refresh callback
 */
export default function ModulePage({ moduleName, notify, setRefresh }) {
  const [config, setConfig] = useState(null);
  const [records, setRecords] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState('records');

  const copyPrompt = useCallback(() => {
    if (!config) return;
    navigator.clipboard.writeText(buildDataPrompt(config));
    setCopied(true);
    notify('Prompt copied to clipboard');
    setTimeout(() => setCopied(false), 2000);
  }, [config, notify]);

  // Load config + records
  const loadAll = useCallback(async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const [cfgRes, recRes] = await Promise.all([
        api(`/api/modules/${moduleName}`),
        api(`/api/modules/${moduleName}/records`),
      ]);
      setConfig(cfgRes);
      const recs = Array.isArray(recRes?.records) ? recRes.records : [];
      setRecords(recs);
      setSelectedId(prev => {
        if (prev && recs.some(r => r.id === prev)) return prev;
        return recs[0]?.id || null;
      });
    } catch (err) {
      notify(err.message || `Failed to load ${moduleName}`, 'warn');
    } finally {
      setLoading(false);
    }
  }, [moduleName, notify]);

  useEffect(() => { setRefresh(loadAll); }, [loadAll, setRefresh]);

  useEffect(() => {
    setSelectedId(null);
    setFilter('all');
    setSearch('');
    setActiveTab('records');
    loadAll();
    const timer = setInterval(() => loadAll(true), 30000);
    return () => clearInterval(timer);
  }, [moduleName]); // eslint-disable-line react-hooks/exhaustive-deps

  // Filter/search records
  const filterField = config?.list_config?.filterField;
  const titleField  = config?.list_config?.titleField  || 'id';
  const subtitleField = config?.list_config?.subtitleField;
  const badgeField  = config?.list_config?.badgeField;
  const metaField = config?.list_config?.metaField;

  const filterValues = useMemo(() => {
    if (!filterField) return [];
    return [...new Set(records.map(r => r[filterField]).filter(Boolean))];
  }, [records, filterField]);

  const filtered = useMemo(() => {
    let list = records;
    if (filter !== 'all' && filterField) list = list.filter(r => r[filterField] === filter);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(r =>
        Object.values(r).some(v => String(v ?? '').toLowerCase().includes(q))
      );
    }
    return list;
  }, [records, filter, filterField, search]);

  const selectedRecord = useMemo(
    () => records.find(r => r.id === selectedId) || null,
    [records, selectedId]
  );

  // Standalone actions = actions not scoped to a specific record
  const standaloneActions = useMemo(
    () => (config?.actions || []).filter(a => (a.placement || 'standalone') === 'standalone'),
    [config]
  );
  const hasStandaloneActions = standaloneActions.length > 0;
  const hasCreate = (config?.actions || []).some(a => a.type === 'create');
  const createAction = (config?.actions || []).find(a => a.type === 'create');

  // CRUD handlers
  const handleCreate = async (data) => {
    try {
      setSaving(true);
      const rec = await api(`/api/modules/${moduleName}/records`, { method: 'POST', body: JSON.stringify(data) });
      setCreateOpen(false);
      notify('Created');
      await loadAll(true);
      setSelectedId(rec.id);
    } catch (err) {
      notify(err.message || 'Create failed', 'warn');
    } finally {
      setSaving(false);
    }
  };

  const handlePatch = async (patch) => {
    if (!selectedId) return;
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/records/${selectedId}`, { method: 'PATCH', body: JSON.stringify(patch) });
      notify('Saved');
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Save failed', 'warn');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/records/${id}`, { method: 'DELETE' });
      setConfirmDeleteId(null);
      notify('Deleted');
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Delete failed', 'warn');
    } finally {
      setSaving(false);
    }
  };

  const handleAction = async (actionId) => {
    if (!selectedId) return;
    try {
      setSaving(true);
      const res = await api(`/api/modules/${moduleName}/records/${selectedId}/action/${actionId}`, { method: 'POST', body: JSON.stringify({}) });
      notify(res?.ok !== false ? 'Action completed' : (res?.error || 'Action returned an error'), res?.ok !== false ? 'success' : 'warn');
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Action failed', 'warn');
    } finally {
      setSaving(false);
    }
  };

  // ── Routing ──────────────────────────────────────────────────────────────
  if (!config && loading) return <div className="flex items-center justify-center h-full text-[color:var(--text-muted)] text-sm">Loading…</div>;
  if (!config) return null;

  if (config.type === 'page') return <PageModule config={config} notify={notify} setRefresh={setRefresh} />;
  if (config.type === 'tool') return <ApiModule config={config} notify={notify} setRefresh={setRefresh} />;

  // data module — may have both records and standalone actions
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Sub-header */}
      <div className="sub-header" style={{ position: 'relative', padding: '14px 16px' }}>
        <div className="sub-header-left">
          <span className="text-sm font-medium text-[color:var(--text-secondary)]">{config.label}</span>
          {/* Tab bar — only shown when module has standalone actions */}
          {hasStandaloneActions && (
            <div className="flex items-center gap-1 ml-4">
              {['records', 'actions'].map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors capitalize ${
                    activeTab === tab
                      ? 'bg-[color:var(--accent-solid)] text-[color:var(--app-bg)]'
                      : 'text-[color:var(--text-secondary)] hover:text-[color:var(--text-primary)] hover:bg-[color:var(--surface-2)]'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="sub-header-right">
          <button
            className="p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors rounded hover:bg-[color:var(--surface-2)] flex items-center gap-1"
            onClick={copyPrompt}
            title="Copy agent prompt"
          >
            <IconCopy />
            <span className="text-[10px] font-bold uppercase tracking-widest">{copied ? 'Copied!' : 'Prompt'}</span>
          </button>
          {activeTab === 'records' && hasCreate && (
            <button className="btn-primary" onClick={() => setCreateOpen(true)}>
              {createAction?.label || `New ${config.label}`}
            </button>
          )}
        </div>
        {activeTab === 'records' && (
          <div style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)' }}>
            <input
              className="form-input"
              style={{ width: 240, padding: '4px 10px', fontSize: 12 }}
              placeholder="Search…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
        )}
      </div>

      {/* Actions tab — delegates fully to ApiModule with standalone actions only */}
      {activeTab === 'actions' && hasStandaloneActions && (
        <ApiModule
          config={{ ...config, actions: standaloneActions }}
          notify={notify}
          setRefresh={setRefresh}
        />
      )}

      {/* Records tab */}
      {activeTab === 'records' && (
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* Left: list */}
          <div className="list-pane">
            {/* Filter tabs */}
            {filterValues.length > 0 && (
              <div className="filter-tabs">
                {['all', ...filterValues].map(v => (
                  <button
                    key={v}
                    className={`filter-tab ${filter === v ? 'active' : ''}`}
                    onClick={() => setFilter(v)}
                  >
                    {v}
                  </button>
                ))}
              </div>
            )}
            <div className="list-scroll">
              {loading && <div className="p-4 text-sm text-[color:var(--text-muted)]">Loading…</div>}
              {!loading && filtered.length === 0 && (
                <div className="p-4 text-sm text-[color:var(--text-muted)]">No records found.</div>
              )}
              {filtered.map(rec => {
                const title = rec[titleField] || rec.id;
                const hue = avatarHue(title);
                const badge = badgeField && rec[badgeField]
                  ? <span className="badge badge-neutral">{rec[badgeField]}</span>
                  : null;
                const meta = metaField
                  ? (rec[metaField] ? (shortDate(rec[metaField]) || rec[metaField]) : null)
                  : null;

                return (
                  <ListCard
                    key={rec.id}
                    active={rec.id === selectedId}
                    onClick={() => setSelectedId(rec.id)}
                    avatarStyle={{ backgroundColor: `hsl(${hue}, 50%, 30%)`, color: '#fff' }}
                    avatarContent={initials(title)}
                    title={title}
                    subtitle={subtitleField ? (rec[subtitleField] || '—') : '—'}
                    meta={meta}
                    badge={badge}
                  />
                );
              })}
            </div>
          </div>

          {/* Right: detail */}
          <div className="detail-pane">
            <DynamicDetailPane
              config={config}
              record={selectedRecord}
              saving={saving}
              onPatch={handlePatch}
              onDelete={() => setConfirmDeleteId(selectedId)}
              onAction={handleAction}
              onEdit={() => setEditOpen(true)}
            />
          </div>
        </div>
      )}

      {/* Modals */}
      {createOpen && (
        <DynamicForm
          fields={config.fields || []}
          onSubmit={handleCreate}
          onClose={() => setCreateOpen(false)}
          saving={saving}
          title={`New ${config.label}`}
        />
      )}
      {editOpen && selectedRecord && (
        <DynamicForm
          fields={config.fields || []}
          initial={selectedRecord}
          onSubmit={async (data) => { await handlePatch(data); setEditOpen(false); }}
          onClose={() => setEditOpen(false)}
          saving={saving}
          title={`Edit ${selectedRecord[titleField] || 'Record'}`}
        />
      )}
      {confirmDeleteId && (
        <ConfirmDialog
          message="Delete this record? This cannot be undone."
          onConfirm={() => handleDelete(confirmDeleteId)}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </div>
  );
}
