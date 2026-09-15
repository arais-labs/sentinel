import { messageNotice } from '../lib/message-notice';
import type { ChatRow } from './chatTimeline';

export type ChatTurn = { key: string; input?: ChatRow; rows: ChatRow[]; collapsible: boolean; toolCount: number };
export const isUserRow = (row: ChatRow) => row.kind === 'message' && row.message.role === 'user' && !messageNotice(row.message);
export function groupChatTurns(rows: ChatRow[], busy: boolean): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let current: ChatTurn | undefined;
  for (const row of rows) {
    if (row.kind === 'message' && messageNotice(row.message)) {
      turns.push({ key: row.key, rows: [row], collapsible: false, toolCount: 0 });
      current = undefined;
      continue;
    }
    // Steering belongs to the running turn and must never disappear when folded.
    const steering = row.kind === 'message' && Boolean(row.message.metadata?.steering);
    if (isUserRow(row) && !steering) {
      current = { key: row.key, input: row, rows: [], collapsible: false, toolCount: 0 };
      turns.push(current);
    } else {
      if (!current) {
        current = { key: `partial-${row.key}`, rows: [], collapsible: false, toolCount: 0 };
        turns.push(current);
      }
      current.rows.push(row);
      if (row.kind === 'tool' || (row.kind === 'message' && ['tool', 'tool_result', 'tool_call'].includes(row.message.role))) current.toolCount++;
    }
  }
  for (const [index, turn] of turns.entries()) {
    const last = turn.rows.at(-1);
    const pending = turn.rows.some(row => row.kind === 'tool' ? row.active : row.kind === 'text' ? row.streaming :
      row.message.metadata?.pending === true || row.message.metadata?.retryable_error ||
      (row.message.metadata?.approval as { pending?: boolean } | undefined)?.pending === true);
    turn.collapsible = Boolean(turn.input && turn.toolCount && last?.kind === 'message' && last.message.role === 'assistant'
      && last.message.content?.trim() && !pending && !(busy && index === turns.length - 1));
  }
  return turns;
}
