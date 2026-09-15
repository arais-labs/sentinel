"""Internal, conversation-scoped delivery through the existing persisted steering inbox."""

from uuid import UUID, uuid4
from sqlalchemy import select
from app.models import Message, Session, SubAgentTask
from sentral import ConversationItem, TextBlock
from sentral.errors import ToolValidationError


async def root_session(db, session_id):
    session = await db.get(Session, session_id)
    seen = set()
    while session and session.parent_session_id:
        if session.id in seen:
            raise ToolValidationError("Invalid conversation ancestry")
        seen.add(session.id)
        session = await db.get(Session, session.parent_session_id)
    if session is None:
        raise ToolValidationError("Conversation not found")
    return session.id


async def send_message(db, *, sender_id, target, content, orchestrator, run_registry=None):
    sender = await db.get(Session, sender_id)
    if sender is None:
        raise ToolValidationError("Sender conversation not found")
    root = await root_session(db, sender_id)
    tasks = list((await db.execute(select(SubAgentTask))).scalars().all())
    target_task = next(
        (
            task
            for task in tasks
            if str(task.id) == target or str((task.result or {}).get("child_session_id")) == target
        ),
        None,
    )
    if target == "parent":
        target_id = sender.parent_session_id
    elif target_task:
        child = (target_task.result or {}).get("child_session_id")
        target_id = UUID(child) if child else None
    else:
        try:
            target_id = UUID(target)
        except ValueError:
            target_id = None
    if target_id is None or target_id == sender_id or await root_session(db, target_id) != root:
        raise ToolValidationError("Target must be another agent in this conversation")
    if target_task is None:
        target_task = next(
            (
                task
                for task in tasks
                if (task.result or {}).get("child_session_id") == str(target_id)
            ),
            None,
        )
    if target_id != root and target_task is None:
        raise ToolValidationError("Target is not a delegated agent")
    message_id = uuid4()
    metadata = {
        "source": "agent_message",
        "notice": {"title": "Agent message", "body": content},
        "sender_session_id": str(sender_id),
        "root_session_id": str(root),
        "steering": "pending",
        "steering_id": str(message_id),
    }
    text = f"[Agent message from {sender_id}]\n{content}"
    message = Message(
        id=message_id,
        session_id=target_id,
        role="user",
        content=text,
        metadata_json=metadata,
    )
    db.add(message)
    await db.commit()
    item = ConversationItem(
        id=str(message_id),
        role="user",
        content=[TextBlock(text=text)],
        metadata=metadata,
    )
    if target_task:
        accepted = orchestrator is not None and orchestrator.inject_item(target_task.id, item)
    elif run_registry:
        run_registry.enqueue_interjection(str(target_id), item)
        await run_registry.notify_idle_interjections(str(target_id))
        accepted = True
    else:
        accepted = False
    return {
        "message_id": str(message_id),
        "target_session_id": str(target_id),
        "delivery": "queued" if accepted else "pending",
        "sender_session_id": str(sender_id),
    }
