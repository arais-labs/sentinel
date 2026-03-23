from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from app.services.agent import ToolAdapter
from app.services.llm.generic.types import ToolCallContent
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolDefinition, ToolRegistry
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def test_tool_adapter_hides_session_id_from_model_schema():
    registry = ToolRegistry()

    async def _execute(payload):
        return payload

    original_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "query"],
        "properties": {
            "session_id": {"type": "string"},
            "query": {"type": "string"},
        },
    }
    registry.register(
        ToolDefinition(
            name="scoped_lookup",
            description="Scoped lookup",
            parameters_schema=original_schema,
            execute=_execute,
        )
    )

    adapter = ToolAdapter(registry, ToolExecutor(registry))
    [schema] = adapter.get_tool_schemas()
    params = schema.parameters

    assert "session_id" not in params["properties"]
    assert params["required"] == ["query"]

    # Keep the registry schema intact for non-agent/manual entry points.
    assert "session_id" in original_schema["properties"]
    assert original_schema["required"] == ["session_id", "query"]


def test_tool_adapter_injects_context_session_id_and_strips_from_result():
    registry = ToolRegistry()
    seen_payloads: list[dict] = []

    async def _execute(payload):
        seen_payloads.append(dict(payload))
        return {
            "ok": True,
            "query": payload.get("query"),
            "session_id": payload.get("session_id"),
        }

    registry.register(
        ToolDefinition(
            name="scoped_lookup",
            description="Scoped lookup",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["session_id", "query"],
                "properties": {
                    "session_id": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            execute=_execute,
        )
    )

    adapter = ToolAdapter(registry, ToolExecutor(registry))
    context_session_id = uuid4()
    db = FakeDB()
    [result] = _run(
        adapter.execute_tool_calls(
            [ToolCallContent(id="c1", name="scoped_lookup", arguments={"query": "ping"})],
            db,
            session_id=context_session_id,
        )
    )

    assert result.is_error is False
    assert seen_payloads[0]["session_id"] == str(context_session_id)
    parsed = json.loads(result.content)
    assert parsed["ok"] is True
    assert parsed["query"] == "ping"
    assert "session_id" not in parsed


def test_tool_adapter_overrides_model_provided_session_id():
    registry = ToolRegistry()
    seen_payloads: list[dict] = []

    async def _execute(payload):
        seen_payloads.append(dict(payload))
        return {"ok": True}

    registry.register(
        ToolDefinition(
            name="scoped_lookup",
            description="Scoped lookup",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["session_id", "query"],
                "properties": {
                    "session_id": {"type": "string"},
                    "query": {"type": "string"},
                },
            },
            execute=_execute,
        )
    )

    adapter = ToolAdapter(registry, ToolExecutor(registry))
    context_session_id = uuid4()
    fake_session_from_model = str(uuid4())

    _run(
        adapter.execute_tool_calls(
            [
                ToolCallContent(
                    id="c1",
                    name="scoped_lookup",
                    arguments={"query": "ping", "session_id": fake_session_from_model},
                )
            ],
            FakeDB(),
            session_id=context_session_id,
        )
    )

    assert seen_payloads[0]["session_id"] == str(context_session_id)

