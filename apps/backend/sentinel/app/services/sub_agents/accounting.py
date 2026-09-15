"""Use the same persisted provider snapshots as the main conversation."""

from uuid import UUID
from sqlalchemy import select
from app.models import Message
from app.services.llm.session_selection import selection_model
from app.services.sessions.usage import conversation_usage, session_usage


async def task_usage(db, task):
    child = (task.result or {}).get("child_session_id")
    return await conversation_usage(db, UUID(child)) if child else session_usage([])


async def inherited_model(db, session_id, tier=None):
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    for message in result.scalars().all():
        metadata = message.metadata_json or {}
        generation = metadata.get("generation") or {}
        selection = metadata.get("model_selection") or generation.get("model_selection") or {}
        requested = generation.get("requested_tier")
        if requested or selection:
            if requested and str(requested).startswith("sentinel:"):
                parts = requested.split(":")
                if tier:
                    parts[1] = tier
                return ":".join(parts)
            return selection_model(
                tier or requested,
                selection.get("provider_id"),
                selection.get("reasoning_level"),
                selection.get("fast_mode", False),
            )
    return selection_model(tier)


async def wakeup_model(db, session_id, steering_metadata):
    generation = steering_metadata.get("generation") or {}
    selection = steering_metadata.get("model_selection") or generation.get("model_selection") or {}
    tier = generation.get("requested_tier")
    if tier or selection:
        return selection_model(
            tier,
            selection.get("provider_id"),
            selection.get("reasoning_level"),
            selection.get("fast_mode", False),
        )
    return await inherited_model(db, session_id)
