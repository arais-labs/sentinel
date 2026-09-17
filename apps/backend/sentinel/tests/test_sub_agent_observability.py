import asyncio
from uuid import UUID

import pytest

from app.models import Message, Session
from sentral.llm.generic.types import (
    AssistantMessage,
    TextContent,
    TokenUsage,
    ToolCallContent,
)
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.service import SessionService
from app.services.sub_agents.orchestrator import SubAgentOrchestrator
from app.services.sub_agents.accounting import inherited_model
from app.services.sub_agents.messaging import send_message
from app.services.tools import ToolDefinition, ToolRegistry
from sentral.errors import ToolValidationError
from tests.fake_db import FakeDB
from tests.test_sub_agent_orchestrator import (
    _add_task,
    _build_base_runtime_support,
    _SequenceProvider,
    _SessionFactory,
)


def test_cancel_preserves_checkpointed_usage_and_prices():
    async def scenario():
        db = FakeDB()
        parent = Session(user_id="dev-admin", title="parent", status="active")
        db.add(parent)
        task = _add_task(db, parent)
        entered = asyncio.Event()

        async def slow_tool(payload, runtime):
            entered.set()
            await asyncio.Event().wait()

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="slow_tool",
                description="wait",
                parameters_schema={"type": "object"},
                execute=slow_tool,
            )
        )
        provider = _SequenceProvider(
            [
                AssistantMessage(
                    content=[ToolCallContent(id="call", name="slow_tool", arguments={})],
                    model="m",
                    provider="p",
                    usage=TokenUsage(input_tokens=60000, output_tokens=9),
                    stop_reason="tool_use",
                )
            ]
        )
        runtime = SubAgentOrchestrator(
            _build_base_runtime_support(provider, registry),
            _SessionFactory(db),
            registry,
        )
        runtime.start_task(task.id)
        try:
            await asyncio.wait_for(entered.wait(), 2)
        except TimeoutError:
            raise AssertionError(
                (
                    task.status,
                    task.result,
                    [(m.role, m.content) for m in db.storage.get(Message, [])][-3:],
                )
            )
        assert task.tokens_used == 60009
        await runtime.stop_task(task.id)
        assert task.status == "cancelled"
        assert task.tokens_used == 60009
        assert task.result["usage"]["costs"]["api_list_price"]["usd"] == "0.00012"
        total = await SessionService(run_registry=AgentRunRegistry()).get_usage(
            db, session_id=parent.id, user_id=parent.user_id
        )
        assert total["input_tokens"] == total["delegated"]["input_tokens"] == 60000
        assert total["main"]["requests"] == 0
        assert total["requests"] == 1

    asyncio.run(scenario())


def test_followup_reuses_child_and_counts_both_turns():
    async def scenario():
        db = FakeDB()
        parent = Session(user_id="dev-admin", title="parent", status="active")
        db.add(parent)
        task = _add_task(db, parent)
        provider = _SequenceProvider(
            [
                AssistantMessage(
                    content=[TextContent(text="answer")],
                    model="m",
                    provider="p",
                    usage=TokenUsage(input_tokens=5, output_tokens=2),
                )
            ]
        )
        runtime = SubAgentOrchestrator(
            _build_base_runtime_support(provider), _SessionFactory(db), ToolRegistry()
        )
        await runtime.run_task(task.id)
        child_id = task.result["child_session_id"]
        response = await send_message(
            db,
            sender_id=parent.id,
            target=str(task.id),
            content="Which file supports that?",
            orchestrator=runtime,
        )
        assert response["delivery"] == "queued"
        await asyncio.wait_for(runtime._running_tasks[str(task.id)], 2)
        assert task.status == "completed", task.result
        assert task.result["child_session_id"] == child_id
        assert len(db.storage[Session]) == 2
        assert task.tokens_used == 14
        assert task.turns_used == 2
        inbox = [m for m in db.storage[Message] if m.id == UUID(response["message_id"])]
        assert len(inbox) == 1
        assert inbox[0].metadata_json["steering"] == "delivered"

    asyncio.run(scenario())


