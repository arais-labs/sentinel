import { FilesPaneControls } from './FilesPaneControls';
import { usePaneState } from './usePaneState';
import { usePaneId } from '../../lib/workspace-context';
import { ReviewFileTree } from './ReviewFileTree';
import { ReviewChecks } from './ReviewChecks';
import { ExplorerSidebar } from './ExplorerSidebar';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, CheckCheck, ChevronDown, List, ListTree, FileCode2, GitPullRequest, Loader2, MessageSquare, PanelLeftClose, PanelLeftOpen, RefreshCw, Search, Send, X } from 'lucide-react';
import { api } from '../../lib/api';
import { DiffViewer } from '../workbench/DiffViewer';
import { Markdown } from '../ui/Markdown';
import { streamReview } from './reviewTransport';
import './workspace-browser.css';
import './pull-request-review.css';

type User = { login: string };
type Pull = { number: number; title: string; body: string; html_url: string; state: string; draft: boolean; merged: boolean; user: User; head: { sha: string; ref: string }; base: { ref: string }; additions: number; deletions: number; changed_files: number };
type ChangedFile = { filename: string; previous_filename?: string; status: string; additions: number; deletions: number; patch?: string; sha: string };
type Comment = { id: number; body: string; user: User; path?: string; line?: number; side?: 'LEFT' | 'RIGHT'; created_at?: string; state?: string };
type DraftComment = { path: string; line: number; side: 'LEFT' | 'RIGHT'; body: string };
type CheckRun = { id: number; name?: string; context?: string; status?: string; conclusion?: string; state?: string };
type InboxItem = { number: number; title: string; html_url: string; user: User; draft: boolean };
type Draft = { body: string; comments: DraftComment[]; viewed: string[] };
const blankDraft = (): Draft => ({ body: '', comments: [], viewed: [] });
const readDraft = (key: string): Draft => { try { return { ...blankDraft(), ...JSON.parse(localStorage.getItem(key) ?? '{}') }; } catch { return blankDraft(); } };
const patchFor = (file: ChangedFile) => `diff --git a/${file.previous_filename ?? file.filename} b/${file.filename}\n--- ${file.status === 'added' ? '/dev/null' : `a/${file.previous_filename ?? file.filename}`}\n+++ ${file.status === 'removed' ? '/dev/null' : `b/${file.filename}`}\n${file.patch}\n`;

