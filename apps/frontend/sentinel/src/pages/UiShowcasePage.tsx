import { useState, type ReactNode } from 'react';
import { Menu, MenuItem } from '@mui/material';
import { Activity, Check, ChevronDown, Copy, File, GitBranch, Globe, Plus, RefreshCw, Search, Settings2, Terminal, X } from 'lucide-react';
import { AppShell } from '../components/AppShell';
import { Logo } from '../components/ui/Logo';
import { StatusChip } from '../components/ui/StatusChip';
import '../components/session/chat-header.css';
import './ui-showcase.css';

function Section({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return <section className="showcase-section"><header><h2>{title}</h2><p>{subtitle}</p></header>{children}</section>;
}

export function UiShowcasePage() {
  const [view, setView] = useState(0);
  const [windowIndex, setWindowIndex] = useState(0);
  const [changed, setChanged] = useState(false);
  const [query, setQuery] = useState('');
  const [menu, setMenu] = useState<HTMLButtonElement | null>(null);
  const [filter, setFilter] = useState('All types');
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState('Browser');
  return <AppShell title="Showcase" subtitle="Design system" contentClassName="ui-showcase">
    <div className="showcase-intro"><Logo size={32} /><div><h1>Sentinel design system</h1><p>Quiet surfaces, compact controls, and actions placed with their content.</p></div></div>
    <div className="showcase-grid">
      <Section title="Surfaces" subtitle="A shared canvas, subtle raised controls, and inset selection.">
        <div className="showcase-swatches">{[['Canvas', 'var(--app-bg)'], ['Raised', 'var(--showcase-face)'], ['Inset track', 'var(--surface-2)']].map(([name, color]) => <div key={name}><span style={{ background:color }} /><strong>{name}</strong></div>)}</div>
        <p className="showcase-note">Soft shadows and faint edges. Blue marks selection; amber marks cost and warnings.</p>
      </Section>
      <Section title="Pane controls" subtitle="One control bar. CAPS action labels, familiar icons, restrained hover states.">
        <div className="chat-header-actions showcase-controls"><Activity size={16} /><span className="chat-header-pill"><i className="showcase-dot" />LIVE</span><button className="chat-header-pill" onClick={() => setNotice('Settings preview selected')}><Settings2 size={13} />RUN SETTINGS</button><button className="chat-header-pill showcase-icon" title="Refresh preview" aria-label="Refresh preview" onClick={() => setNotice('Preview refreshed')}><RefreshCw size={14} /></button><button className="chat-header-pill" aria-haspopup="menu" aria-expanded={Boolean(menu)} onClick={event => setMenu(event.currentTarget)}>{filter}<ChevronDown size={12} /></button><button className="showcase-primary" onClick={() => setNotice('Run preview selected')}>RUN</button></div>
        <Menu open={Boolean(menu)} anchorEl={menu} onClose={() => setMenu(null)} disableScrollLock slotProps={{ paper:{ className:'showcase-menu' } }}>
          {['All types', 'Cron', 'Webhook', 'Heartbeat'].map(type => <MenuItem key={type} selected={filter === type} onClick={() => { setFilter(type); setMenu(null); }}><span>{type}</span>{filter === type && <Check size={13} />}</MenuItem>)}
        </Menu>
        <p className="showcase-note" role="status">{notice || 'Interactive examples only; these controls do not run workspace actions.'}</p>
      </Section>
      <Section title="Selection & tabs" subtitle="Inset view switches and a light underline for terminal windows.">
        <div className="showcase-segments" role="group" aria-label="Example browser view"><span className="showcase-segment-indicator" style={{ transform:`translateX(${view * 100}%)` }} />{['Files', 'Changes', 'History'].map((label, index) => <button key={label} aria-pressed={view === index} onClick={() => setView(index)}>{label}</button>)}</div>
        <div className="showcase-window-tabs" role="tablist" aria-label="Example terminal windows">{['main', 'bash', 'server'].map((label,index) => <button key={label} role="tab" aria-selected={windowIndex === index} onClick={() => setWindowIndex(index)}><small>0{index + 1}</small>{label}</button>)}<span style={{ transform:`translateX(${windowIndex * 100}%)` }} /></div>
        <div className="showcase-terminal"><Terminal size={13} />cache <span>❯</span></div>
      </Section>
      <Section title="Search & local actions" subtitle="Search owns its focus outline. Small content actions sit beside it.">
        <div className="showcase-search-row"><label className="showcase-search"><Search size={14} /><input aria-label="Example file search" placeholder="Find a file…" value={query} onChange={event => setQuery(event.target.value)} />{query && <button aria-label="Clear example search" onClick={() => setQuery('')}><X size={12} /></button>}</label><button className="showcase-local" aria-label="Changed files only" aria-pressed={changed} title="Changed files only" onClick={() => setChanged(!changed)}><GitBranch size={15} /></button><button className="showcase-local" aria-label="Add example item" title="Add item" onClick={() => setNotice('Add item preview selected')}><Plus size={16} /></button></div>
        <label className="showcase-field">DESCRIPTION<textarea placeholder="A short description…" rows={3} /></label>
      </Section>
      <Section title="Catalog & selection" subtitle="Readable descriptions, soft selection, and room for a separate scrollbar.">
        <div className="showcase-catalog">{['Browser', 'Computer', 'Runtime', 'Memory', 'Triggers', 'Tasks'].map(label => <button className="menu-selection-item showcase-catalog-item" key={label} aria-pressed={selected === label} onClick={() => setSelected(label)}><Globe size={15} /><span><strong>{label}</strong><small>Browse available actions and configuration.</small></span></button>)}</div>
      </Section>
      <Section title="Compact diagnostics" subtitle="Keep usage values visible, with secondary pricing details on demand.">
        <article className="showcase-event"><header><strong>ASSISTANT STEP</strong><StatusChip label="LATEST" tone="good" /><time>09:16:26</time></header><p>Checked three tools successfully.</p><div className="showcase-model">gpt-5.6-luna <span>Codex OAuth</span></div><dl className="showcase-metrics">{[['Input','14,184'],['Output','27'],['Total','14,211'],['Cached input','13,056'],['Cache writes','0'],['Reasoning','0']].map(([label,value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><div className="showcase-cost"><span>API-equivalent token cost</span><span>$0.00051912</span></div><details><summary>Usage & pricing details</summary><p>Illustrative values. Cache reads are included in input. API-equivalent pricing is not a subscription charge.</p></details></article>
      </Section>
      <Section title="Content & empty states" subtitle="Icon, title, and subtitle form one aligned group.">
        <div className="showcase-welcome"><File size={26} /><div><h3>Disposable</h3><p>Open a file or review what changed.</p></div></div>
        <div className="showcase-empty"><GitBranch size={24} /><div><h3>No Git history</h3><p>This folder isn’t a Git repository.</p></div></div>
      </Section>
      <Section title="Status & feedback" subtitle="Neutral empty states. Semantic color for status, warning, and failure.">
        <div className="showcase-status"><StatusChip label="RUNNING" tone="good" /><StatusChip label="WARNING" tone="warn" /><StatusChip label="FAILED" tone="danger" /><StatusChip label="CONNECTED" tone="info" /><StatusChip label="IDLE" /></div>
        <div className="showcase-motion"><Copy size={24} /><div><h3>Motion and interaction</h3><p>Short fades and slides, subtle hover feedback, and visible keyboard focus.</p><span className="showcase-motion-note">Respects reduced motion</span></div></div>
      </Section>
    </div>
  </AppShell>;
}
