"""Deliver a finished background runtime job (or any report) to the agent that started it.

The report is the turn's input itself: a user-role item (what model APIs accept as a turn's input)
whose `notice` metadata makes the chat show it as a system notice, never as the user's message. It
goes through the persisted steering inbox like sub-agent reports, so an idle agent wakes on it and a
running one reads it on its next step.
"""

from uuid import NAMESPACE_URL, uuid5

from app.models import Message
from sentral import ConversationItem, TextBlock


def report_text(job: dict, *, stdout_tail: str, stderr_tail: str) -> str:
    lines = [
        "[Machine Job Report]",
        "A background runtime job you started has finished.",
        "Finish the current step, then integrate this result if relevant.",
        "",
        f"Job ID: {str(job.get('id') or '').strip()}",
        f"Status: {str(job.get('status') or '').strip() or 'unknown'}",
    ]
    if job.get("window_id"):
        lines.append(f"Window: {job['window_id']}")
    if job.get("pane_id"):
        lines.append(f"Pane: {job['pane_id']}")
    if job.get("returncode") is not None:
        lines.append(f"Return code: {job['returncode']}")
    command = str(job.get("command") or "").strip()
    if command:
        lines.extend(["", "Command:", command])
    if stdout_tail.strip():
        lines.extend(["", "Stdout tail:", stdout_tail.strip()])
    if stderr_tail.strip():
        lines.extend(["", "Stderr tail:", stderr_tail.strip()])
    return "\n".join(lines).strip()


async def deliver_job_report(db, session, job, *, stdout_tail, stderr_tail, run_registry, wake):
    """Persists and queues the report for `session`; with `wake`, an idle agent starts a turn."""
    job_id = str(job.get("id") or "")
    message_id = uuid5(NAMESPACE_URL, f"sentinel:runtime-job:{session.id}:{job_id}")
    return await deliver_report(
        db,
        session,
        message_id,
        report_text(job, stdout_tail=stdout_tail, stderr_tail=stderr_tail),
        {
            "source": "runtime_job_completion",
            "job_id": job_id,
            "status": str(job.get("status") or ""),
        },
        title="Background job report",
        run_registry=run_registry,
        wake=wake,
    )


async def deliver_report(db, session, message_id, content, metadata, *, title, run_registry, wake):
    """Persists `content` as a notice once per `message_id` and queues it for `session`'s agent."""
    if await db.get(Message, message_id) is not None:
        return False
    metadata = {
        **metadata,
        "notice": {"title": title, "body": content},
        "steering": "pending",
        "steering_id": str(message_id),
    }
    db.add(
        Message(
            id=message_id,
            session_id=session.id,
            role="user",
            content=content,
            metadata_json=metadata,
        )
    )
    await db.commit()
    key = str(session.id)
    run_registry.enqueue_interjection(
        key,
        ConversationItem(
            id=str(message_id), role="user", content=[TextBlock(text=content)], metadata=metadata
        ),
    )
    if wake:
        await run_registry.notify_idle_interjections(key)
    return True
