"""Direct agent-to-agent delivery through the persisted steering inbox.

A main chat or Voice can message any main chat or Voice. A delegated sub-agent can
message its parent or a sibling task in the same conversation tree. Delivery wakes an
idle target; a running target receives the message at its next model boundary.
"""

from uuid import UUID, uuid4

from sqlalchemy import select

from app.models import Message, Session, SessionKind, SubAgentTask
from app.services.voice.session import voice_session
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


def agent_label(session: Session) -> str:
    if session.kind == SessionKind.VOICE:
        return "Voice"
    return f'chat "{session.title}"' if session.title else f"chat {session.id}"


async def _resolve_target(db, sender: Session, target: str):
    """Return (target session, delegated task or None), enforcing who may reach whom."""
    if target == "voice":
        target = str((await voice_session(db)).id)
    tasks = list((await db.execute(select(SubAgentTask))).scalars().all())
    task = next(
        (
            item
            for item in tasks
            if str(item.id) == target or str((item.result or {}).get("child_session_id")) == target
        ),
        None,
    )
    if target == "parent":
        target_id = sender.parent_session_id
    elif task:
        child = (task.result or {}).get("child_session_id")
        target_id = UUID(child) if child else None
    else:
        try:
            target_id = UUID(target)
        except ValueError:
            target_id = None
    if target_id is None or target_id == sender.id:
        raise ToolValidationError(
            "Target must be another agent: a chat ID, a task ID, parent, or voice"
        )
    session = await db.get(Session, target_id)
    if session is None:
        raise ToolValidationError("Target conversation not found")
    if task is None:
        task = next(
            (
                item
                for item in tasks
                if (item.result or {}).get("child_session_id") == str(target_id)
            ),
            None,
        )
    if sender.parent_session_id is not None:
        # Delegated agents stay inside their own conversation tree.
        root = await root_session(db, sender.id)
        if await root_session(db, target_id) != root or (target_id != root and task is None):
            raise ToolValidationError("Target must be another agent in this conversation")
    elif session.parent_session_id is not None and (
        task is None or await root_session(db, target_id) != sender.id
    ):
        raise ToolValidationError(
            "Target must be a main chat, voice, or one of your delegated agents"
        )
    return session, task


async def send_message(db, *, sender_id, target, content, orchestrator, run_registry=None):
    sender = await db.get(Session, sender_id)
    if sender is None:
        raise ToolValidationError("Sender conversation not found")
    session, task = await _resolve_target(db, sender, target)
    message_id = uuid4()
    label = agent_label(sender)
    metadata = {
        "source": "agent_message",
        "notice": {"title": f"Message from {label}", "body": content},
        "sender_session_id": str(sender_id),
        "sender_kind": sender.kind,
        "root_session_id": str(await root_session(db, sender_id)),
        "steering": "pending",
        "steering_id": str(message_id),
    }
    text = (
        f"[Message from {label}, id {sender_id}. To answer, call chats.send with target "
        f"{sender_id}; a normal reply in this chat does not reach the sender.]\n{content}"
    )
    db.add(
        Message(
            id=message_id, session_id=session.id, role="user", content=text, metadata_json=metadata
        )
    )
    await db.commit()
    item = ConversationItem(
        id=str(message_id), role="user", content=[TextBlock(text=text)], metadata=metadata
    )
    if task:
        accepted = orchestrator is not None and orchestrator.inject_item(task.id, item)
    elif run_registry:
        run_registry.enqueue_interjection(str(session.id), item)
        await run_registry.notify_idle_interjections(str(session.id))
        accepted = True
    else:
        accepted = False
    return {
        "message_id": str(message_id),
        "target_session_id": str(session.id),
        "target": agent_label(session),
        "delivery": "queued" if accepted else "pending",
        "sender_session_id": str(sender_id),
    }
