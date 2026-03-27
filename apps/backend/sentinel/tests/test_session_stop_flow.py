from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

from app.models import Message, Session
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.service import SessionService
from tests.fake_db import FakeDB


@pytest.mark.asyncio
async def test_agent_run_registry_cancel_and_wait_clears_settled_task() -> None:
    registry = AgentRunRegistry()
    settled = asyncio.Event()

    async def _worker() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            raise
        finally:
            settled.set()

    task = asyncio.create_task(_worker())
    try:
        assert await registry.register("session-1", task) is True
        await asyncio.sleep(0)
        assert await registry.cancel_and_wait("session-1", timeout_seconds=1.0) is True
        await asyncio.wait_for(settled.wait(), timeout=1.0)
        assert task.done() is True
        assert await registry.is_running("session-1") is False
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_stop_generation_waits_for_cancelled_run_to_materialize_tool_result() -> None:
    fake_db = FakeDB()
    run_registry = AgentRunRegistry()
    service = SessionService(run_registry=run_registry)

    session_id = uuid.uuid4()
    fake_db.add(Session(id=session_id, user_id="user-1", agent_id="agent-1", title="stop-flow"))

    async def _run_task() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            fake_db.add(
                Message(
                    session_id=session_id,
                    role="assistant",
                    content="",
                    metadata_json={
                        "generation": {
                            "requested_tier": "normal",
                            "resolved_model": "gpt-5.3-codex",
                            "provider": "openai-codex",
                            "temperature": 0.7,
                            "max_iterations": 50,
                        },
                        "tool_calls": [
                            {
                                "id": "toolu_cancelled_1",
                                "name": "runtime",
                                "arguments": {
                                    "command": "user",
                                    "shell_command": "sleep 20",
                                },
                            }
                        ],
                    },
                )
            )
            await asyncio.sleep(0.05)
            raise

    task = asyncio.create_task(_run_task())
    try:
        assert await run_registry.register(str(session_id), task) is True
        await asyncio.sleep(0)
        with patch(
            "app.services.sessions.service.stop_all_detached_runtime_jobs",
            autospec=True,
        ) as stop_jobs:
            cancelled = await service.stop_generation(
                fake_db,
                session_id=session_id,
                user_id="user-1",
            )

        assert cancelled is True
        stop_jobs.assert_awaited_once()
        tool_results = [
            row
            for row in fake_db.storage[Message]
            if row.session_id == session_id
            and row.role == "tool_result"
            and row.tool_call_id == "toolu_cancelled_1"
        ]
        assert len(tool_results) == 1
        result = tool_results[0]
        assert result.tool_name == "runtime"
        assert result.metadata_json["cancelled_by_stop"] is True
        assert result.metadata_json["pending"] is False
        generation = result.metadata_json.get("generation") or {}
        assert generation.get("resolved_model") == "gpt-5.3-codex"
        assert generation.get("provider") == "openai-codex"
        assert await run_registry.is_running(str(session_id)) is False
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
