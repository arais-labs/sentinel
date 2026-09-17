"""Select model-visible history without removing the conversation's audit trail."""

from datetime import UTC, datetime
from sqlalchemy import select
from app.models import Message, SessionSummary


async def context_history(db, session_id, *, include_compacted=False):
    result = await db.execute(select(SessionSummary).where(SessionSummary.session_id == session_id))
    summary = result.scalars().first()
    payload = summary.summary or {} if summary else {}
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .execution_options(populate_existing=True)
    )
    messages = sorted(
        result.scalars().all(),
        key=lambda m: (m.created_at or datetime.min.replace(tzinfo=UTC), str(m.id)),
    )
    messages = order_steering_history(messages)
    boundary = payload.get("through_message_id")
    if boundary and not include_compacted:
        for index, message in enumerate(messages):
            if str(message.id) == boundary:
                messages = messages[index + 1 :]
                break
        else:
            raise ValueError("The compacted context boundary is missing from session history.")
    messages = [
        m
        for m in messages
        if (m.metadata_json or {}).get("source") not in {"runtime_context", "usage"}
    ]
    return summary, messages


def order_steering_history(messages):
    """Keep the visible send time, but replay steering where the model received it.

    Inserting a user update between a tool call and its result would otherwise
    break provider tool pairing when the conversation is loaded again.
    """
    anchored = {}
    pending = []
    ordinary = []
    for message in messages:
        metadata = message.metadata_json or {}
        state = metadata.get("steering")
        if state == "cancelled":
            continue
        if state == "pending":
            pending.append(message)
        elif state == "delivered":
            anchored.setdefault(metadata.get("steering_after_message_id"), []).append(message)
        else:
            ordinary.append(message)
    ordered = list(anchored.pop(None, []))
    for message in ordinary:
        ordered.append(message)
        ordered.extend(anchored.pop(str(message.id), []))
    # Anchors may have been compacted out of an already selected history slice.
    for updates in anchored.values():
        ordered.extend(updates)
    return ordered + pending
