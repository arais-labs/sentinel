from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from app.models import Message, Session
from app.services.agent import ToolAdapter
from app.services.agent.sentinel_runner import AgentLoop
from app.services.llm.generic.types import ToolCallContent
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
)
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


class _FakeSessionFactory:
    def __init__(self, db: FakeDB) -> None:
        self._db = db

    class _SessionContext:
        def __init__(self, db: FakeDB) -> None:
            self._db = db

        async def __aenter__(self) -> FakeDB:
            return self._db

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    def __call__(self) -> "_FakeSessionFactory._SessionContext":
        return self._SessionContext(self._db)


def test_tool_adapter_persists_pending_tool_result_for_approval():
    registry = ToolRegistry()
    seen_payloads: list[dict] = []

    async def _execute(payload):
        seen_payloads.append(dict(payload))
        return {"ok": True}

    async def _waiter(tool_name, payload, requirement, pending_callback):
        approval_payload = {
            "provider": "approval_tool",
            "approval_id": "approval-1",
            "status": "pending",
            "pending": True,
            "can_resolve": True,
            "label": f"{tool_name} approval",
            "action": requirement.action,
            "description": requirement.description,
        }
        if pending_callback is not None:
            await pending_callback(approval_payload)
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                **approval_payload,
                "status": "approved",
                "pending": False,
                "can_resolve": False,
            },
            message="Approval approved.",
        )

    registry.register(
        ToolDefinition(
            name="approval_tool",
            description="Needs approval",
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
            approval_check=lambda: ToolApprovalEvaluation.require(
                ToolApprovalRequirement(
                    action="approval_tool.execute",
                    description="Approval required.",
                ),
            ),
        )
    )

    fake_db = FakeDB()
    adapter = ToolAdapter(
        registry,
        ToolExecutor(registry, approval_waiter=_waiter),
        session_factory=_FakeSessionFactory(fake_db),
    )
    streamed_pending: list[object] = []
    session_id = uuid4()

    async def _capture_pending(item: object) -> None:
        streamed_pending.append(item)

    [result] = _run(
        adapter.execute_tool_calls(
            [ToolCallContent(id="c1", name="approval_tool", arguments={"query": "ping"})],
            fake_db,
            session_id=session_id,
            on_pending_tool_result=_capture_pending,
        )
    )

    assert result.is_error is False
    assert seen_payloads[0]["session_id"] == str(session_id)
    assert len(streamed_pending) == 1
    pending_rows = fake_db.storage[Message]
    assert len(pending_rows) == 1
    row = pending_rows[0]
    assert row.role == "tool_result"
    assert row.tool_call_id == "c1"
    assert row.tool_name == "approval_tool"
    assert row.metadata_json["pending"] is True
    assert row.metadata_json["approval"]["approval_id"] == "approval-1"


def test_tool_adapter_cancellation_reconciles_pending_tool_result_row():
    registry = ToolRegistry()

    async def _execute(_payload):
        raise AssertionError("tool execute should not run after approval wait is cancelled")

    async def _waiter(tool_name, payload, requirement, pending_callback):
        approval_payload = {
            "provider": "approval_tool",
            "approval_id": "approval-cancelled",
            "status": "pending",
            "pending": True,
            "can_resolve": True,
            "label": f"{tool_name} approval",
            "action": requirement.action,
            "description": requirement.description,
        }
        if pending_callback is not None:
            await pending_callback(approval_payload)
        raise asyncio.CancelledError()

    registry.register(
        ToolDefinition(
            name="approval_tool",
            description="Needs approval",
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
            approval_check=lambda: ToolApprovalEvaluation.require(
                ToolApprovalRequirement(
                    action="approval_tool.execute",
                    description="Approval required.",
                ),
            ),
        )
    )

    fake_db = FakeDB()
    session_id = uuid4()
    fake_db.add(Session(id=session_id, user_id="dev-admin", title="approval-cancelled"))
    adapter = ToolAdapter(
        registry,
        ToolExecutor(registry, approval_waiter=_waiter),
        session_factory=_FakeSessionFactory(fake_db),
    )

    [result] = _run(
        adapter.execute_tool_calls(
            [ToolCallContent(id="c1", name="approval_tool", arguments={"query": "ping"})],
            fake_db,
            session_id=session_id,
        )
    )

    pending_rows = fake_db.storage[Message]
    assert len(pending_rows) == 1
    pending_row_id = pending_rows[0].id
    assert pending_rows[0].metadata_json["pending"] is True

    parsed_result = json.loads(result.content)
    assert result.is_error is True
    assert parsed_result["status"] == "cancelled"
    assert result.metadata["__persisted_message_id"] == str(pending_row_id)

    loop = AgentLoop(provider=None, context_builder=None, tool_adapter=adapter)
    _run(
        loop._persist_messages(
            fake_db,
            session_id,
            [result],
            {},
            requested_tier="normal",
            temperature=0.7,
            max_iterations=50,
        )
    )

    rows = fake_db.storage[Message]
    assert len(rows) == 1
    assert rows[0].id == pending_row_id
    assert rows[0].metadata_json["is_error"] is True
    assert rows[0].metadata_json.get("pending") is not True
    assert json.loads(rows[0].content)["status"] == "cancelled"
    assert result.metadata is not None
    assert isinstance(result.metadata["__persisted_message_id"], str)
