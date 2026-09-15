import { useId, useMemo, useState, type ReactNode } from 'react';
import type { ChatRow } from '../../pages/chatTimeline';
import { groupChatTurns, isUserRow, type ChatTurn } from '../../pages/chatTurns';
import './chat-turns.css';

type RenderRow = (row: ChatRow) => ReactNode;
export function ChatTurnTimeline({ rows, busy, renderRow }: { rows: ChatRow[]; busy: boolean; renderRow: RenderRow }) {
  const turns = useMemo(() => groupChatTurns(rows, busy), [rows, busy]);
  return <>{turns.map(turn => <Turn key={turn.key} turn={turn} renderRow={renderRow} />)}</>;
}
function Turn({ turn, renderRow }: { turn: ChatTurn; renderRow: RenderRow }) {
  const [folded, setFolded] = useState(true);
  const id = useId();
  const collapsed = folded && turn.collapsible;
  return <>
    {turn.input && <div data-turn-prompt={turn.input.kind === 'message' ? turn.input.message.content?.replace(/\s+/g, ' ').trim().slice(0, 220) || 'Message with attachment' : ''}>{renderRow(turn.input)}</div>}
    {turn.rows.length > 0 && <section className="chat-turn" data-tool-count={turn.toolCount} data-turn-complete={turn.collapsible || undefined} data-collapsible={turn.collapsible || undefined} data-collapsed={collapsed || undefined}>
      {turn.collapsible && <button className="chat-turn-rail" type="button" aria-label={collapsed ? 'Expand intermediate tool calls' : 'Collapse intermediate tool calls'} aria-expanded={!collapsed} aria-controls={id} onClick={() => setFolded(value => !value)}><span className="chat-turn-grip" /></button>}
      <div id={id} className="chat-turn-content">
        <div className="chat-turn-summary" aria-hidden={!collapsed}><div><button type="button" tabIndex={collapsed ? 0 : -1} onClick={() => setFolded(false)}>{turn.toolCount} tool {turn.toolCount === 1 ? 'call' : 'calls'} · Show work</button></div></div>
        {turn.rows.map((row, index) => {
          const hide = collapsed && !isUserRow(row) && index !== turn.rows.length - 1;
          return <div key={row.key} className="chat-turn-history" data-nav-tool={(row.kind === 'tool' || (row.kind === 'message' && ['tool', 'tool_result', 'tool_call'].includes(row.message.role))) || undefined} data-hidden={hide || undefined} inert={hide} aria-hidden={hide}>
            <div className="chat-turn-history-inner">{renderRow(row)}</div>
          </div>;
        })}
      </div>
    </section>}
  </>;
}
