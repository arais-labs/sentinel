from __future__ import annotations

import asyncio

from app.models import Message, Session
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import (
    AgentEvent,
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    TokenUsage,
    UserMessage,
)
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolDefinition, ToolRegistry
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


class _SequenceProvider(LLMProvider):
    def __init__(self, responses: list[AssistantMessage]) -> None:
        self._responses = responses
        self.calls = 0
        self.message_batches: list[list] = []

    @property
    def name(self) -> str:
        return "sequence"

    async def chat(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        idx = min(self.calls, len(self._responses) - 1)
        self.message_batches.append(list(messages))
        self.calls += 1
        return self._responses[idx]

    async def stream(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


class _StreamingProvider(LLMProvider):
    def __init__(
        self,
        scripts: list[list[AgentEvent]],
        *,
        chat_response: AssistantMessage | None = None,
        raise_on_stream: Exception | None = None,
    ) -> None:
        self._scripts = scripts
        self._chat_response = chat_response or AssistantMessage(content=[TextContent(text="chat-fallback")])
        self._raise_on_stream = raise_on_stream
        self.stream_calls = 0
        self.chat_calls = 0

    @property
    def name(self) -> str:
        return "streaming"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.chat_calls += 1
        return self._chat_response

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.stream_calls += 1
        if self._raise_on_stream is not None:
            raise self._raise_on_stream

        if not self._scripts:
            yield AgentEvent(type="done", stop_reason="stop")
            return

        idx = min(self.stream_calls - 1, len(self._scripts) - 1)
        for event in self._scripts[idx]:
            yield event


class _SlowProvider(LLMProvider):
    def __init__(self, sleep_seconds: float = 0.2) -> None:
        self._sleep_seconds = sleep_seconds
        self.chat_calls = 0

    @property
    def name(self) -> str:
        return "slow"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = messages, model, tools, temperature
        self.chat_calls += 1
        await asyncio.sleep(self._sleep_seconds)
        return AssistantMessage(
            content=[TextContent(text="late")],
            model="m",
            provider="p",
            usage=TokenUsage(),
            stop_reason="stop",
        )

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = messages, model, tools, temperature
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


def _build_loop(provider: LLMProvider, registry: ToolRegistry | None = None) -> AgentLoop:
    tool_registry = registry or ToolRegistry()
    adapter = ToolAdapter(tool_registry, ToolExecutor(tool_registry))
    return AgentLoop(provider, ContextBuilder(default_system_prompt="System prompt"), adapter)


def _new_session(db: FakeDB, user_id: str = "dev-admin") -> Session:
    session = Session(user_id=user_id, status="active", title="test")
    db.add(session)
    return session


def test_agent_loop_text_only_persists_messages_and_emits_done():
    db = FakeDB()
    session = _new_session(db)
    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[TextContent(text="All set")],
                model="m1",
                provider="p1",
                usage=TokenUsage(input_tokens=3, output_tokens=7),
                stop_reason="stop",
            )
        ]
    )
    loop = _build_loop(provider)

    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "hello", stream=False, on_event=_capture))
    assert result.final_text == "All set"
    assert result.iterations == 1
    assert result.messages_created == 2
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 7

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    conversational_saved = [
        m
        for m in saved
        if not (
            m.role == "system"
            and isinstance(m.metadata_json, dict)
            and str(m.metadata_json.get("source") or "").strip().lower() == "runtime_context"
        )
    ]
    assert [m.role for m in conversational_saved] == ["user", "assistant"]
    assert any(event.type == "text_delta" and event.delta == "All set" for event in events)
    assert any(event.type == "done" and event.stop_reason == "stop" for event in events)


