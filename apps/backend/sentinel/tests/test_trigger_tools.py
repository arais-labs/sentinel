from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.models import Session, Trigger
from app.services.tools.executor import ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolRegistry
from app.services.tools.trigger_tools import (
    trigger_create_tool,
    trigger_delete_tool,
    trigger_list_tool,
    trigger_update_tool,
)
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


class _SessionCtx:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    def __call__(self):
        return _SessionCtx(self._db)


def _executor_for(db: FakeDB) -> ToolExecutor:
    registry = ToolRegistry()
    session_factory = _SessionFactory(db)
    registry.register(trigger_create_tool(session_factory))
    registry.register(trigger_list_tool(session_factory))
    registry.register(trigger_update_tool(session_factory))
    registry.register(trigger_delete_tool(session_factory))
    return ToolExecutor(registry)


def test_trigger_create_binds_owner_from_context_session():
    db = FakeDB()
    session = Session(user_id="user-1", title="main", status="active")
    db.add(session)
    executor = _executor_for(db)

    _run(
        executor.execute(
            "trigger_create",
            {
                "name": "daily",
                "type": "heartbeat",
                "config": {"interval_seconds": 60},
                "action_type": "tool_call",
                "action_config": {"name": "memory_roots", "arguments": {}},
                "session_id": str(session.id),
            },
            allow_high_risk=True,
        )
    )

    assert len(db.storage[Trigger]) == 1
    created = db.storage[Trigger][0]
    assert created.user_id == "user-1"
    assert created.action_config.get("route_mode") is None
    assert created.action_config.get("resolved_session_id") is None
    assert created.action_config.get("session_id") is None


def test_trigger_create_invalid_specific_target_falls_back_to_main():
    db = FakeDB()
    session = Session(user_id="user-1", title="main", status="active")
    db.add(session)
    executor = _executor_for(db)

    result, _ = _run(
        executor.execute(
            "trigger_create",
            {
                "name": "daily",
                "type": "heartbeat",
                "config": {"interval_seconds": 60},
                "action_type": "agent_message",
                "action_config": {
                    "message": "ping",
                    "route_mode": "session",
                    "target_session_id": "0fb4e3d8-4238-4fae-a5df-7bf1a615b38b",
                },
                "session_id": str(session.id),
            },
            allow_high_risk=True,
        )
    )

    assert result["trigger_id"]
    created = db.storage[Trigger][0]
    assert created.action_config.get("route_mode") == "main"
    assert created.action_config.get("target_session_id") is None
    assert created.action_config.get("route_fallback_reason") == "invalid_or_deleted_target_session"


def test_trigger_create_rejects_missing_owner_context():
    db = FakeDB()
    executor = _executor_for(db)

    try:
        _run(
            executor.execute(
                "trigger_create",
                {
                    "name": "daily",
                    "type": "heartbeat",
                    "config": {"interval_seconds": 60},
                    "action_type": "tool_call",
                    "action_config": {"name": "memory_roots", "arguments": {}},
                },
                allow_high_risk=True,
            )
        )
        raised = False
    except ToolValidationError as exc:
        raised = True
        assert "requires session context" in str(exc)
    assert raised is True


def test_trigger_list_is_owner_scoped_by_session():
    db = FakeDB()
    user_a_session = Session(user_id="user-a", title="a", status="active")
    user_b_session = Session(user_id="user-b", title="b", status="active")
    db.add(user_a_session)
    db.add(user_b_session)
    db.add(
        Trigger(
            user_id="user-a",
            name="a-trigger",
            type="heartbeat",
            enabled=True,
            config={"interval_seconds": 60},
            action_type="tool_call",
            action_config={"name": "x", "arguments": {}},
            next_fire_at=datetime.now(UTC),
        )
    )
    db.add(
        Trigger(
            user_id="user-b",
            name="b-trigger",
            type="heartbeat",
            enabled=True,
            config={"interval_seconds": 60},
            action_type="tool_call",
            action_config={"name": "x", "arguments": {}},
            next_fire_at=datetime.now(UTC),
        )
    )
    executor = _executor_for(db)

    user_a_list, _ = _run(
        executor.execute(
            "trigger_list",
            {"session_id": str(user_a_session.id)},
            allow_high_risk=True,
        )
    )
    user_b_list, _ = _run(
        executor.execute(
            "trigger_list",
            {"session_id": str(user_b_session.id)},
            allow_high_risk=True,
        )
    )

    assert user_a_list["total"] == 1
    assert user_a_list["triggers"][0]["name"] == "a-trigger"
    assert user_b_list["total"] == 1
    assert user_b_list["triggers"][0]["name"] == "b-trigger"
