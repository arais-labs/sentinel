import { History, Loader2, Trash2 } from 'lucide-react';
import { SessionRow, sessionChannelKind } from './SessionRow';
import type { Session } from '../../types/api';

interface SessionHistorySidebarProps {
  forkSession?: (session: Session) => void;
  forkingSessionId?: string | null;
  showHeaderTitle?: boolean;
  hideTabs?: boolean;
  highlightedSessionId?: string;
  historyTab: 'sessions' | 'sub_agents';
  setHistoryTab: (tab: 'sessions' | 'sub_agents') => void;
  sessionFilter: string;
  setSessionFilter: (v: string) => void;
  isMultiSelectMode: boolean;
  setIsMultiSelectMode: (v: boolean | ((curr: boolean) => boolean)) => void;
  selectedSessionIds: string[];
  setSelectedSessionIds: (v: string[] | ((curr: string[]) => string[])) => void;
  allVisibleSelected: boolean;
  selectableVisibleSessionIds: string[];
  deleteSelectedSessions: () => void;
  deletingSessionId: string | null;
  filteredSessions: Session[];
  activeSessionId: string | null;
  onSessionClick: (id: string) => void;
  editingSessionId: string | null;
  editingSessionTitle: string;
  setEditingSessionTitle: (v: string) => void;
  submitRenameSession: (s: Session) => void;
  cancelRenameSession: () => void;
  startRenameSession: (s: Session) => void;
  deleteSession: (s: Session) => void;
  renamingSessionId: string | null;
  loadingSessions?: boolean;
}

