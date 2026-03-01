import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { initials, avatarHue, shortDate } from '../lib/utils';
import ListCard from './ListCard';
import ConfirmDialog from './ConfirmDialog';
import DynamicDetailPane from './DynamicDetailPane';
import DynamicForm from './DynamicForm';
import PageModule from './PageModule';
import ApiModule from './ApiModule';
import { IconSearch, IconCopy } from './Icons';

function buildDataPrompt(config) {
  const BASE_URL = window.location.origin;
  const base = `${BASE_URL}/api/modules/${config.name}`;
  const fields = (config.fields || []).map(f => `${f.key}${f.required ? '*' : ''} (${f.type}${f.options ? ': ' + f.options.join('|') : ''})`).join(', ');
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
  return lines.join('\n');
}

/**
 * Generic config-driven triage page for any module.
 * Routes to PageModule or ApiModule based on config.type.
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
    loadAll();
    const timer = setInterval(() => loadAll(true), 30000);
    return () => clearInterval(timer);
  }, [moduleName]); // eslint-disable-line react-hooks/exhaustive-deps

  // Filter/search records
  const filteredRecords = useMemo(() => {
    if (!config) return [];
    const { titleField, subtitleField, filterField } = config.list_config || {};
    const term = search.trim().toLowerCase();
    return records.filter(r => {
      const hay = [r[titleField], r[subtitleField]].filter(Boolean).join(' ').toLowerCase();
      const matchSearch = !term || hay.includes(term);
      const matchFilter = filter === 'all' || !filterField || r[filterField] === filter;
      return matchSearch && matchFilter;
    });
  }, [records, config, search, filter]);

  const selectedRecord = filteredRecords.find(r => r.id === selectedId) || filteredRecords[0] || null;

  // Build filter chips from filterField options
  const filterOptions = useMemo(() => {
    if (!config?.list_config?.filterField) return [];
    const field = (config.fields || []).find(f => f.key === config.list_config.filterField);
    return field?.options || [];
  }, [config]);

  // Count by filter value
  const filterCounts = useMemo(() => {
    if (!config?.list_config?.filterField) return {};
    const ff = config.list_config.filterField;
    return records.reduce((acc, r) => {
      const v = r[ff] || 'other';
      acc[v] = (acc[v] || 0) + 1;
      return acc;
    }, {});
  }, [records, config]);

  // CRUD handlers
  const createRecord = useCallback(async (data) => {
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/records`, {
        method: 'POST',
        body: JSON.stringify(data),
      });
      setCreateOpen(false);
      notify('Record created');
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Could not create record', 'warn');
    } finally {
      setSaving(false);
    }
  }, [moduleName, notify, loadAll]);

  const patchRecord = useCallback(async (patch) => {
    if (!selectedRecord) return;
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/records/${selectedRecord.id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
      notify('Saved');
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Could not save', 'warn');
    } finally {
      setSaving(false);
    }
  }, [moduleName, selectedRecord, notify, loadAll]);

  const editRecord = useCallback(async (data) => {
    if (!selectedRecord) return;
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/records/${selectedRecord.id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      });
      setEditOpen(false);
      notify('Record updated');
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Could not update record', 'warn');
    } finally {
      setSaving(false);
    }
  }, [moduleName, selectedRecord, notify, loadAll]);

  const deleteRecord = useCallback(async (id) => {
    try {
      setSaving(true);
      await api(`/api/modules/${moduleName}/records/${id}`, { method: 'DELETE' });
      notify('Record deleted');
      if (selectedId === id) setSelectedId(null);
      await loadAll(true);
    } catch (err) {
      notify(err.message || 'Could not delete', 'warn');
    } finally {
      setSaving(false);
    }
  }, [moduleName, notify, loadAll, selectedId]);

  const runAction = useCallback(async (actionId) => {
    if (!selectedRecord) return;
    try {
      const res = await api(`/api/modules/${moduleName}/records/${selectedRecord.id}/action/${actionId}`, {
        method: 'POST',
      });
      notify(res?.message || 'Action executed');
    } catch (err) {
      notify(err.message || 'Action failed', 'warn');
    }
  }, [moduleName, selectedRecord, notify]);

  if (!config && loading) {
    return (
      <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">
        Loading {moduleName}...
      </div>
    );
  }

  if (!config) return null;

  // Route to specialised renderers based on module type
  if (config.type === 'page') {
    return <PageModule config={config} notify={notify} setRefresh={setRefresh} />;
  }
  if (config.type === 'tool') {
    return <ApiModule config={config} notify={notify} setRefresh={setRefresh} />;
  }

  const { titleField, subtitleField, badgeField, metaField } = config.list_config || {};
  const hasCreate = (config.actions || []).some(a => a.type === 'create');
  const createAction = (config.actions || []).find(a => a.type === 'create');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Search bar */}
      <div className="top-bar-actions-row">
        <div className="top-search-wrap">
          <IconSearch />
          <input
            className="search-input"
            placeholder={`Search ${config.label}...`}
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Sub-header: filter chips + CTA */}
      <div className="sub-header">
        <div className="sub-header-left">
          <div
            className={`stat-chip ${filter === 'all' ? 'active' : ''}`}
            onClick={() => setFilter('all')}
          >
            <span className="stat-label">All</span>
            <strong className="stat-value">{records.length}</strong>
          </div>
          {filterOptions.map(opt => (
            <div
              key={opt}
              className={`stat-chip ${filter === opt ? 'active' : ''}`}
              onClick={() => setFilter(opt)}
            >
              <span className="stat-label">{opt}</span>
              <strong className="stat-value">{filterCounts[opt] || 0}</strong>
            </div>
          ))}
        </div>
        <div className="sub-header-right" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            className="p-1.5 text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)] transition-colors rounded hover:bg-[color:var(--surface-2)] flex items-center gap-1"
            onClick={copyPrompt}
            title="Copy agent prompt"
          >
            <IconCopy />
            <span className="text-[10px] font-bold uppercase tracking-widest">{copied ? 'Copied!' : 'Prompt'}</span>
          </button>
          {hasCreate && (
            <button className="btn-primary" onClick={() => setCreateOpen(true)}>
              {createAction?.label || `New ${config.label}`}
            </button>
          )}
        </div>
      </div>

      {/* Triage layout */}
      <div className="triage-layout">
        {/* List pane */}
        <section className="list-pane">
          {loading ? (
            <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">
              Loading...
            </div>
          ) : filteredRecords.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-[color:var(--text-muted)] gap-2 opacity-50">
              <span style={{ fontSize: '2rem' }}>◈</span>
              <p className="text-sm font-medium">No records match filters</p>
            </div>
          ) : (
            <div className="lead-list">
              {filteredRecords.map(record => {
                const isActive = selectedRecord?.id === record.id;
                const title = record[titleField] || record.id;
                const hue = avatarHue(title);
                const badge = badgeField && record[badgeField]
                  ? <span className="badge badge-neutral">{record[badgeField]}</span>
                  : null;
                const meta = metaField ? (record[metaField] ? shortDate(record[metaField]) || record[metaField] : null) : null;

                return (
                  <ListCard
                    key={record.id}
                    active={isActive}
                    onClick={() => setSelectedId(record.id)}
                    avatarStyle={{ backgroundColor: `hsl(${hue}, 50%, 30%)`, color: '#fff' }}
                    avatarContent={initials(title)}
                    title={title}
                    subtitle={subtitleField ? (record[subtitleField] || '—') : '—'}
                    meta={meta}
                    badge={badge}
                  />
                );
              })}
            </div>
          )}
        </section>

        {/* Detail pane */}
        <aside className="detail-pane">
          <DynamicDetailPane
            config={config}
            record={selectedRecord}
            saving={saving}
            onPatch={patchRecord}
            onDelete={() => setConfirmDeleteId(selectedRecord?.id)}
            onAction={runAction}
            onEdit={() => setEditOpen(true)}
          />
        </aside>
      </div>

      {/* Create modal */}
      {createOpen && (
        <DynamicForm
          title={createAction?.label || `New ${config.label}`}
          fields={config.fields || []}
          saving={saving}
          onSubmit={createRecord}
          onClose={() => setCreateOpen(false)}
        />
      )}

      {/* Edit modal */}
      {editOpen && selectedRecord && (
        <DynamicForm
          title={`Edit ${config.label}`}
          fields={config.fields || []}
          initial={selectedRecord}
          saving={saving}
          onSubmit={editRecord}
          onClose={() => setEditOpen(false)}
        />
      )}

      {/* Delete confirmation */}
      {confirmDeleteId && (
        <ConfirmDialog
          title={`Delete ${config.label}`}
          message="Are you sure? This cannot be undone."
          onConfirm={() => { deleteRecord(confirmDeleteId); setConfirmDeleteId(null); }}
          onCancel={() => setConfirmDeleteId(null)}
        />
      )}
    </div>
  );
}
