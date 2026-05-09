from __future__ import annotations

import asyncio

import pytest

from app.sentral import ConversationItem, TextBlock
from app.services.sessions.agent_run_registry import AgentRunRegistry


def _system_item(text: str) -> ConversationItem:
    return ConversationItem(
        id=f"item-{text}",
        role="system",
        content=[TextBlock(text=text)],
    )


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