export function PullRequestReview({ instance }: { instance: string }) {
  const paneId = usePaneId();
  const stateKey = `pr-pane:${instance}:${paneId ?? "default"}:`;
  const [accounts, setAccounts] = useState<{ id: string; name: string; has_token: boolean }[]>([]);
  const [account, setAccount] = usePaneState(stateKey + "account", '');
  const [url, setUrl] = usePaneState(stateKey + "url", '');
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [inboxView, setInboxView] = usePaneState<'requested' | 'created'>(stateKey + "inboxView", 'requested');
  const [inboxBusy, setInboxBusy] = useState(false);
  const [inboxLimited, setInboxLimited] = useState(false);
  const [pull, setPull] = usePaneState<Pull | null>(stateKey + "pull", null);
  const [files, setFiles] = usePaneState<ChangedFile[]>(stateKey + "files", []);
  const [comments, setComments] = usePaneState<Comment[]>(stateKey + "comments", []);
  const [activity, setActivity] = usePaneState<Comment[]>(stateKey + "activity", []);
  const [checks, setChecks] = usePaneState<CheckRun[]>(stateKey + "checks", []);
  const [selected, setSelected] = usePaneState(stateKey + "selected", '');
  const [filter, setFilter] = usePaneState(stateKey + "filter", '');
  const [view, setView] = usePaneState<'files' | 'activity' | 'checks'>(stateKey + "view", 'files');
  const [split, setSplit] = usePaneState(stateKey + "split", false);
  const [catalogOpen, setCatalogOpen] = usePaneState(stateKey + 'catalogOpen', true);
  const [sidebarWidth, setSidebarWidth] = usePaneState(stateKey + 'sidebarWidth', 340);
  const [tree, setTree] = usePaneState(stateKey + 'tree', true);
  const [sideOpen, setSideOpen] = usePaneState(stateKey + "sideOpen", true);
  const [narrow, setNarrow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loaded, setLoaded] = usePaneState(stateKey + "loaded", false);
  const [progress, setProgress] = useState({ label: '', step: 0, total: 5 });
  const [error, setError] = useState('');
  const [warnings, setWarnings] = usePaneState<string[]>(stateKey + "warnings", []);
  const [draft, setDraft] = usePaneState<Draft>(stateKey + "draft", blankDraft);
  const [draftKey, setDraftKey] = usePaneState(stateKey + "draftKey", '');
  const [reviewOpen, setReviewOpen] = useState(false);
  const [verdict, setVerdict] = usePaneState<'COMMENT' | 'APPROVE' | 'REQUEST_CHANGES'>(stateKey + "verdict", 'COMMENT');
  const [anchor, setAnchor] = usePaneState<{ line: number; side: 'LEFT' | 'RIGHT' } | null>(stateKey + "anchor", null);
  const [commentBody, setCommentBody] = usePaneState(stateKey + "commentBody", '');
  const [success, setSuccess] = useState('');
  const host = useRef<HTMLDivElement>(null);
  const codeScroll = useRef<HTMLDivElement>(null);
  const fileScroll = useRef<HTMLDivElement>(null);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const base = `/instances/${encodeURIComponent(instance)}/git`;
  useEffect(() => {
    try {
      if (codeScroll.current) codeScroll.current.scrollTop = Number(sessionStorage.getItem(stateKey + 'scroll:' + selected) ?? 0);
      if (fileScroll.current) fileScroll.current.scrollTop = Number(sessionStorage.getItem(stateKey + 'treeScroll') ?? 0);
    } catch { /* Scroll recovery is optional. */ }
  }, [stateKey, selected, view]);
  const rememberScroll = (key: string, value: number) => { try { sessionStorage.setItem(stateKey + key, String(value)); } catch { /* Optional persistence. */ } };

  useEffect(() => { const observer = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < 850)); if (host.current) observer.observe(host.current); return () => observer.disconnect(); }, []);
  useEffect(() => { let active = true; api.get<{ items: typeof accounts }>(`${base}/accounts`).then(data => { if (active) { const usable = data.items.filter(a => a.has_token); setAccounts(usable); if (usable.length === 1) setAccount(usable[0].id); } }).catch(e => { if (active) setError(e.message); }); return () => { active = false; generation.current++; controller.current?.abort(); }; }, [base]);

  useEffect(() => { if (!draftKey) return; try { localStorage.setItem(draftKey, JSON.stringify(draft)); } catch { /* Draft remains available for this visit. */ } }, [draft, draftKey]);
  useEffect(() => {
    if (!account) return;
    let active = true; setInboxBusy(true); setError('');
    api.get<{ items: InboxItem[]; limited: boolean }>(`${base}/reviews/inbox?account_id=${account}&view=${inboxView}`).then(data => { if (active) { setInbox(data.items); setInboxLimited(data.limited); } }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setInboxBusy(false); });
    return () => { active = false; };
  }, [base, account, inboxView]);

  const load = useCallback(async (target: string) => {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    const current = ++generation.current;
    setBusy(true); setLoaded(false); setError(''); setSuccess(''); setWarnings([]); setFiles([]); setComments([]); setActivity([]); setChecks([]); setPull(null); setSelected(''); setAnchor(null); setReviewOpen(false); setUrl(target);
    try {
      await streamReview(instance, 'load', { url: target.trim(), account_id: account || undefined }, event => {
        if (generation.current !== current) return;
        if (event.event === 'progress') setProgress({ label: event.label!, step: event.step!, total: event.total! });
        if (event.event === 'pull') { const pr = event.data as Pull; setPull(pr); setSideOpen(true); setCatalogOpen(false); const key = `sentinel.review:${instance}:${event.account_id}:${pr.html_url}:${pr.head.sha}`; setDraft(readDraft(key)); setDraftKey(key); }
        if (event.event === 'files') { const batch = event.data as ChangedFile[]; setFiles(value => [...value, ...batch]); setSelected(value => value || batch[0]?.filename || ''); }
        if (event.event === 'comments') setComments(value => [...value, ...event.data as Comment[]]);
        if (event.event === 'activity' || event.event === 'reviews') setActivity(value => [...value, ...event.data as Comment[]]);
        if (event.event === 'checks' || event.event === 'statuses') setChecks(value => [...value, ...event.data as CheckRun[]]);
        if (event.event === 'warning') setWarnings(value => [...value, event.message!]);
        if (event.event === 'complete') setLoaded(true);
      }, abort.signal);
    } catch (e) { if (!abort.signal.aborted && generation.current === current) setError(e instanceof Error ? e.message : 'Unable to load review'); }
    finally { if (generation.current === current) setBusy(false); }
  }, [instance, account]);

  const submit = async () => {
    if (!pull || submitting) return;
    setSubmitting(true); setError('');
    try {
      await streamReview(instance, 'submit', { url: pull.html_url, account_id: account || undefined, commit_id: pull.head.sha, event: verdict, body: draft.body, comments: draft.comments }, event => {
        if (event.event === 'progress') setProgress({ label: event.label!, step: event.step!, total: event.total! });
        if (event.event === 'submitted') { setSuccess('Review submitted to GitHub'); const cleared = { ...draft, body: '', comments: [] }; try { localStorage.setItem(draftKey, JSON.stringify(cleared)); } catch { /* Storage may be unavailable. */ } setDraft(cleared); setReviewOpen(false); }
      });
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not submit review'); }
    finally { setSubmitting(false); }
  };
  const file = files.find(f => f.filename === selected);
  const filtered = files.filter(f => f.filename.toLowerCase().includes(filter.toLowerCase()));
  const viewedCount = files.filter(f => draft.viewed.includes(`${f.filename}:${f.sha}`)).length;
  const fileComments = comments.filter(c => c.path === selected);
  const fileDrafts = draft.comments.filter(c => c.path === selected);
  const annotations = useMemo(() => [...fileComments.filter(c => c.line).map(c => ({ side: c.side === 'LEFT' ? 'deletions' as const : 'additions' as const, lineNumber: c.line!, metadata: <div className="pr-inline-comment"><strong>{c.user.login}</strong><Markdown content={c.body} /></div> })), ...fileDrafts.map((c, i) => ({ side: c.side === 'LEFT' ? 'deletions' as const : 'additions' as const, lineNumber: c.line, metadata: <div className="pr-inline-comment pr-draft"><strong>Pending review</strong><p>{c.body}</p><button onClick={() => setDraft(d => ({ ...d, comments: d.comments.filter(item => item !== c) }))}>Remove comment {i + 1}</button></div> }))], [fileComments, fileDrafts]);
  const changeFile = (path: string) => { setSelected(path); setAnchor(null); setCommentBody(''); if (narrow) setSideOpen(false); };
  const backToInbox = () => {
    controller.current?.abort(); generation.current++;
    setBusy(false); setPull(null); setLoaded(false); setCatalogOpen(true); setReviewOpen(false);
  };
  const inboxLink = <button className="chat-header-pill pr-back-button" disabled={submitting} onClick={backToInbox}><ArrowLeft size={14} /><span>Pull requests</span></button>;
  const toggledViewed = () => { if (!file) return; const key = `${file.filename}:${file.sha}`; setDraft(d => ({ ...d, viewed: d.viewed.includes(key) ? d.viewed.filter(k => k !== key) : [...d.viewed, key] })); };
  const addComment = () => { if (!anchor || !commentBody.trim()) return; setDraft(d => ({ ...d, comments: [...d.comments, { path: selected, ...anchor, body: commentBody.trim() }] })); setAnchor(null); setCommentBody(''); };

  return <div ref={host} className={`pr-review pr-with-catalog ${narrow ? 'pr-narrow' : ''} ${pull ? 'pr-has-selection' : ''} ${catalogOpen ? 'pr-catalog-open' : ''}`}>
    {!catalogOpen && !pull && <div className="project-explorer-edge"><button className="project-icon-button" aria-label="Show pull requests" onClick={() => setCatalogOpen(true)}><PanelLeftOpen size={16} /></button></div>}
    <ExplorerSidebar className="pr-catalog-shell" open={!pull && catalogOpen} width={300} overlay={narrow && !!pull}>
    <aside className="pr-lobby" aria-label="Pull requests">
      <header className="project-explorer-heading"><span>PULL REQUESTS</span><button className="project-icon-button" aria-label="Hide pull requests" onClick={() => setCatalogOpen(false)}><PanelLeftClose size={16} /></button></header>
      <nav className="project-view-tabs pr-inbox-tabs" aria-label="Pull request filter"><span aria-hidden="true" className="project-view-indicator" style={{ transform: `translateX(${inboxView === 'requested' ? 0 : 100}%)` }} /><button aria-pressed={inboxView === 'requested'} onClick={() => setInboxView('requested')}>For review</button><button aria-pressed={inboxView === 'created'} onClick={() => setInboxView('created')}>Created by me</button></nav>
      <form className="project-search-row pr-url-row" onSubmit={e => { e.preventDefault(); void load(url); }}><div className="project-search"><Search size={13} /><input aria-label="Pull request URL" placeholder="Open a pull request…" value={url} onChange={e => setUrl(e.target.value)} /><button className="project-icon-button" aria-label="Open review" title="Open review" disabled={!url.trim() || busy || submitting} type="submit">{busy ? <Loader2 className="animate-spin" size={14} /> : <ArrowUpRight size={14} />}</button></div></form>
      <div className="pr-account-row"><select aria-label="GitHub account" value={account} disabled={busy || submitting} onChange={e => { setAccount(e.target.value); setPull(null); setLoaded(false); setInbox([]); }}><option value="">Choose account</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></div>
      {error && <div className="pr-error" role="alert">{error}</div>}
      {!accounts.length && <p className="pr-note">Connect a GitHub account in Settings to start reviewing.</p>}
      {inboxBusy ? <div className="pr-empty"><Loader2 className="animate-spin" size={22} /><p>Finding your pull requests…</p></div> : inbox.length ? <div className="pr-inbox">{inbox.map(item => <button key={item.html_url} aria-current={pull?.html_url === item.html_url ? 'true' : undefined} title={item.title} disabled={submitting} onClick={() => void load(item.html_url)}><GitPullRequest size={14} /><span><strong>{item.title}</strong><small>{item.html_url.split('/').slice(3, 5).join('/')} · #{item.number} · {item.user.login}{item.draft ? ' · Draft' : ''}</small></span></button>)}</div> : account && <div className="pr-empty project-empty"><CheckCheck size={27} /><h3>{inboxView === 'requested' ? 'No reviews waiting' : 'No open pull requests'}</h3><p>You can open any accessible PR using its URL above.</p></div>}
      {inboxLimited && <p className="pr-note">Showing matches from the latest 100 results.</p>}
    </aside></ExplorerSidebar>
    <section className="pr-review-content">{!pull ? <div className="pr-empty pr-selection-empty"><GitPullRequest size={24} /><h3>Select a pull request</h3><p>Choose from the list or open a GitHub URL.</p>{busy && <span role="status">{progress.label || 'Loading pull request…'}</span>}</div> : <>
      <FilesPaneControls>
        {inboxLink}
        <span className="pr-pane-title" title={`${pull.title} · ${pull.head.ref} → ${pull.base.ref}`}><span className="chat-header-pill pr-title-number">{pull.number}</span><span className="pr-title-text">{pull.title}</span><span className={`chat-header-pill pr-title-status ${pull.merged ? 'merged' : pull.state}`}>{pull.merged ? 'Merged' : pull.draft ? 'Draft' : pull.state}</span></span>
        <button className="chat-header-pill" aria-label="Refresh pull request" disabled={busy || submitting} onClick={() => void load(pull.html_url)}><RefreshCw size={14} className={busy ? 'animate-spin' : ''} /></button>
        <button className="chat-header-pill" aria-expanded={reviewOpen} disabled={!loaded || submitting || pull.state !== 'open'} onClick={() => setReviewOpen(v => !v)}><MessageSquare size={13} /><span>Review{draft.comments.length > 0 && ` · ${draft.comments.length}`}</span><ChevronDown size={12} /></button>
      </FilesPaneControls>
      {(busy || submitting) && <div className="pr-progress" role="status" aria-live="polite"><Loader2 size={12} className="animate-spin" /><span>{progress.label}</span><small>{progress.step}/{progress.total}</small><i style={{ width: `${progress.step / progress.total * 100}%` }} /></div>}
      {error && <div className="pr-error" role="alert">{error}</div>}{success && <div className="pr-success" role="status"><Check size={14} />{success}</div>}
      {warnings.length > 0 && <details className="pr-warnings"><summary>{warnings.length} loading notice{warnings.length > 1 ? 's' : ''}</summary>{warnings.map((w, i) => <p key={i}>{w}</p>)}</details>}
      <nav className="pr-navigation"><div className="pr-review-tabs chat-header-actions" role="tablist" aria-label="Review sections"><button className="chat-header-pill" role="tab" aria-selected={view === 'files'} onClick={() => setView('files')}>Changes <span>{files.length}</span></button><button className="chat-header-pill" role="tab" aria-selected={view === 'activity'} onClick={() => setView('activity')}>Discussion <span>{activity.length}</span></button><button className="chat-header-pill" role="tab" aria-selected={view === 'checks'} onClick={() => setView('checks')}>Checks <span>{checks.length}</span></button></div><span className="pr-navigation-path" title={selected}>{view === 'files' ? selected : ''}</span><div className="pr-diff-actions">{view === 'files' && file && (<div className="project-view-tabs pr-diff-toggle" role="group" aria-label="Diff layout"><span aria-hidden="true" className="project-view-indicator" style={{ transform: `translateX(${split && !narrow ? 100 : 0}%)` }} /><button aria-pressed={!split || narrow} onClick={() => setSplit(false)}>Unified</button><button aria-pressed={split && !narrow} disabled={narrow} title={narrow ? 'Widen the pane for split view' : 'Split diff'} onClick={() => setSplit(true)}>Split</button></div>)}<div className="pr-diffstat"><span>+{pull.additions}</span><span>−{pull.deletions}</span></div></div></nav>
      {view === 'files' ? <div className={`pr-workbench ${sideOpen ? '' : 'pr-sidebar-hidden'}`}>
        <ExplorerSidebar open={sideOpen} width={Math.min(sidebarWidth, narrow ? 300 : 480)} overlay={narrow} className="pr-files-shell"><aside className="pr-file-list"><div className="project-explorer-heading pr-tree-toolbar"><div className="pr-file-search project-search"><Search size={13} /><input aria-label="Filter changed files" placeholder="Filter files…" value={filter} onChange={e => setFilter(e.target.value)} /></div><button className="project-icon-button pr-tree-mode" aria-label={tree ? "Switch to list view" : "Switch to tree view"} title={tree ? "Switch to list view" : "Switch to tree view"} onClick={() => setTree(v => !v)}>{tree ? <ListTree size={15} /> : <List size={15} />}</button><button className="project-icon-button" aria-label="Hide changed files" onClick={() => setSideOpen(false)}><PanelLeftClose size={16} /></button></div><div className="pr-file-scroll" ref={fileScroll} onScroll={e => rememberScroll('treeScroll', e.currentTarget.scrollTop)}><ReviewFileTree files={filtered} tree={tree} selected={selected} viewed={draft.viewed} onSelect={changeFile} storageKey={stateKey + 'folders'} />{!filtered.length && <p className="pr-note">{busy ? 'Loading files…' : 'No matching files'}</p>}</div><footer className="pr-tree-footer"><CheckCheck size={12} /><span>{viewedCount} of {files.length} viewed</span><progress aria-label="Review progress" value={viewedCount} max={files.length || 1} /></footer></aside></ExplorerSidebar>{!narrow && sideOpen && <div className="pr-sidebar-resizer" role="separator" aria-label="Resize changed files" aria-orientation="vertical" tabIndex={0} onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); setSidebarWidth(v => Math.max(240, Math.min(480, v + (e.key === 'ArrowRight' ? 16 : -16)))); } }} onPointerDown={e => e.currentTarget.setPointerCapture(e.pointerId)} onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) { const left = host.current?.getBoundingClientRect().left ?? 0; setSidebarWidth(Math.max(240, Math.min(480, e.clientX - left))); } }} onPointerUp={e => e.currentTarget.releasePointerCapture(e.pointerId)} />}
        <main className="pr-code"><div className="pr-file-toolbar"><>{!sideOpen && <button className="project-icon-button" aria-label="Show files" onClick={() => setSideOpen(true)}><PanelLeftOpen size={16} /></button>}</>{file && <><button aria-pressed={draft.viewed.includes(`${file.filename}:${file.sha}`)} onClick={toggledViewed}><Check size={13} />Viewed</button></>}</div>
          <div className="pr-code-scroll" key={selected} ref={codeScroll} onScroll={e => rememberScroll('scroll:' + selected, e.currentTarget.scrollTop)}>{file ? <>{file.previous_filename && <p className="pr-note">Renamed from {file.previous_filename}</p>}{file.patch ? <><DiffViewer diff={patchFor(file)} viewMode={split && !narrow ? 'split' : 'unified'} annotations={annotations} onLineClick={line => { setAnchor({ line: line.lineNumber, side: line.annotationSide === 'deletions' ? 'LEFT' : 'RIGHT' }); setCommentBody(''); }} /></> : <div className="pr-empty"><FileCode2 size={26} /><h3>No text diff available</h3><p>GitHub omitted this patch. It may be binary or too large.</p></div>}
          {anchor && <div className="pr-comment-editor"><header><MessageSquare size={14} />Comment on {anchor.side === 'LEFT' ? 'old' : 'new'} line {anchor.line}<button aria-label="Cancel comment" onClick={() => setAnchor(null)}><X size={14} /></button></header><textarea autoFocus aria-label="Line comment" value={commentBody} placeholder="Leave a specific, actionable comment…" onChange={e => setCommentBody(e.target.value)} /><footer><span>Saved as a draft until you submit</span><button className="pr-primary" disabled={!commentBody.trim()} onClick={addComment}>Add to review</button></footer></div>}
          {(fileComments.length > 0 || fileDrafts.length > 0) && <details className="pr-file-discussion"><summary>File discussion · {fileComments.length + fileDrafts.length}</summary>{fileComments.map(c => <article key={c.id}><strong>{c.user.login} · {c.line ? `line ${c.line}` : 'Outdated comment'}</strong><Markdown content={c.body} /></article>)}{fileDrafts.map((c, i) => <article key={i}><strong>Pending · line {c.line}</strong><p>{c.body}</p><button onClick={() => setDraft(d => ({ ...d, comments: d.comments.filter(item => item !== c) }))}>Remove</button></article>)}</details>}</> : <div className="pr-empty"><FileCode2 size={26} /><p>{busy ? 'Loading changes…' : 'Select a changed file'}</p></div>}</div>
        </main></div> : view === 'activity' ? <div className="pr-details-scroll"><article className="pr-description"><span className="pr-eyebrow">DESCRIPTION</span><Markdown content={pull.body || 'No description provided.'} /></article>{[...activity].sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? '')).map((item, i) => <article className="pr-activity" key={`${item.id}-${i}`}><header><strong>{item.user.login}</strong><span>{item.state?.replaceAll('_', ' ') || 'Comment'}</span></header><Markdown content={item.body || 'No review message.'} /></article>)}</div> : <ReviewChecks instance={instance} url={pull.html_url} account={account} checks={checks} />}

      {reviewOpen && <div className="pr-review-sheet" role="dialog" aria-label="Submit review"><header><div><span className="pr-eyebrow">YOUR REVIEW</span><h3>Finish your review</h3></div><button className="pr-icon" aria-label="Close review" disabled={submitting} onClick={() => setReviewOpen(false)}><X size={17} /></button></header><p>{viewedCount}/{files.length} files viewed · {draft.comments.length} pending line comments</p><textarea aria-label="Review summary" placeholder="Summarize your feedback…" value={draft.body} disabled={submitting} onChange={e => setDraft(d => ({ ...d, body: e.target.value }))} /><div className="pr-verdicts">{(['COMMENT', 'APPROVE', 'REQUEST_CHANGES'] as const).map(v => <label key={v}><input type="radio" name="review-verdict" value={v} checked={verdict === v} disabled={submitting} onChange={() => setVerdict(v)} /><span>{v === 'COMMENT' ? 'Comment' : v === 'APPROVE' ? 'Approve' : 'Request changes'}</span></label>)}</div><footer><small>Posts to GitHub as {accounts.find(a => a.id === account)?.name ?? 'your connected account'}</small><button className="pr-primary" disabled={submitting || !loaded || (verdict === 'REQUEST_CHANGES' && !draft.body.trim()) || (verdict === 'COMMENT' && !draft.body.trim() && !draft.comments.length)} onClick={() => void submit()}>{submitting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}Submit review</button></footer></div>}
    </>}</section>
  </div>;
}
