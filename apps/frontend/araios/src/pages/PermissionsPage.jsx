import { useEffect, useState } from 'react';
import { api } from '../lib/api';

const LEVELS = ['allow', 'approval', 'deny'];

function groupByResource(permissions) {
  const groups = {};
  for (const p of permissions) {
    const dot = p.action.indexOf('.');
    const resource = dot > 0 ? p.action.slice(0, dot) : p.action;
    if (!groups[resource]) groups[resource] = [];
    groups[resource].push(p);
  }
  return groups;
}

export default function PermissionsPage({ notify, setRefresh }) {
  const [permissions, setPermissions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [updatingAction, setUpdatingAction] = useState('');
  const [search, setSearch] = useState('');

  const load = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const data = await api('/api/permissions');
      setPermissions(data.permissions || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  useEffect(() => { setRefresh(load); }, []);

  useEffect(() => { load(); }, []);

  const handleToggle = async (action, newLevel) => {
    try {
      setUpdatingAction(action);
      await api(`/api/permissions/${action}`, {
        method: 'PATCH',
        body: JSON.stringify({ level: newLevel }),
      });
      setPermissions((prev) =>
        prev.map((p) => (p.action === action ? { ...p, level: newLevel } : p))
      );
      notify(`Protocol override: ${action} -> ${newLevel}`);
    } catch (err) {
      notify(err.message || 'Override failure', 'warn');
    } finally {
      setUpdatingAction('');
    }
  };

  const filtered = search.trim()
    ? permissions.filter(p => p.action.toLowerCase().includes(search.toLowerCase()))
    : permissions;
  const groups = groupByResource(filtered);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="sub-header" style={{ position: 'relative' }}>
        <div className="sub-header-left">
           <div className="stat-chip active">
              <span className="stat-label">Active Policies</span>
              <strong className="stat-value">{permissions.length}</strong>
           </div>
        </div>
        <div style={{ position: 'absolute', left: '50%', transform: 'translateX(-50%)' }}>
          <input
            className="form-input"
            style={{ width: 260, padding: '4px 10px', fontSize: 12 }}
            placeholder="Search permissions…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      <div className="detail-content" style={{ padding: '24px' }}>
        {loading ? (
          <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">Parsing system manifest...</div>
        ) : (
          <div style={{ maxWidth: '800px', margin: '0 auto', width: '100%', display: 'flex', flexDirection: 'column', gap: '32px' }}>
            {Object.entries(groups).map(([resource, perms]) => (
              <section key={resource} className="space-y-4">
                <h3 className="form-label" style={{ color: 'var(--accent-solid)', borderLeft: '3px solid var(--accent-solid)', paddingLeft: '12px' }}>
                  {resource.toUpperCase()} PROTOCOLS
                </h3>
                <div className="panel overflow-hidden">
                  <div style={{ divideY: '1px solid var(--border-subtle)' }}>
                    {perms.map((p) => (
                      <div key={p.action} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px', borderTop: p !== perms[0] ? '1px solid var(--border-subtle)' : 'none' }}>
                        <span className="text-sm font-mono font-bold">{p.action}</span>
                        <div style={{ display: 'flex', backgroundColor: 'var(--surface-2)', padding: '3px', borderRadius: '8px', border: '1px solid var(--border-subtle)' }}>
                          {LEVELS.map((lvl) => {
                            const active = p.level === lvl;
                            const isUpdating = updatingAction === p.action;
                            return (
                              <button
                                key={lvl}
                                disabled={isUpdating}
                                className={clsx(
                                  "px-4 py-1.5 text-[10px] font-bold uppercase tracking-widest rounded-md transition-all",
                                  active 
                                    ? (lvl === 'allow' ? 'bg-emerald-600 text-white shadow-lg' : 
                                       lvl === 'approval' ? 'bg-white text-black shadow-lg' : 
                                       'bg-rose-600 text-white shadow-lg')
                                    : 'text-[color:var(--text-muted)] hover:text-[color:var(--text-primary)]'
                                )}
                                onClick={() => !active && handleToggle(p.action, lvl)}
                              >
                                {lvl}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Add clsx helper locally since it's used
function clsx(...args) {
  return args.filter(Boolean).join(' ');
}
