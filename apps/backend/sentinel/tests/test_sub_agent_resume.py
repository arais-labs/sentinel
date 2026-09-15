import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Message, Session, SubAgentTask
from app.models.base import Base
from sentral.llm.generic.types import (
    AssistantMessage,
    TextContent,
    TokenUsage,
    ToolCallContent,
)
from app.services.sub_agents.orchestrator import SubAgentOrchestrator
from app.services.tools import ToolDefinition, ToolRegistry
from sentral.errors import ToolValidationError
from tests.fake_db import FakeDB
from tests.test_sub_agent_orchestrator import (
    _add_task,
    _build_base_runtime_support,
    _SequenceProvider,
    _SessionFactory,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_orchestrator", [False, True])
async def test_cancel_resume_keeps_history_usage_and_never_replays_tool(
    tmp_path, restart_orchestrator
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/resume.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    entered = asyncio.Event()
    tool_calls, seen = [], []

    async def interrupted_tool(payload, runtime):
        tool_calls.append("called")
        entered.set()
        await asyncio.Event().wait()

    class Provider(_SequenceProvider):
        async def chat(self, messages, *args, **kwargs):
            seen.append(messages)
            return await super().chat(messages, *args, **kwargs)

    provider = Provider(
        [
            AssistantMessage(
                content=[
                    TextContent(text="Prior inspection found file A."),
                    ToolCallContent(id="interrupted", name="work", arguments={}),
                ],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=20, output_tokens=5),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="Continued using the prior inspection.")],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=30, output_tokens=7),
            ),
        ]
    )
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="work",
            description="work",
            parameters_schema={"type": "object"},
            execute=interrupted_tool,
        )
    )
    support = _build_base_runtime_support(provider, tools)
    runtime = SubAgentOrchestrator(support, factory, tools)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            parent = Session(user_id="local", title="Supervisor", status="active")
            db.add(parent)
            await db.flush()
            task = SubAgentTask(
                session_id=parent.id,
                objective="Inspect and fix",
                context="Preserve unrelated work",
                allowed_tools=["work"],
                constraints=[],
                status="pending",
            )
            db.add(task)
            await db.commit()
            task_id, parent_id = task.id, parent.id
        runtime.start_task(task_id)
        await asyncio.wait_for(entered.wait(), 5)
        await runtime.stop_task(task_id)
        async with factory() as db:
            task = await db.get(SubAgentTask, task_id)
            assert task.status == "cancelled"
            child_id, previous_turn = (
                task.result["child_session_id"],
                task.result["turn_id"],
            )
            assert task.tokens_used == 25
        assert provider.calls == 1
        if restart_orchestrator:
            await runtime.close()
            runtime = SubAgentOrchestrator(support, factory, tools)
        resumed = await runtime.resume_task(
            task_id,
            parent_id=parent_id,
            message="The workspace was repaired. Check existing changes before continuing.",
        )
        assert resumed["target_session_id"] == child_id
        assert resumed["delivery"] == "queued"
        await asyncio.wait_for(runtime._running_tasks[str(task_id)], 5)
        async with factory() as db:
            task = await db.get(SubAgentTask, task_id)
            assert task.status == "completed", task.result
            assert task.result["child_session_id"] == child_id
            assert task.result["turn_id"] != previous_turn
            assert task.tokens_used == 62
            assert task.allowed_tools == ["work"]
            assert len((await db.execute(select(Session))).scalars().all()) == 2
            delivered = await db.get(Message, UUID(resumed["message_id"]))
            assert delivered.metadata_json["steering"] == "delivered"
        assert "Prior inspection found file A." in str(seen[-1])
        assert "The workspace was repaired." in str(seen[-1])
        assert tool_calls == ["called"], "Interrupted tool calls must not be replayed"
    finally:
        await runtime.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_resume_rejects_unrelated_parent_missing_child_and_capacity():
    db = FakeDB()
    parent = Session(user_id="local", title="parent", status="active")
    db.add(parent)
    task = _add_task(db, parent)
    task.status = "cancelled"
    runtime = SubAgentOrchestrator(
        _build_base_runtime_support(_SequenceProvider([])),
        _SessionFactory(db),
        ToolRegistry(),
    )
    with pytest.raises(ToolValidationError, match="not found for this session"):
        await runtime.resume_task(task.id, parent_id=uuid4(), message="continue")
    with pytest.raises(ToolValidationError, match="no saved child"):
        await runtime.resume_task(task.id, parent_id=parent.id, message="continue")
    child = Session(user_id="local", parent_session_id=parent.id, title="child", status="active")
    db.add(child)
    task.result = {"child_session_id": str(child.id)}
    for _ in range(3):
        _add_task(db, parent)
    with pytest.raises(ToolValidationError, match="Max 3 concurrent"):
        await runtime.resume_task(task.id, parent_id=parent.id, message="continue")
    assert not runtime._running_tasks
    assert not db.storage.get(Message)
    await runtime.close()


