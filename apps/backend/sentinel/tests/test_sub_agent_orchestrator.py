from __future__ import annotations

import asyncio
import json

from app.models import Message, Session, SubAgentTask
from app.services.agent.context_builder import ContextBuilder
from app.services.agent.runtime_support import SentinelRuntimeSupport
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import (
    AgentEvent,
    AssistantMessage,
    TextContent,
    ToolCallContent,
    TokenUsage,
)
from app.services.sub_agents.orchestrator import SubAgentOrchestrator
from app.services.tools import ToolDefinition, ToolExecutor, ToolRegistry
from app.services.tools.registry import ToolRuntimeContext
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

    async def chat(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        idx = min(self.calls, len(self._responses) - 1)
        _ = tool_choice
        self.calls += 1
        return self._responses[idx]

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        _ = tool_choice
        message = await self.chat(
            messages, model, tools, temperature, reasoning_config, tool_choice
        )
        message.provider_usage = {
            "usage": {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
            "price_kind": "api_list_price",
            "price": {"usd": "0.00012"},
        }
        for index, block in enumerate(message.content):
            if isinstance(block, TextContent):
                yield AgentEvent(type="text_delta", content_index=index, delta=block.text)
            elif isinstance(block, ToolCallContent):
                yield AgentEvent(type="toolcall_start", content_index=index, tool_call=block)
        yield AgentEvent(type="done", message=message, stop_reason=message.stop_reason)


class _SlowProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "slow"

    async def chat(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        await asyncio.sleep(2)
        raise AssertionError("unreachable")

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
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
        tool_registry,
        ToolExecutor(tool_registry),
    )


def _add_task(db: FakeDB, session: Session, **kwargs) -> SubAgentTask:
    task = SubAgentTask(
        session_id=session.id,
        objective=kwargs.get("objective", "do work"),
        context=kwargs.get("context", "scope"),
        constraints=[],
        allowed_tools=kwargs.get("allowed_tools", []),
        status="pending",
    )
    db.add(task)
    return task


def test_orchestrator_completes_and_creates_child_session_with_usage():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent)

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

    assert task.status == "completed", task.result
    assert task.turns_used == 1
    assert task.tokens_used == 12
    child_id = task.result["child_session_id"]
    child = next(s for s in db.storage[Session] if str(s.id) == child_id)
    assert child.parent_session_id == parent.id


def test_orchestrator_scopes_allowed_tools():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, allowed_tools=["allowed_tool"])

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

    assert task.status == "completed", task.result
    child_id = task.result["child_session_id"]
    tool_result = next(
        m for m in db.storage[Message] if str(m.session_id) == child_id and m.role == "tool_result"
    )
    assert tool_result.tool_name == "blocked_tool"
    assert "not registered" in tool_result.content


def test_sub_agents_do_not_receive_delegate_tool_or_policy():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent)

    registry = ToolRegistry()

    async def _delegate_exec(payload, runtime):
        return {"unexpected": True, "payload": payload, "session_id": str(runtime.session_id)}

    registry.register(
        ToolDefinition(
            name="delegate",
            description="delegate work",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_delegate_exec,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[
                    ToolCallContent(
                        id="call_delegate", name="delegate", arguments={"action": "spawn"}
                    )
                ],
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

    scoped = orchestrator._scoped_runtime_support(task)
    scoped_tools = {tool.name for tool in scoped.tool_registry.list_schemas()}
    assert "delegate" not in scoped_tools
    assert "delegate" not in getattr(scoped.context_builder, "_available_tools", set())

    _run(orchestrator.run_task(task.id))

    assert task.status == "completed", task.result
    child_id = task.result["child_session_id"]
    tool_result = next(
        m for m in db.storage[Message] if str(m.session_id) == child_id and m.role == "tool_result"
    )
    assert tool_result.tool_name == "delegate"
    assert "not registered" in tool_result.content
    child_session = next(s for s in db.storage[Session] if str(s.id) == child_id)
    assert "## Delegation Policy" not in (child_session.latest_system_prompt or "")


def test_orchestrator_reports_actual_iterations_without_budget():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent)

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
            # Next automatic response.
            AssistantMessage(
                content=[TextContent(text='{"continue": true}')],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
            # Unused response after completion.
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
    assert task.status == "completed", task.result
    assert task.turns_used == 2
    assert isinstance(task.result, dict)
    assert provider.calls == 2


def test_orchestrator_uses_parent_runtime_session_for_runtime_bound_tools():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent, allowed_tools=["inspect_runtime"])

    registry = ToolRegistry()

    async def _inspect_runtime(_payload: dict, runtime: ToolRuntimeContext):
        return {
            "history_session_id": str(runtime.session_id),
            "runtime_session_id": str(runtime.runtime_session_id),
        }

    registry.register(
        ToolDefinition(
            name="inspect_runtime",
            description="inspect runtime binding",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_inspect_runtime,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_runtime", name="inspect_runtime", arguments={})],
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

    assert task.status == "completed", task.result
    child_id = task.result["child_session_id"]
    tool_result = next(
        m for m in db.storage[Message] if str(m.session_id) == child_id and m.role == "tool_result"
    )
    payload = json.loads(tool_result.content)
    assert payload["history_session_id"] == child_id
    assert payload["runtime_session_id"] == str(parent.id)


def test_orchestrator_start_task_returns_true_and_runs():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent)

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
    assert task.status == "completed", task.result


def test_orchestrator_without_runtime_reports_failure():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent)

    orchestrator = SubAgentOrchestrator(None, _SessionFactory(db), ToolRegistry())
    _run(orchestrator.complete_task(db, task))

    assert task.status == "failed"
    assert "unavailable" in task.result["error"]


def test_orchestrator_cancel_task_marks_cancelled():
    db = FakeDB()
    parent = Session(user_id="dev-admin", status="active", title="parent")
    db.add(parent)
    task = _add_task(db, parent)

    orchestrator = SubAgentOrchestrator(
        _build_base_runtime_support(_SlowProvider()), _SessionFactory(db), ToolRegistry()
    )

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
    task = _add_task(db, parent)

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

    assert task.status == "completed", task.result
    assert seen and seen[-1] == (str(task.id), "completed")
