import {
  BadgeCheck,
  Check,
  Loader2,
  Pencil,
  Send,
  Trash2,
  Users,
  X
} from 'lucide-react';
import { memo } from 'react';
import { formatCompactDate } from '../../lib/format';
import type { Session } from '../../types/api';

export function sessionChannelKind(session: Session): 'default' | 'telegram_group' | 'telegram_dm' {
  const title = (session.title ?? '').trim().toLowerCase();
  if (title.startsWith('tg group ·')) return 'telegram_group';
  if (title.startsWith('tg dm ·')) return 'telegram_dm';
  return 'default';
}

interface SessionRowProps {
  session: Session;
  isActive: boolean;
  onClick: (id: string) => void;
  canDelete: boolean;
  isDeleting: boolean;
  onDelete: (s: Session) => void;
  onSetMain: (s: Session) => void;
  canRename: boolean;
  isEditing: boolean;
  isRenaming: boolean;
  editTitle: string;
  onEditTitleChange: (v: string) => void;
  onSubmitRename: (s: Session) => void;
  onCancelRename: () => void;
  onRename: (s: Session) => void;
  multiSelectMode: boolean;
  selected: boolean;
  onToggleSelect: (id: string) => void;
}

export const SessionRow = memo(({
  session,
  isActive,
  onClick,
  canDelete,
  isDeleting,
  onDelete,
  onSetMain,
  canRename,
  isEditing,
  isRenaming,
  editTitle,
  onEditTitleChange,
  onSubmitRename,
  onCancelRename,
  onRename,
  multiSelectMode,
  selected,
  onToggleSelect,
}: SessionRowProps) => (
  <div className="group session-row relative">
    {multiSelectMode && canDelete ? (
      <button
        onClick={() => onToggleSelect(session.id)}
        title={selected ? 'Unselect session' : 'Select session'}
        className={`absolute left-2.5 top-3 h-5 w-5 rounded-full border flex items-center justify-center transition-all z-20 ${
          selected
            ? 'border-sky-500 bg-sky-500 shadow-[0_0_8px_rgba(14,165,233,0.4)]'
            : 'border-[color:var(--border-subtle)] bg-[color:var(--surface-1)] hover:border-[color:var(--border-strong)]'
        }`}
      >
        {selected && <Check size={10} className="text-white" strokeWidth={4} />}
      </button>
    ) : null}
    {isEditing ? (
      <div
        className={`w-full flex flex-col gap-1 p-3 rounded-xl text-left transition-all duration-200 border ${
          isActive
            ? 'bg-[color:var(--surface-0)] shadow-md border-[color:var(--border-strong)]'
            : 'bg-[color:var(--surface-1)] border-[color:var(--border-subtle)]'
        } ${multiSelectMode ? 'pl-10 pr-3' : 'pr-3'}`}
      >
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={editTitle}
            onChange={(event) => onEditTitleChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                onSubmitRename(session);
              } else if (event.key === 'Escape') {
                event.preventDefault();
                onCancelRename();
              }
            }}
            className="min-w-0 flex-1 rounded-full border border-[color:var(--border-subtle)] bg-[color:var(--surface-0)] px-3 py-1 text-xs font-semibold text-[color:var(--text-primary)] focus:border-[color:var(--accent-solid)] focus:outline-none"
            placeholder="Session title"
            maxLength={200}
          />
          <div className="flex items-center gap-1">
            <button
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onSubmitRename(session);
              }}
              disabled={isRenaming}
              title="Save title"
              className="h-7 w-7 rounded-full border border-emerald-500/35 text-emerald-400 bg-[color:var(--surface-1)] hover:bg-emerald-500/10 flex items-center justify-center disabled:opacity-40 transition-colors"
            >
              {isRenaming ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
            </button>
            <button
              onClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                onCancelRename();
              }}
              disabled={isRenaming}
              title="Cancel rename"
              className="h-7 w-7 rounded-full border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] bg-[color:var(--surface-1)] hover:bg-[color:var(--surface-0)] flex items-center justify-center disabled:opacity-40 transition-colors"
            >
              <X size={13} />
            </button>
          </div>
        </div>
        <span className="text-[10px] font-medium uppercase tracking-tight text-[color:var(--text-muted)] px-1 opacity-60">{formatCompactDate(session.started_at)}</span>
      </div>
    ) : (
      <button
        data-active={isActive ? 'true' : 'false'}
        onClick={() => {
          if (multiSelectMode) {
            if (canDelete) onToggleSelect(session.id);
            return;
          }
          onClick(session.id);
        }}
        className={`session-row-main w-full flex flex-col gap-1 p-3 rounded-xl text-left transition-all duration-200 border active:scale-[0.98] ${
          isActive
            ? 'bg-[color:var(--surface-0)] shadow-md border-[color:var(--border-strong)] scale-[1.02] z-10'
            : 'hover:bg-[color:var(--surface-2)] text-[color:var(--text-secondary)] border-transparent'
        } ${multiSelectMode ? 'pl-10' : ''}`}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            {session.has_unread && !isActive ? (
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500 animate-pulse" />
            ) : null}
            <span className="min-w-0 flex-1 text-xs font-bold truncate">{session.title || 'Session'}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {sessionChannelKind(session) === 'telegram_group' ? (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider text-sky-400">
                <Users size={8} />
                <span>TG</span>
              </span>
            ) : null}
            {sessionChannelKind(session) === 'telegram_dm' ? (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-sky-500/30 bg-sky-500/10 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider text-sky-400">
                <Send size={8} />
                <span>DM</span>
              </span>
            ) : null}
            {session.is_main ? (
              <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider text-emerald-400">
                <BadgeCheck size={8} />
                Main
              </span>
            ) : null}
          </div>
        </div>
        <span className="text-[9px] font-medium uppercase tracking-tight text-[color:var(--text-muted)] opacity-60">{formatCompactDate(session.started_at)}</span>
      </button>
    )}
    {!isEditing && !multiSelectMode && !session.is_main ? (
      <button
        onClick={() => onSetMain(session)}
        title="Set as main session"
        className="session-row-action absolute right-16 top-3 h-7 w-7 rounded-full border border-emerald-500/35 text-emerald-400 bg-[color:var(--surface-1)] hover:bg-[color:var(--surface-0)] flex items-center justify-center transition-all opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:opacity-100 focus-visible:pointer-events-auto z-20 shadow-sm"
      >
        <BadgeCheck size={13} />
      </button>
    ) : null}
    {!isEditing && !multiSelectMode && canRename ? (
      <button
        onClick={() => onRename(session)}
        disabled={isRenaming}
        title="Rename session"
        className="session-row-action absolute right-9 top-3 h-7 w-7 rounded-full border border-[color:var(--border-subtle)] text-[color:var(--text-secondary)] bg-[color:var(--surface-1)] hover:bg-[color:var(--surface-0)] flex items-center justify-center transition-all opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:opacity-100 focus-visible:pointer-events-auto disabled:opacity-40 disabled:pointer-events-none z-20 shadow-sm"
      >
        {isRenaming ? <Loader2 size={13} className="animate-spin" /> : <Pencil size={13} />}
      </button>
    ) : null}
    {canDelete && !isEditing && !multiSelectMode ? (
      <button
        onClick={() => onDelete(session)}
        disabled={isDeleting}
        title="Delete session"
        className="session-row-action absolute right-2 top-3 h-7 w-7 rounded-full border border-rose-500/20 text-rose-500 bg-[color:var(--surface-1)] hover:bg-[color:var(--surface-0)] flex items-center justify-center transition-all opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:opacity-100 focus-visible:pointer-events-auto z-20 shadow-sm"
      >
        {isDeleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
      </button>
    ) : null}
  </div>
));
SessionRow.displayName = 'SessionRow';
