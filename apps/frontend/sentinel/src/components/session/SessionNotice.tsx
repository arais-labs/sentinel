import { Markdown } from '../ui/Markdown';
import type { Message } from '../../types/api';
import type { MessageNotice } from '../../lib/message-notice';

export function SessionNotice({ message, notice }: { message: Message; notice: MessageNotice }) {
  const state = message.metadata?.steering;
  const delivery = state === 'pending' ? 'Queued' : state === 'delivered' ? 'Delivered' : state === 'cancelled' ? 'Cancelled' : null;
  return <div className="my-4 w-full border-y border-(--border-subtle) py-3 text-xs leading-relaxed text-(--text-secondary)">
    <div className="mb-1 flex items-center gap-2 text-[10px] font-semibold text-(--text-muted)">
      <span>{notice.title}</span>
      {delivery && <span>· {delivery}</span>}
    </div>
    <Markdown content={notice.body ?? message.content} compact muted />
  </div>;
}