def test_agent_loop_tool_use_path_runs_tool_and_finishes_second_iteration():
    db = FakeDB()
    session = _new_session(db)

    registry = ToolRegistry()

    async def _tool_exec(payload):
        return {
            "value": payload.get("query"),
            "token": "sk-proj-abc123def456ghi789jkl",
        }

    registry.register(
        ToolDefinition(
            name="lookup",
            description="Lookup",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_tool_exec,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[
                    ToolCallContent(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "x"},
                        thought_signature="sig-call-1",
                    )
                ],
                model="m1",
                provider="p1",
                usage=TokenUsage(input_tokens=2, output_tokens=1),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="Tool complete")],
                model="m1",
                provider="p1",
                usage=TokenUsage(input_tokens=4, output_tokens=2),
                stop_reason="stop",
            ),
        ]
    )

    loop = _build_loop(provider, registry)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "run lookup", stream=False, on_event=_capture))
    assert result.iterations == 2
    assert result.final_text == "Tool complete"

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    saved_non_system = [m for m in saved if m.role != "system"]
    roles = [m.role for m in saved_non_system]
    assert roles == ["user", "assistant", "tool_result", "assistant"]
    first_assistant = next(m for m in saved_non_system if m.role == "assistant")
    tool_calls_meta = (first_assistant.metadata_json or {}).get("tool_calls") or []
    assert isinstance(tool_calls_meta, list) and tool_calls_meta
    assert tool_calls_meta[0]["thought_signature"] == "sig-call-1"
    tool_result = next(m for m in saved_non_system if m.role == "tool_result")
    assert tool_result.tool_call_id == "call_1"
    assert tool_result.tool_name == "lookup"
    assert "sk-proj-abc123def456ghi789jkl" not in tool_result.content
    assert "sk-pro" in tool_result.content

    second_batch = provider.message_batches[1]
    replayed_assistant = next(
        msg
        for msg in second_batch
        if isinstance(msg, AssistantMessage)
        and any(
            isinstance(block, ToolCallContent) and block.id == "call_1"
            for block in msg.content
        )
    )
    replayed_tool_call = next(
        block
        for block in replayed_assistant.content
        if isinstance(block, ToolCallContent) and block.id == "call_1"
    )
    assert replayed_tool_call.thought_signature == "sig-call-1"

    event_types = [e.type for e in events]
    assert "toolcall_start" in event_types
    assert "toolcall_end" in event_types
    assert event_types.count("done") == 1


def test_agent_loop_stream_uses_last_done_event_and_does_not_drop_late_chunks():
    db = FakeDB()
    session = _new_session(db)

    provider = _StreamingProvider(
        [
            [
                AgentEvent(type="start"),
                AgentEvent(type="text_start", content_index=0),
                AgentEvent(type="text_delta", content_index=0, delta="A"),
                AgentEvent(type="done", stop_reason="stop"),
                AgentEvent(type="text_delta", content_index=0, delta="B"),
                AgentEvent(type="text_end", content_index=0),
                AgentEvent(type="done", stop_reason="stop"),
            ]
        ]
    )

    loop = _build_loop(provider)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "hello", on_event=_capture))
    assert result.final_text == "AB"
    assert result.iterations == 1
    assert [event.type for event in events].count("done") == 1


def test_agent_loop_stream_keeps_tool_call_when_empty_text_starts_same_index():
    db = FakeDB()
    session = _new_session(db)

    registry = ToolRegistry()

    async def _tool_exec(payload):
        return {"value": payload.get("query")}

    registry.register(
        ToolDefinition(
            name="lookup",
            description="Lookup",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_tool_exec,
        )
    )

    provider = _StreamingProvider(
        [
            [
                AgentEvent(type="start"),
                AgentEvent(
                    type="toolcall_start",
                    content_index=0,
                    tool_call=ToolCallContent(id="call_1", name="lookup", arguments={}),
                ),
                AgentEvent(type="toolcall_delta", content_index=0, delta='{"query":"x"}'),
                AgentEvent(type="toolcall_end", content_index=0),
                AgentEvent(type="text_start", content_index=0),
                AgentEvent(type="text_end", content_index=0),
                AgentEvent(type="done", stop_reason="tool_use"),
            ],
            [
                AgentEvent(type="start"),
                AgentEvent(type="text_start", content_index=0),
                AgentEvent(type="text_delta", content_index=0, delta="ok"),
                AgentEvent(type="text_end", content_index=0),
                AgentEvent(type="done", stop_reason="stop"),
            ],
        ]
    )

    loop = _build_loop(provider, registry)
    result = _run(loop.run(db, session.id, "run lookup", stream=True))
    assert result.final_text == "ok"
    assert result.iterations == 2

    saved = [m for m in db.storage[Message] if m.session_id == session.id and m.role != "system"]
    assert [m.role for m in saved] == ["user", "assistant", "tool_result", "assistant"]