def test_messages_reject_unrelated_chats_and_allow_siblings():
    async def scenario():
        db = FakeDB()
        root = Session(user_id="dev-admin", title="root", status="active")
        unrelated = Session(user_id="dev-admin", title="unrelated", status="active")
        db.add(root)
        db.add(unrelated)
        children = []
        for _ in range(2):
            child = Session(
                user_id="dev-admin",
                title="child",
                status="active",
                parent_session_id=root.id,
            )
            db.add(child)
            task = _add_task(db, root)
            task.result = {"child_session_id": str(child.id)}
            children.append(child)
        with pytest.raises(ToolValidationError):
            await send_message(
                db,
                sender_id=children[0].id,
                target=str(unrelated.id),
                content="no",
                orchestrator=None,
            )
        assert not db.storage.get(Message)
        result = await send_message(
            db,
            sender_id=children[0].id,
            target=str(children[1].id),
            content="finding",
            orchestrator=None,
        )
        assert result["delivery"] == "pending"
        assert db.storage[Message][0].metadata_json["root_session_id"] == str(root.id)

    asyncio.run(scenario())


def test_model_selection_inherits_parent_and_allows_tier_override():
    async def scenario():
        db = FakeDB()
        parent = Session(user_id="dev-admin", title="parent", status="active")
        db.add(parent)
        db.add(
            Message(
                session_id=parent.id,
                role="user",
                content="go",
                metadata_json={
                    "generation": {
                        "requested_tier": "hard",
                        "model_selection": {"reasoning_level": "high"},
                    }
                },
            )
        )
        assert await inherited_model(db, parent.id) == "sentinel:hard:auto:high"
        assert await inherited_model(db, parent.id, "fast") == "sentinel:fast:auto:high"

    asyncio.run(scenario())


def test_child_persistence_and_followup_with_real_sqlite(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import SubAgentTask
    from app.models.base import Base

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/children.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with factory() as db:
                parent = Session(user_id="dev-admin", title="parent", status="active")
                db.add(parent)
                await db.flush()
                task = SubAgentTask(
                    session_id=parent.id,
                    objective="inspect",
                    context="scope",
                    allowed_tools=[],
                    constraints=[],
                    status="pending",
                )
                db.add(task)
                await db.commit()
                parent_id, task_id = parent.id, task.id
            provider = _SequenceProvider(
                [
                    AssistantMessage(
                        content=[TextContent(text="answer")],
                        model="m",
                        provider="p",
                        usage=TokenUsage(input_tokens=15, output_tokens=4),
                    )
                ]
            )
            runtime = SubAgentOrchestrator(
                _build_base_runtime_support(provider), factory, ToolRegistry()
            )
            await runtime.run_task(task_id)
            async with factory() as db:
                task = await db.get(SubAgentTask, task_id)
                assert task.status == "completed", task.result
                child_id = task.result["child_session_id"]
                await send_message(
                    db,
                    sender_id=parent_id,
                    target=str(task_id),
                    content="Explain that result",
                    orchestrator=runtime,
                )
            await asyncio.wait_for(runtime._running_tasks[str(task_id)], 3)
            async with factory() as db:
                task = await db.get(SubAgentTask, task_id)
                assert task.status == "completed", task.result
                assert task.result["child_session_id"] == child_id
                assert task.tokens_used == 38
                assert task.result["usage"]["costs"]["api_list_price"]["usd"] == "0.00024"
            await runtime.close()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_internal_messaging_is_hidden_and_legacy_channel_retired():
    from fastapi import HTTPException

    from app.models.modules import Module
    from app.routers.modules import get_module, list_modules
    from app.services.modules.builtins import get_builtins

    modules = {module.name: module for module in get_builtins()}
    assert "coordination" not in modules
    assert modules["chats"].internal

    async def scenario():
        db = FakeDB()
        db.add(Module(name="coordination", label="Coordination", system=True))
        visible = await list_modules(db)
        assert not {"coordination", "chats"} & {m["name"] for m in visible["modules"]}
        for name in ("coordination", "chats"):
            with pytest.raises(HTTPException) as error:
                await get_module(name, db)
            assert error.value.status_code == 404

    asyncio.run(scenario())