export function SessionHistorySidebar({
  forkSession,
  forkingSessionId,
  showHeaderTitle = true,
  hideTabs = false,
  highlightedSessionId,
  historyTab,
  setHistoryTab,
  sessionFilter,
  setSessionFilter,
  isMultiSelectMode,
  setIsMultiSelectMode,
  selectedSessionIds,
  setSelectedSessionIds,
  allVisibleSelected,
  selectableVisibleSessionIds,
  deleteSelectedSessions,
  deletingSessionId,
  filteredSessions,
  activeSessionId,
  onSessionClick,
  editingSessionId,
  editingSessionTitle,
  setEditingSessionTitle,
  submitRenameSession,
  cancelRenameSession,
  startRenameSession,
  deleteSession,
  renamingSessionId,
  loadingSessions = false,
}: SessionHistorySidebarProps) {
  return (
    <div className="session-history flex flex-col h-full min-h-0 min-w-0">
      <div className="session-history-controls p-3 shrink-0 border-b border-(--border-subtle) space-y-2">
        {showHeaderTitle ? (
          <h2 className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted) px-1">History</h2>
        ) : null}
        {!hideTabs && <div className="relative grid grid-cols-2 gap-0 rounded-full border border-(--border-subtle) p-0.5 bg-(--surface-2) overflow-hidden">
          {/* Sliding Indicator */}
          <div
            className={`absolute top-0.5 bottom-0.5 w-[calc(50%-2px)] rounded-full bg-(--surface-0) shadow-xs transition-all duration-300 ease-out ${
              historyTab === 'sessions' ? 'left-0.5' : 'left-[calc(50%)]'
            }`}
          />

          <button
            onClick={() => setHistoryTab('sessions')}
            className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
              historyTab === 'sessions'
                ? 'text-(--text-primary)'
                : 'text-(--text-muted) hover:text-(--text-secondary)'
            }`}
          >
            Sessions
          </button>
          <button
            onClick={() => setHistoryTab('sub_agents')}
            className={`relative z-10 h-7 rounded-full text-[10px] font-bold uppercase tracking-wider transition-colors duration-200 active:scale-95 ${
              historyTab === 'sub_agents'
                ? 'text-(--text-primary)'
                : 'text-(--text-muted) hover:text-(--text-secondary)'
            }`}
          >
            Sub-agents
          </button>
        </div>}
        <div className="relative">
          <History size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-(--text-muted)" />
          <input
              className="input-field pl-8 h-8 rounded-full text-xs"
              placeholder="Search sessions…"
              aria-label="Search sessions"
              value={sessionFilter}
              onChange={(e) => setSessionFilter(e.target.value)}
          />
        </div>
        <div className="flex items-center justify-between gap-2">
          <button
            onClick={() => {
              setIsMultiSelectMode((current) => {
                const next = !current;
                if (!next) setSelectedSessionIds([]);
                return next;
              });
            }}
            className={`rounded-full border px-3 py-1 text-[10px] font-bold uppercase tracking-wider transition-all active:scale-95 shadow-xs ${
              isMultiSelectMode
                ? 'bg-(--accent-solid) text-(--app-bg) border-transparent'
                : 'border-(--border-subtle) bg-(--surface-0) text-(--text-secondary) hover:border-(--border-strong) hover:text-(--text-primary)'
            }`}
          >
            {isMultiSelectMode ? 'Done' : 'Select'}
          </button>
          {isMultiSelectMode ? (
            <button
              onClick={() =>
                setSelectedSessionIds((current) => {
                  const set = new Set(current);
                  if (allVisibleSelected) {
                    selectableVisibleSessionIds.forEach((id) => set.delete(id));
                  } else {
                    selectableVisibleSessionIds.forEach((id) => set.add(id));
                  }
                  return Array.from(set);
                })
              }
              className="rounded-full border border-(--border-subtle) bg-(--surface-0) px-3 py-1 text-[10px] font-bold uppercase tracking-wider text-(--text-secondary) hover:border-(--border-strong) hover:text-(--text-primary) transition-all active:scale-95 shadow-xs"
            >
              {allVisibleSelected ? 'Unselect All' : 'Select All'}
            </button>
          ) : null}
        </div>
        {isMultiSelectMode ? (
          <div className="flex items-center justify-between gap-2 px-1 pt-2 border-t border-(--border-subtle) animate-in slide-in-from-top-1">
            <p className="text-[10px] font-bold uppercase tracking-widest text-(--text-muted)">
              {selectedSessionIds.length} <span className="opacity-50">Selected</span>
            </p>
            <button
              onClick={() => void deleteSelectedSessions()}
              disabled={selectedSessionIds.length === 0 || deletingSessionId !== null}
              className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/10 border border-rose-500/20 px-3 py-1 text-[10px] font-bold uppercase tracking-widest text-rose-500 hover:bg-rose-500 hover:text-white transition-all active:scale-95 disabled:opacity-40 disabled:pointer-events-none shadow-xs"
            >
              <Trash2 size={11} />
              Delete Selected
            </button>
          </div>
        ) : null}
      </div>
      <div className="session-history-results flex-1 min-h-0 overflow-y-auto p-2 space-y-0.5 custom-scrollbar">
        {loadingSessions && filteredSessions.length === 0 ? (
          <div className="py-8 text-center">
            <Loader2 size={16} className="animate-spin mx-auto text-(--text-muted)" />
          </div>
        ) : null}
        {filteredSessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              isActive={s.id === (highlightedSessionId ?? activeSessionId)}
              onClick={onSessionClick}
              canDelete={true}
              isDeleting={
                deletingSessionId === s.id ||
                selectedSessionIds.includes(s.id) && deletingSessionId !== null
              }
              onFork={forkSession}
              isForking={forkingSessionId === s.id}
              onDelete={deleteSession}
              canRename={(() => {
                const kind = sessionChannelKind(s);
                return kind !== 'telegram_group' && kind !== 'telegram_dm';
              })()}
              isEditing={editingSessionId === s.id}
              isRenaming={renamingSessionId === s.id}
              editTitle={editingSessionTitle}
              onEditTitleChange={setEditingSessionTitle}
              onSubmitRename={submitRenameSession}
              onCancelRename={cancelRenameSession}
              onRename={startRenameSession}
              multiSelectMode={isMultiSelectMode}
              selected={selectedSessionIds.includes(s.id)}
              onToggleSelect={(id) => setSelectedSessionIds(curr => curr.includes(id) ? curr.filter(x => x !== id) : [...curr, id])}
            />
        ))}
        {!loadingSessions && filteredSessions.length === 0 && (
          <div className="py-8 text-center opacity-40">
            <p className="text-[10px] font-bold uppercase tracking-widest">No matching history</p>
          </div>
        )}
      </div>
    </div>
  );
}
