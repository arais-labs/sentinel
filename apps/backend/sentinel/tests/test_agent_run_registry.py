from __future__ import annotations

import asyncio

import pytest

from sentral import ConversationItem, TextBlock
from app.services.sessions.agent_run_registry import AgentRunRegistry


def _system_item(text: str) -> ConversationItem:
    return ConversationItem(
        id=f"item-{text}",
        role="system",
        content=[TextBlock(text=text)],
    )


@pytest.mark.asyncio
async def test_attachment_guard_blocks_only_its_session_and_never_status_reads():
    registry = AgentRunRegistry()
    release = asyncio.Event()
    async with registry.idle_guard("a") as idle:
        assert idle
        pending = asyncio.create_task(registry.start("a", release.wait()))
        other = await asyncio.wait_for(registry.start("b", release.wait()), 1)
        assert await asyncio.wait_for(registry.is_running("b"), 1)
        assert await asyncio.wait_for(registry.get_phase("b"), 1) == "thinking"
        assert not pending.done()
        async with registry.idle_guard("c") as other_idle:
            assert other_idle
    first = await asyncio.wait_for(pending, 1)
    release.set()
    await asyncio.gather(first, other)


@pytest.mark.asyncio
async def test_workspace_guard_keeps_start_exclusion_without_blocking_polling():
    registry = AgentRunRegistry()
    async with registry.workspace_change_guard():
        pending = asyncio.create_task(registry.start("a", asyncio.sleep(0)))
        assert not await asyncio.wait_for(registry.is_running("a"), 1)
        assert not pending.done()
    task = await asyncio.wait_for(pending, 1)
    await task


@pytest.mark.asyncio
async def test_agent_run_registry_drains_interjections_per_session() -> None:
    registry = AgentRunRegistry()

    registry.enqueue_interjection("s1", _system_item("first"))
    registry.enqueue_interjection("s1", _system_item("second"))
    registry.enqueue_interjection("s2", _system_item("other"))

    drained = registry.drain_interjections("s1")

    assert [item.content[0].text for item in drained] == ["first", "second"]
    assert registry.drain_interjections("s1") == []
    assert [item.content[0].text for item in registry.drain_interjections("s2")] == ["other"]


@pytest.mark.asyncio
async def test_agent_run_registry_notifies_when_run_clears_with_pending_interjections() -> None:
    registry = AgentRunRegistry()
    notified: list[str] = []

    async def _on_idle(session_id: str) -> None:
        notified.append(session_id)

    registry.configure_idle_interjections_callback(_on_idle)
    task = asyncio.create_task(asyncio.sleep(0))
    assert await registry.register("s1", task) is True
    registry.enqueue_interjection("s1", _system_item("pending"))

    await registry.clear("s1", task)

    assert notified == ["s1"]
    await task


@pytest.mark.asyncio
async def test_agent_run_registry_cancel_all_cancels_every_active_run() -> None:
    """cancel_all signals every live run task and returns the count, used by
    the FastAPI lifespan to avoid uvicorn waiting on streaming generations
    during a reload.
    """
    registry = AgentRunRegistry()

    async def _long_run() -> None:
        await asyncio.sleep(60)

    task_a = asyncio.create_task(_long_run())
    task_b = asyncio.create_task(_long_run())
    assert await registry.register("a", task_a) is True
    assert await registry.register("b", task_b) is True

    cancelled = await registry.cancel_all(timeout_seconds=1.0)
    assert cancelled == 2
    assert task_a.done() and task_a.cancelled()
    assert task_b.done() and task_b.cancelled()


@pytest.mark.asyncio
async def test_agent_run_registry_cancel_all_returns_zero_when_idle() -> None:
    registry = AgentRunRegistry()
    assert await registry.cancel_all(timeout_seconds=0.5) == 0


@pytest.mark.asyncio
async def test_agent_run_registry_cancel_all_honours_deadline() -> None:
    """cancel_all must return within ~timeout_seconds even when an active task
    is slow to react to cancellation. We assert wall-clock latency, not task
    state — asyncio.wait_for re-cancels on timeout, so the task itself will
    eventually finish; what matters for shutdown is that we don't block past
    the deadline.
    """
    registry = AgentRunRegistry()

    async def _slow_to_cancel() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            # Tiny extra delay to ensure cancel_all's wait_for ticks over.
            await asyncio.sleep(0.05)
            raise

    task = asyncio.create_task(_slow_to_cancel())
    assert await registry.register("s", task) is True

    loop = asyncio.get_running_loop()
    started = loop.time()
    cancelled = await registry.cancel_all(timeout_seconds=0.1)
    elapsed = loop.time() - started

    assert cancelled == 1
    # Generous upper bound; the real guarantee is "bounded, not unbounded".
    assert elapsed < 1.0, f"cancel_all took {elapsed:.2f}s — should be bounded"


@pytest.mark.asyncio
async def test_agent_run_registry_does_not_notify_when_queue_already_drained() -> None:
    registry = AgentRunRegistry()
    notified: list[str] = []

    async def _on_idle(session_id: str) -> None:
        notified.append(session_id)

    registry.configure_idle_interjections_callback(_on_idle)
    task = asyncio.create_task(asyncio.sleep(0))
    assert await registry.register("s1", task) is True
    registry.enqueue_interjection("s1", _system_item("pending"))
    assert registry.drain_interjections("s1")

    await registry.clear("s1", task)

    assert notified == []
    await task