@pytest.mark.asyncio
async def test_resume_tool_validates_and_forwards_instructions():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.modules.builtins.sub_agents.handlers import handle_resume
    from app.services.modules.builtins.sub_agents.module import MODULE
    from app.services.tools.registry import ToolRuntimeContext

    assert "resume" in {action.id for action in MODULE.actions}
    parent, task = uuid4(), uuid4()
    orchestrator = SimpleNamespace(resume_task=AsyncMock(return_value={"status": "pending"}))
    runtime = ToolRuntimeContext(session_id=parent, sub_agent_orchestrator=orchestrator)
    for payload in [
        {"task_id": "invalid", "message": "continue"},
        {"task_id": str(task), "message": " "},
    ]:
        with pytest.raises(ToolValidationError):
            await handle_resume(payload, runtime)
    await handle_resume(
        {"task_id": str(task), "message": " Continue from your saved work. "}, runtime
    )
    orchestrator.resume_task.assert_awaited_once_with(
        task, parent_id=parent, message="Continue from your saved work."
    )


@pytest.mark.asyncio
async def test_concurrent_resume_starts_once_and_subsequent_stop_cancels_it():
    from tests.test_sub_agent_orchestrator import _SlowProvider

    db = FakeDB()
    parent = Session(user_id="local", title="parent", status="active")
    db.add(parent)
    child = Session(user_id="local", parent_session_id=parent.id, title="child", status="active")
    db.add(child)
    task = _add_task(db, parent)
    task.status = "cancelled"
    task.result = {"child_session_id": str(child.id)}
    runtime = SubAgentOrchestrator(
        _build_base_runtime_support(_SlowProvider()),
        _SessionFactory(db),
        ToolRegistry(),
    )
    try:
        results = await asyncio.gather(
            *[
                runtime.resume_task(task.id, parent_id=parent.id, message="Continue")
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, ToolValidationError) for result in results) == 1
        for _ in range(100):
            if task.status == "running":
                break
            await asyncio.sleep(0.001)
        assert task.status == "running"
        assert await runtime.stop_task(task.id)
        assert task.status == "cancelled"
        assert not runtime._running_tasks
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_shutdown_during_admission_does_not_leave_a_phantom_resume():
    db = FakeDB()
    parent = Session(user_id="local", title="parent", status="active")
    db.add(parent)
    child = Session(user_id="local", parent_session_id=parent.id, title="child", status="active")
    db.add(child)
    task = _add_task(db, parent)
    task.status = "cancelled"
    task.result = {"child_session_id": str(child.id)}
    runtime = SubAgentOrchestrator(
        _build_base_runtime_support(_SequenceProvider([])),
        _SessionFactory(db),
        ToolRegistry(),
    )
    runtime.inject_item = lambda *args: False
    with pytest.raises(ToolValidationError, match="stopped before resuming"):
        await runtime.resume_task(task.id, parent_id=parent.id, message="Continue")
    assert task.status == "cancelled"
    assert db.storage[Message][0].metadata_json["steering"] == "cancelled"
    assert not runtime._running_tasks
    await runtime.close()
