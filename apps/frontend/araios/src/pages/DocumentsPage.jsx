import { useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import { api } from '../lib/api';
import ListCard from '../components/ListCard';
import { IconDocument } from '../components/Icons';

function timeAgo(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString('en', { month: 'short', day: 'numeric' });
}

export default function DocumentsPage({ notify, setRefresh }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeSlug, setActiveSlug] = useState(null);
  const [activeDoc, setActiveDoc] = useState(null);
  const [tagFilter, setTagFilter] = useState('');

  const loadList = async (silent = false) => {
    try {
      if (!silent) setLoading(true);
      const url = tagFilter ? `/api/documents?tag=${encodeURIComponent(tagFilter)}` : '/api/documents';
      const data = await api(url);
      setDocuments(data.documents || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  const loadDoc = async (slug) => {
    try {
      const data = await api(`/api/documents/${slug}`);
      setActiveDoc(data);
      setActiveSlug(slug);
    } catch { notify('Access denied to data node', 'warn'); }
  };

  useEffect(() => { setRefresh(loadList); }, []);

  useEffect(() => { loadList(); }, [tagFilter]);

  const allTags = [...new Set(documents.flatMap((d) => d.tags || []))].sort();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div className="sub-header">
        <div className="sub-header-left">
           <div className={`stat-chip ${!tagFilter ? 'active' : ''}`} onClick={() => setTagFilter('')}>
              <span className="stat-label">All Nodes</span>
              <strong className="stat-value">{documents.length}</strong>
           </div>
           {allTags.map(t => (
             <div key={t} className={`stat-chip ${tagFilter === t ? 'active' : ''}`} onClick={() => setTagFilter(t)}>
                <span className="stat-label">{t}</span>
             </div>
           ))}
        </div>
      </div>

      <div className="triage-layout">
        {/* Artifact List */}
        <section className="list-pane">
          {loading ? (
            <div className="flex items-center justify-center h-full text-[color:var(--text-muted)]">Scanning artifacts...</div>
          ) : (
            <div className="lead-list">
              {documents.map(doc => (
                <ListCard
                  key={doc.id}
                  active={activeSlug === doc.slug}
                  onClick={() => loadDoc(doc.slug)}
                  avatarStyle={{ backgroundColor: '#eff6ff', color: '#3b82f6' }}
                  avatarContent={<IconDocument size={16} />}
                  title={doc.title}
                  subtitle={`/${doc.slug}`}
                  meta={`v${doc.version}`}
                  badge={<span className="row-date">{timeAgo(doc.updatedAt)}</span>}
                />
              ))}
            </div>
          )}
        </section>

        {/* Artifact Content */}
        <aside className="detail-pane">
          {!activeDoc ? (
            <div className="flex items-center justify-center h-full opacity-50">Select artifact to decrypt</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
              <div className="detail-hero">
                <div className="detail-avatar">
                  <IconDocument size={24} />
                </div>
                <div className="flex-1">
                  <h2 className="detail-name">{activeDoc.title}</h2>
                  <p className="text-sm text-[color:var(--text-secondary)] font-mono">ID: {activeDoc.slug}</p>
                </div>
                <div className="flex flex-col items-end">
                   <span className="text-[10px] font-bold text-[color:var(--text-muted)] uppercase">Last Edit</span>
                   <span className="text-xs font-bold">{activeDoc.lastEditedBy}</span>
                </div>
              </div>
              <div className="detail-content" style={{ padding: '40px' }}>
                <article className="prose prose-invert max-w-none">
                   <Markdown>{activeDoc.content}</Markdown>
                </article>
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
