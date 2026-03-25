from __future__ import annotations

import asyncio

from app.models import Message, Session, SubAgentTask
from app.services.agent import ContextBuilder, SentinelRuntimeSupport, ToolAdapter
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent, ToolCallContent, TokenUsage
from app.services.sub_agents import SubAgentOrchestrator
from app.services.tools import ToolDefinition, ToolExecutor, ToolRegistry
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


class _SequenceProvider(LLMProvider):
    def __init__(self, responses: list[AssistantMessage]) -> None:
        self._responses = responses
        self.calls = 0

    @property
    def name(self) -> str:
        return "sequence"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        idx = min(self.calls, len(self._responses) - 1)
        _ = tool_choice
        self.calls += 1
        return self._responses[idx]

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = tool_choice
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


class _SlowProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "slow"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        await asyncio.sleep(2)
        raise AssertionError("unreachable")

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        await asyncio.sleep(2)
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


def _build_base_runtime_support(
    provider: LLMProvider,
    registry: ToolRegistry | None = None,
) -> SentinelRuntimeSupport:
    tool_registry = registry or ToolRegistry()
    return SentinelRuntimeSupport(
        provider,
        ContextBuilder(default_system_prompt="base"),
        ToolAdapter(tool_registry, ToolExecutor(tool_registry)),
    )


def _add_task(db: FakeDB, session: Session, **kwargs) -> SubAgentTask:
    task = SubAgentTask(
        session_id=session.id,
        objective=kwargs.get("objective", "do work"),
        context=kwargs.get("context", "scope"),
        constraints=[],
        allowed_tools=kwargs.get("allowed_tools", []),
        max_turns=kwargs.get("max_turns", 3),
        timeout_seconds=kwargs.get("timeout_seconds", 30),
        status="pending",
    )
    db.add(task)
    return task


def test_orchestrator_completes_and_creates_child_session_with_usage():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, max_turns=2)

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[TextContent(text="sub-agent complete")],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=5, output_tokens=7),
                stop_reason="stop",
            )
        ]
    )
    runtime_support = _build_base_runtime_support(provider)
    orchestrator = SubAgentOrchestrator(runtime_support, _SessionFactory(db), ToolRegistry())

    _run(orchestrator.run_task(task.id))

    assert task.status == "completed"
    assert task.turns_used == 1
    assert task.tokens_used == 12
    child_id = task.result["child_session_id"]
    child = next(s for s in db.storage[Session] if str(s.id) == child_id)
    assert child.parent_session_id == parent.id


def test_orchestrator_scopes_allowed_tools():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, allowed_tools=["allowed_tool"], max_turns=2)

    registry = ToolRegistry()

    async def _allowed_exec(payload):
        return {"ok": True, "payload": payload}

    async def _blocked_exec(payload):
        return {"bad": payload}

    registry.register(
        ToolDefinition(
            name="allowed_tool",
            description="allowed",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_allowed_exec,
        )
    )
    registry.register(
        ToolDefinition(
            name="blocked_tool",
            description="blocked",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_blocked_exec,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_1", name="blocked_tool", arguments={"x": 1})],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="done")],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )
    runtime_support = _build_base_runtime_support(provider, registry)
    orchestrator = SubAgentOrchestrator(runtime_support, _SessionFactory(db), registry)

    _run(orchestrator.run_task(task.id))

    assert task.status == "completed"
    child_id = task.result["child_session_id"]
    tool_result = next(
        m for m in db.storage[Message] if str(m.session_id) == child_id and m.role == "tool_result"
    )
    assert tool_result.tool_name == "blocked_tool"
    assert "not registered" in tool_result.content


def test_orchestrator_reports_actual_iterations_with_grace():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, max_turns=1)

    provider = _SequenceProvider(
        [
            # Iteration 1: tool call.
            AssistantMessage(
                content=[ToolCallContent(id="call_x", name="missing_tool", arguments={})],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            # Grace analysis response (FAST model).
            AssistantMessage(
                content=[TextContent(text='{"continue": true}')],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
            # Grace iteration.
            AssistantMessage(
                content=[TextContent(text="done")],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )
    runtime_support = _build_base_runtime_support(provider)
    orchestrator = SubAgentOrchestrator(runtime_support, _SessionFactory(db), ToolRegistry())

    _run(orchestrator.run_task(task.id))
    assert task.status == "completed"
    assert task.turns_used == 2
    assert isinstance(task.result, dict)
    assert int(task.result.get("iterations", 0)) == 2


def test_orchestrator_timeout_marks_task_failed():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, timeout_seconds=1)

    orchestrator = SubAgentOrchestrator(_build_base_runtime_support(_SlowProvider()), _SessionFactory(db), ToolRegistry())
    _run(orchestrator.run_task(task.id))

    assert task.status == "failed"
    assert "timed out" in (task.result or {}).get("error", "")


def test_orchestrator_start_task_returns_true_and_runs():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, timeout_seconds=2)

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[TextContent(text="done")],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                stop_reason="stop",
            )
        ]
    )
    runtime_support = _build_base_runtime_support(provider)
    orchestrator = SubAgentOrchestrator(runtime_support, _SessionFactory(db), ToolRegistry())

    async def _scenario():
        started = orchestrator.start_task(task.id)
        assert started is True
        await asyncio.sleep(0.05)

    _run(_scenario())
    assert task.status == "completed"


def test_orchestrator_complete_task_fallback_sets_completed_result():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, max_turns=3)

    orchestrator = SubAgentOrchestrator(None, _SessionFactory(db), ToolRegistry())
    _run(orchestrator.complete_task(db, task))

    assert task.status == "completed"
    assert isinstance(task.result, dict)
    assert "summary" in task.result


def test_orchestrator_cancel_task_marks_cancelled():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, timeout_seconds=5)

    orchestrator = SubAgentOrchestrator(_build_base_runtime_support(_SlowProvider()), _SessionFactory(db), ToolRegistry())

    async def _scenario():
        started = orchestrator.start_task(task.id)
        assert started is True
        await asyncio.sleep(0.05)
        cancelled = orchestrator.cancel_task(task.id)
        assert cancelled is True
        await asyncio.sleep(0.05)

    _run(_scenario())
    assert task.status == "cancelled"


def test_orchestrator_invokes_completion_callback():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, max_turns=1)

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[TextContent(text="done")],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                stop_reason="stop",
            )
        ]
    )
    runtime_support = _build_base_runtime_support(provider)
    seen: list[tuple[str, str]] = []

    async def _on_completed(item: SubAgentTask):
        seen.append((str(item.id), item.status))

    orchestrator = SubAgentOrchestrator(
        runtime_support,
        _SessionFactory(db),
        ToolRegistry(),
        on_task_completed=_on_completed,
    )
    _run(orchestrator.run_task(task.id))

    assert task.status == "completed"
    assert seen and seen[-1] == (str(task.id), "completed")
