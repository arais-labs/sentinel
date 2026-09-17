"""Read-only conversation evidence, scoped to the caller's instance and user.

Search returns message references rather than inferring task ownership from titles.
Never expose system prompts, provider reasoning, or opaque tool payloads here.
"""

import re
from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select

from app.models import Message, Session, SessionKind


def message_evidence(message: Message, *, query: str = "", limit: int = 1800) -> dict:
    text = " ".join(message.content.split())
    offset = 0
    if query:
        positions = [text.lower().find(term) for term in query.lower().split()]
        matches = [position for position in positions if position >= 0]
        offset = max(0, min(matches) - limit // 4) if matches else 0
    content = text[offset : offset + limit]
    return {
        "message_id": str(message.id),
        "role": message.role,
        "source": (message.metadata_json or {}).get("source", "chat"),
        "content": ("…" if offset else "") + content + ("…" if offset + limit < len(text) else ""),
        "created_at": message.created_at.isoformat(),
        "truncated": offset > 0 or offset + limit < len(text),
    }


def visible_messages(user_id: str):
    return (
        select(Message, Session)
        .join(Session, Session.id == Message.session_id)
        .where(
            Session.user_id == user_id,
            Session.parent_session_id.is_(None),
            Session.kind == SessionKind.CHAT,
            Message.role.in_(["user", "assistant"]),
            func.length(func.trim(Message.content)) > 0,
        )
    )


async def search_conversations(db, *, user_id: str, query: str, chat_id=None, offset=0, limit=20):
    terms = list(dict.fromkeys(re.findall(r"\S+", query.lower())))[:8]
    if not terms:
        raise ValueError("Supply words, a file path, or a reference to search for.")
    statement = visible_messages(user_id).where(
        and_(*[func.lower(Message.content).contains(term, autoescape=True) for term in terms])
    )
    if chat_id:
        statement = statement.where(Session.id == chat_id)
    limit = min(limit, 30)
    rows = (
        await db.execute(
            statement.order_by(Message.created_at.desc(), literal_column("messages.rowid").desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return {
        "query": query,
        "match_rule": "All search words must occur in the message; try fewer words if empty. Use the chats tool's list action for titles.",
        "matches": [
            {
                "chat_id": str(chat.id),
                "title": chat.title or "Untitled chat",
                "workspace_id": str(chat.workspace_id) if chat.workspace_id else None,
                **message_evidence(message, query=query),
            }
            for message, chat in rows[:limit]
        ],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


async def conversation_history(
    db, *, user_id: str, chat_id: UUID, before_message_id=None, limit=20
):
    statement = visible_messages(user_id).where(Session.id == chat_id)
    if before_message_id:
        cursor = (await db.execute(statement.where(Message.id == before_message_id))).first()
        if cursor is None:
            raise ValueError("History cursor is not a visible message in this chat.")
        rowid = (
            select(literal_column("messages.rowid"))
            .where(Message.id == before_message_id)
            .correlate(None)
            .scalar_subquery()
        )
        statement = statement.where(
            or_(
                Message.created_at < cursor[0].created_at,
                and_(
                    Message.created_at == cursor[0].created_at,
                    literal_column("messages.rowid") < rowid,
                ),
            )
        )
    limit = min(limit, 30)
    rows = (
        await db.execute(
            statement.order_by(
                Message.created_at.desc(), literal_column("messages.rowid").desc()
            ).limit(limit + 1)
        )
    ).all()
    page = rows[:limit]
    return {
        "chat_id": str(chat_id),
        "messages": [message_evidence(message) for message, _ in reversed(page)],
        "next_before_message_id": str(page[-1][0].id) if len(rows) > limit else None,
        "order": "oldest_first_within_page; cursor fetches older messages",
    }