def test_agent_loop_reinjects_tool_screenshot_as_image_content():
    db = FakeDB()
    session = _new_session(db)

    registry = ToolRegistry()
    png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s8bVgAAAABJRU5ErkJggg=="

    async def _screenshot_tool(_payload):
        return {"image_base64": png_b64}

    registry.register(
        ToolDefinition(
            name="browser_screenshot",
            description="Screenshot",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_screenshot_tool,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_img_1", name="browser_screenshot", arguments={})],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="I reviewed the screenshot.")],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )
    loop = _build_loop(provider, registry)

    _run(loop.run(db, session.id, "inspect page", stream=False))
    assert len(provider.message_batches) >= 2

    second_call_messages = provider.message_batches[1]
    reinjected = [
        msg
        for msg in second_call_messages
        if isinstance(msg, UserMessage)
        and isinstance(msg.content, list)
        and msg.metadata.get("source") == "tool_image_reinjection"
    ]
    assert reinjected, "expected tool image reinjection message in second provider call"
    image_blocks = [
        block
        for block in reinjected[0].content
        if isinstance(block, ImageContent)
    ]
    assert image_blocks
    assert image_blocks[0].data == png_b64


def test_agent_loop_auto_injects_session_id_for_tools_that_declare_it():
    db = FakeDB()
    session = _new_session(db)

    captured: dict[str, str] = {}
    registry = ToolRegistry()

    async def _echo_session(payload):
        captured["session_id"] = str(payload.get("session_id"))
        return {"session_id": payload.get("session_id")}

    registry.register(
        ToolDefinition(
            name="session_echo",
            description="Echo current session id",
            risk_level="low",
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["session_id"],
                "properties": {
                    "session_id": {"type": "string"},
                },
            },
            execute=_echo_session,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_sid", name="session_echo", arguments={})],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="ok")],
                model="m",
                provider="p",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )

    loop = _build_loop(provider, registry)
    result = _run(loop.run(db, session.id, "echo sid", stream=False))

    assert result.final_text == "ok"
    assert captured["session_id"] == str(session.id)


def test_agent_loop_runs_finalization_round_after_max_iterations():
    db = FakeDB()
    session = _new_session(db)

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_loop", name="missing_tool", arguments={})],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="tool_use",
            )
        ]
    )

    loop = _build_loop(provider)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "loop", max_iterations=3, stream=False, on_event=_capture))
    assert result.iterations == 4
    assert provider.calls == 5
    assert any(
        isinstance(batch[-1], UserMessage)
        and "reached its iteration limit" in batch[-1].content
        for batch in provider.message_batches
        if batch
    )
    assert any(event.type == "done" and event.stop_reason == "stop" for event in events)
    assert result.final_text


def test_agent_loop_tool_errors_do_not_crash_and_are_persisted_as_error():
    db = FakeDB()
    session = _new_session(db)

    registry = ToolRegistry()

    async def _broken(_payload):
        raise RuntimeError("ghp_123456789012345678901234567890123456 failed")

    registry.register(
        ToolDefinition(
            name="broken",
            description="Broken",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_broken,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_broken", name="broken", arguments={})],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="Recovered")],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )

    loop = _build_loop(provider, registry)
    result = _run(loop.run(db, session.id, "use broken", stream=False))
    assert result.final_text == "Recovered"

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    tool_record = next(m for m in saved if m.role == "tool_result")
    assert tool_record.metadata_json.get("is_error") is True
    assert "ghp_123456789012345678901234567890123456" not in tool_record.content
    assert "ghp_12" in tool_record.content


def test_agent_loop_streaming_text_emits_incremental_events():
    db = FakeDB()
    session = _new_session(db)
    provider = _StreamingProvider(
        [
            [
                AgentEvent(type="start"),
                AgentEvent(type="text_start", content_index=0),
                AgentEvent(type="text_delta", content_index=0, delta="All "),
                AgentEvent(type="text_delta", content_index=0, delta="set"),
                AgentEvent(type="text_end", content_index=0),
                AgentEvent(type="done", stop_reason="stop"),
            ]
        ]
    )
    loop = _build_loop(provider)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "hello stream", on_event=_capture))
    assert result.final_text == "All set"
    deltas = [event.delta for event in events if event.type == "text_delta"]
    assert deltas == ["All ", "set"]
    assert any(event.type == "text_start" for event in events)
    assert any(event.type == "text_end" for event in events)
    assert any(event.type == "done" and event.stop_reason == "stop" for event in events)
    assert provider.stream_calls == 1
    assert provider.chat_calls == 0


def test_agent_loop_streaming_tool_use_assembles_arguments_for_execution():
    db = FakeDB()
    session = _new_session(db)
    captured: dict[str, str] = {}

    registry = ToolRegistry()

    async def _tool_exec(payload):
        captured.update(payload)
        return {"ok": True}

    registry.register(
        ToolDefinition(
            name="lookup",
            description="Lookup",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_tool_exec,
        )
    )

    provider = _StreamingProvider(
        [
            [
                AgentEvent(type="start"),
                AgentEvent(
                    type="toolcall_start",
                    content_index=1,
                    tool_call=ToolCallContent(id="call_1", name="lookup", arguments={}),
                ),
                AgentEvent(type="toolcall_delta", content_index=1, delta='{"query":"x"}'),
                AgentEvent(type="toolcall_end", content_index=1),
                AgentEvent(type="done", stop_reason="stop"),
            ],
            [
                AgentEvent(type="start"),
                AgentEvent(type="text_start", content_index=0),
                AgentEvent(type="text_delta", content_index=0, delta="Tool complete"),
                AgentEvent(type="text_end", content_index=0),
                AgentEvent(type="done", stop_reason="stop"),
            ],
        ]
    )

    loop = _build_loop(provider, registry)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "run streaming tool", on_event=_capture))
    assert result.iterations == 2
    assert result.final_text == "Tool complete"
    assert captured == {"query": "x"}

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    non_system_roles = [m.role for m in saved if m.role != "system"]
    assert non_system_roles == ["user", "assistant", "tool_result", "assistant"]
    assert any(event.type == "toolcall_delta" for event in events)


