"""Small, current activity snapshots of main chats. No model calls, no agents woken."""

import json
from datetime import UTC, datetime

from sqlalchemy import func, literal_column, select

from app.models import Message, Session, SessionKind, Workspace
from app.services.sessions.visibility import message_evidence


def excerpt(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def tool_summary(message: Message) -> dict:
    metadata = message.metadata_json or {}
    approval = metadata.get("approval") or {}
    pending = metadata.get("pending") or (
        isinstance(approval, dict)
        and (approval.get("pending") or approval.get("status") == "pending")
    )
    # Prefer the tool's own concise presentation; never include its arguments or
    # dump structured output, screenshots, files, or encoded media into Voice.
    summary = metadata.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        try:
            result = json.loads(message.content)
        except (ValueError, TypeError):
            result = None
        if isinstance(result, dict):
            summary = next(
                (
                    result[key]
                    for key in ("summary", "message", "error", "status")
                    if isinstance(result.get(key), str) and result[key].strip()
                ),
                None,
            )
            if not summary:
                # Command tools often expose stdout/stderr rather than a summary.
                # Keep only a tiny text preview, not the full result or arguments.
                preview = next(
                    (
                        result[key]
                        for key in ("stderr", "stdout")
                        if isinstance(result.get(key), str) and result[key].strip()
                    ),
                    "",
                )
                exit_code = result.get("exit_code")
                summary = (
                    f"Exit code {exit_code}. " if isinstance(exit_code, int) else ""
                ) + preview
        elif result is None and not message.content.lstrip().startswith(("{", "[", "data:")):
            summary = message.content
    return {
        "tool": message.tool_name or "Tool",
        "status": "pending" if pending else "failed" if metadata.get("is_error") else "finished",
        "summary": (
            excerpt(summary, 300)
            if isinstance(summary, str) and summary.strip()
            else "No short summary available."
        ),
        "created_at": message.created_at.isoformat(),
    }


async def chat_activity(db, is_running, chat_ids=None, *, since=None, offset=0, limit=None) -> dict:
    """Read at most five text messages and three tool results PER main chat.

    Window functions bound rows returned without scanning history into Python or
    doing a separate history query for every chat. All queries use the caller's
    instance database, and never include subagent/system conversations.
    """
    query = select(Session).where(
        Session.user_id == "local",
        Session.parent_session_id.is_(None),
        Session.kind == SessionKind.CHAT,
    )
    if chat_ids is not None:
        query = query.where(Session.id.in_(chat_ids))
    latest_message = (
        select(func.max(Message.created_at))
        .where(
            Message.session_id == Session.id,
            Message.role.in_(["user", "assistant", "tool", "tool_result"]),
        )
        .correlate(Session)
        .scalar_subquery()
    )
    latest_activity = func.max(
        Session.updated_at, func.coalesce(latest_message, Session.updated_at)
    )
    if since is not None:
        query = query.where(latest_activity >= since.astimezone(UTC))
    query = query.order_by(latest_activity.desc(), Session.id).offset(offset)
    if limit is not None:
        query = query.limit(limit + 1)
    chats = (await db.execute(query.execution_options(populate_existing=True))).scalars().all()
    next_offset = offset + limit if limit is not None and len(chats) > limit else None
    if limit is not None:
        chats = chats[:limit]
    workspace_ids = {chat.workspace_id for chat in chats if chat.workspace_id}
    workspaces = {
        row.id: {
            "workspace_id": str(row.id),
            "name": row.name,
            "directory": row.directory,
            "machine_id": str(row.machine_id),
        }
        for row in (
            (await db.execute(select(Workspace).where(Workspace.id.in_(workspace_ids))))
            .scalars()
            .all()
            if workspace_ids
            else []
        )
    }
    snapshots = {
        chat.id: {
            "chat_id": str(chat.id),
            "title": chat.title or "Untitled chat",
            "running": await is_running(chat.id),
            "workspace": workspaces.get(chat.workspace_id),
            "last_activity_at": chat.updated_at.isoformat(),
            "original_request": excerpt(chat.initial_prompt or "", 1000),
            "messages": [],
            "tools": [],
        }
        for chat in chats
    }
    if snapshots:
        for field, roles, count in [
            ("original_request", ["user"], 1),
            ("messages", ["user", "assistant"], 5),
            ("tools", ["tool_result", "tool"], 3),
        ]:
            ranked = (
                select(
                    Message.id,
                    func.row_number()
                    .over(
                        partition_by=Message.session_id,
                        order_by=(
                            (
                                Message.created_at.asc()
                                if field == "original_request"
                                else Message.created_at.desc()
                            ),
                            (
                                literal_column("messages.rowid").asc()
                                if field == "original_request"
                                else literal_column("messages.rowid").desc()
                            ),
                        ),
                    )
                    .label("position"),
                )
                .where(
                    Message.session_id.in_(snapshots),
                    Message.role.in_(roles),
                    func.length(func.trim(Message.content)) > 0,
                )
                .subquery()
            )
            rows = (
                (
                    await db.execute(
                        select(Message)
                        .join(ranked, Message.id == ranked.c.id)
                        .where(ranked.c.position <= count)
                        .order_by(Message.session_id, ranked.c.position.desc())
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                if field == "original_request":
                    snapshots[row.session_id][field] = excerpt(row.content, 1000)
                    continue
                snapshots[row.session_id]["last_activity_at"] = max(
                    snapshots[row.session_id]["last_activity_at"], row.created_at.isoformat()
                )
                snapshots[row.session_id][field].append(
                    tool_summary(row) if field == "tools" else message_evidence(row, limit=1500)
                )
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "chats": list(snapshots.values()),
        "next_offset": next_offset,
    }
