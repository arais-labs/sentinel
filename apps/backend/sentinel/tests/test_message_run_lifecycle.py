import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.models import Message
from app.services.agent.agent_modes import AgentMode
from sentral.llm.generic.types import AgentEvent
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.ws.ws_stream_service import run_agent_once


@pytest.mark.asyncio
@pytest.mark.parametrize("result_kind", ["failed", "raised", "success", "cancelled"])
async def test_outcome_is_durable_before_ui_completion(result_kind):
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="user",
        content="hello",
        metadata_json={
            "retryable_error": "Previous failure",
            "model_selection": {"provider_id": "anthropic"},
        },
    )
    db = SimpleNamespace(commit=AsyncMock())
    events = []

    async def broadcast(key, event):
        if event["type"] in {"done", "error"}:
            assert db.commit.await_count == 1
            if result_kind in {"failed", "raised"}:
                assert message.metadata_json["retryable_error"] == "Provider failed"
                if event["type"] == "error":
                    assert event["message_id"] == str(message.id)
            else:
                assert "retryable_error" not in message.metadata_json
        events.append(event)

    async def broadcast_event(key, event):
        await broadcast(key, {"type": event.type})

    manager = SimpleNamespace(broadcast=broadcast, broadcast_agent_event=broadcast_event)

    async def run_turn(request, sink):
        if result_kind == "raised":
            raise RuntimeError("Provider failed")
        if result_kind == "cancelled":
            raise asyncio.CancelledError()
        if result_kind == "failed":
            await sink(AgentEvent(type="error", error="Provider failed"))
        await sink(
            AgentEvent(type="done", stop_reason="error" if result_kind == "failed" else "stop")
        )
        return SimpleNamespace(
            status="error" if result_kind == "failed" else "completed",
            error="Provider failed" if result_kind == "failed" else None,
        )

    with (
        patch(
            "app.services.ws.ws_stream_service.runtime_adapter_module.SentinelLoopRuntimeAdapter",
            return_value=SimpleNamespace(run_turn=run_turn),
        ),
        patch(
            "app.services.ws.ws_stream_service.runtime_event_to_sentinel_event",
            side_effect=lambda e: e,
        ),
    ):
        result = await run_agent_once(
            db=db,
            session_id=message.session_id,
            session_key=str(message.session_id),
            manager=manager,
            run_registry=AgentRunRegistry(),
            agent_runtime_support=object(),
            payload="hello",
            tier=None,
            max_iterations=0,
            agent_mode=AgentMode.NORMAL,
            persist_user_message=False,
            user_message=message,
        )
    assert result.failed == (result_kind in {"failed", "raised"})
    assert result.cancelled == (result_kind == "cancelled")
    assert message.metadata_json["model_selection"]["provider_id"] == "anthropic"
    if result_kind == "cancelled":
        assert message.metadata_json["retryable_error"] == "Previous failure"
        db.commit.assert_not_awaited()
    assert events[-1] == {"type": "run_state", "run_active": False}
