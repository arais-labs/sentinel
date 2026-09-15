"""Deliver each child turn through the parent's persisted steering inbox."""

from uuid import NAMESPACE_URL, uuid5

from app.models import Message
from sentral import ConversationItem, TextBlock


async def deliver_completion(db, task, run_registry):
    result = task.result or {}
    turn_id = result.get("turn_id") or str(task.completed_at)
    message_id = uuid5(NAMESPACE_URL, f"sentinel:sub-agent:{task.id}:{turn_id}")
    if await db.get(Message, message_id) is not None:
        return False
    completed = task.status == "completed"
    summary = (
        (result.get("final_text") or "Completed without a text response.")
        if completed
        else (
            f"Execution {task.status}. {result.get('error') or ''} "
            "Recorded work remains available in the child trace; no final answer was produced."
        )
    )
    if task.status in {"cancelled", "failed"} and result.get("child_session_id"):
        summary += (
            " When continuing this task is authorized, use delegate.resume with this Task ID "
            "and current instructions to continue the same child conversation."
        )
    content = (
        f"[Sub-Agent Report] Task: {task.objective}\nTask ID: {task.id}\n"
        f"Turn ID: {turn_id}\nStatus: {task.status}\nResult: {summary}"
    )
    metadata = {
        "source": "sub_agent",
        "task_id": str(task.id),
        "turn_id": turn_id,
        "notice": {
            "title": "Sub-agent report",
            "body": f"{task.objective}\nStatus: {task.status}\n\n{summary}",
        },
    }
    if task.status != "cancelled":
        metadata.update(steering="pending", steering_id=str(message_id))
    db.add(
        Message(
            id=message_id,
            session_id=task.session_id,
            role="user",
            content=content,
            metadata_json=metadata,
        )
    )
    await db.commit()
    if task.status != "cancelled":
        run_registry.enqueue_interjection(
            str(task.session_id),
            ConversationItem(
                id=str(message_id),
                role="user",
                content=[TextBlock(text=content)],
                metadata=metadata,
            ),
        )
        await run_registry.notify_idle_interjections(str(task.session_id))
    return True