def test_agent_loop_stream_false_falls_back_to_chat_provider():
    db = FakeDB()
    session = _new_session(db)
    provider = _StreamingProvider(
        scripts=[
            [
                AgentEvent(type="text_delta", content_index=0, delta="unused"),
                AgentEvent(type="done", stop_reason="stop"),
            ]
        ],
        chat_response=AssistantMessage(
            content=[TextContent(text="chat-only")],
            model="m-chat",
            provider="p-chat",
            usage=TokenUsage(input_tokens=1, output_tokens=2),
            stop_reason="stop",
        ),
    )

    loop = _build_loop(provider)
    result = _run(loop.run(db, session.id, "prefer chat", stream=False))
    assert result.final_text == "chat-only"
    assert provider.chat_calls == 1
    assert provider.stream_calls == 0


def test_agent_loop_stream_errors_emit_error_event_and_stop_gracefully():
    db = FakeDB()
    session = _new_session(db)
    provider = _StreamingProvider([], raise_on_stream=RuntimeError("upstream failure"))
    loop = _build_loop(provider)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "hello", on_event=_capture))
    assert "Error" in result.final_text
    assert "upstream failure" in result.final_text
    assert result.iterations == 1
    assert any(event.type == "error" and "upstream failure" in (event.error or "") for event in events)
    assert any(event.type == "done" and event.stop_reason == "error" for event in events)

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    conversational_saved = [
        m
        for m in saved
        if not (
            m.role == "system"
            and isinstance(m.metadata_json, dict)
            and str(m.metadata_json.get("source") or "").strip().lower() == "runtime_context"
        )
    ]
    assert [m.role for m in conversational_saved] == ["user", "assistant"]


def test_tool_result_content_is_truncated_before_persist():
    db = FakeDB()
    session = _new_session(db)

    registry = ToolRegistry()

    async def _big_tool(_payload):
        return {"blob": "x" * 60_000}

    registry.register(
        ToolDefinition(
            name="big",
            description="Big",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_big_tool,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="call_big", name="big", arguments={})],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="ok")],
                model="m1",
                provider="p1",
                usage=TokenUsage(),
                stop_reason="stop",
            ),
        ]
    )

    loop = _build_loop(provider, registry)
    _run(loop.run(db, session.id, "big result", stream=False))

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    tool_record = next(m for m in saved if m.role == "tool_result")
    assert "[TRUNCATED - " in tool_record.content


def test_agent_loop_timeout_emits_done_timeout_and_persists_partial_state():
    db = FakeDB()
    session = _new_session(db)
    provider = _SlowProvider(sleep_seconds=0.5)
    loop = _build_loop(provider)
    events: list[AgentEvent] = []

    async def _capture(event: AgentEvent) -> None:
        events.append(event)

    result = _run(loop.run(db, session.id, "hello timeout", stream=False, timeout_seconds=0.05, on_event=_capture))
    assert "Error" in result.final_text
    assert "timed out" in result.final_text
    assert any(event.type == "done" and event.stop_reason == "timeout" for event in events)

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    conversational_saved = [
        m
        for m in saved
        if not (
            m.role == "system"
            and isinstance(m.metadata_json, dict)
            and str(m.metadata_json.get("source") or "").strip().lower() == "runtime_context"
        )
    ]
    assert [m.role for m in conversational_saved] == ["user", "assistant"]


def test_agent_loop_persists_distinct_created_at_order_and_iteration_metadata():
    db = FakeDB()
    session = _new_session(db)

    registry = ToolRegistry()

    async def _lookup(payload):
        return {"value": payload.get("query")}

    registry.register(
        ToolDefinition(
            name="lookup",
            description="Lookup",
            risk_level="low",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=_lookup,
        )
    )

    provider = _SequenceProvider(
        [
            AssistantMessage(
                content=[ToolCallContent(id="c1", name="lookup", arguments={"query": "x"})],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                stop_reason="tool_use",
            ),
            AssistantMessage(
                content=[TextContent(text="done")],
                model="m",
                provider="p",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                stop_reason="stop",
            ),
        ]
    )
    loop = _build_loop(provider, registry)
    _run(loop.run(db, session.id, "check ordering", stream=False))

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    created_ats = [item.created_at for item in saved]
    assert all(created_ats[idx] < created_ats[idx + 1] for idx in range(len(created_ats) - 1))
    assert len(set(created_ats)) == len(created_ats)

    assistants = [item for item in saved if item.role == "assistant"]
    assert assistants[0].metadata_json.get("iteration") == 1
    assert assistants[1].metadata_json.get("iteration") == 2
