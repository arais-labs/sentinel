"""Replies cut off by the output cap, and attempts that fail mid-stream.

A cut-off is a normal outcome for every provider: stop_reason "length", unfinished calls
flagged and never run, a note to the model, and the loop continues. A failed attempt is
discarded whole: the retry or fallback answers the same request with the same history.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.llm.tier import TierConfig, TierModelConfig, TierProvider
from sentral import (
    AgentRuntimeEngine,
    ConversationItem,
    GenerationConfig,
    RunTurnRequest,
    TextBlock,
    ToolCallBlock,
    ToolDefinition,
    ToolExecutionResult,
)
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.errors import TransientProviderError
from sentral.llm.generic.types import (
    AgentEvent,
    AssistantMessage,
    ReasoningConfig,
    TextContent,
    ToolCallContent,
    UserMessage,
)
from sentral.llm.ids import TierName
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.providers.codex import CodexProvider
from sentral.llm.providers.gemini import GeminiProvider
from sentral.llm.providers.ollama import OllamaProvider
from sentral.llm.providers.openai import OpenAIProvider
from sentral.llm.runtime_adapter import SentinelProviderAdapter
from tests.test_llm_providers import _FakeAsyncClient, _FakeResponse, _FakeStreamResponse

PARTIAL = '{"files": {"distribution.json": {"text": "{\\"name\\": \\"Omarchy'


def _sse(events):
    return _FakeStreamResponse(["data: " + json.dumps(event) for event in events])


def _claude(*events_per_call):
    return [_sse(events) for events in events_per_call]


def _claude_cut_off_in_call(*, complete_call=True):
    """Claude's real shape when max_tokens hits inside a tool_use block: no block stop."""
    events = [{"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 10}}}]
    if complete_call:
        events += [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "toolu_a", "name": "runtime"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"action": "status"}'},
            },
            {"type": "content_block_stop", "index": 0},
        ]
    events += [
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_cut", "name": "workspaces"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": PARTIAL},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "max_tokens"},
            "usage": {"output_tokens": 8192},
        },
        {"type": "message_stop"},
    ]
    return events


def _claude_text(text, stop="end_turn"):
    return [
        {"type": "message_start", "message": {"content": []}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]


def _responses_lines(response, items=()):
    lines = ['data: {"type":"response.created"}']
    for index, item in items:
        lines.append(
            "data: "
            + json.dumps(
                {"type": "response.output_item.added", "output_index": index, "item": item}
            )
        )
    kind = "response.incomplete" if response.get("status") == "incomplete" else "response.completed"
    lines.append("data: " + json.dumps({"type": kind, "response": response}))
    return _FakeStreamResponse(lines)


def _responses_cut_off():
    done = {
        "type": "function_call",
        "id": "fc_a",
        "call_id": "call_a",
        "name": "runtime",
        "arguments": '{"action": "status"}',
        "status": "completed",
    }
    cut = {
        "type": "function_call",
        "id": "fc_cut",
        "call_id": "call_cut",
        "name": "workspaces",
        "arguments": PARTIAL,
        "status": "incomplete",
    }
    return _responses_lines(
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [done, cut],
            "usage": {"input_tokens": 10, "output_tokens": 8192},
        },
        items=[(0, done), (1, {**cut, "arguments": ""})],
    )


def _responses_text(text):
    return _responses_lines(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


def _chunks(*chunks):
    return _FakeStreamResponse(
        ["data: " + json.dumps(chunk) for chunk in chunks] + ["data: [DONE]"]
    )


def _completions_cut_off():
    calls = [
        {"index": 0, "id": "call_a", "function": {"name": "runtime", "arguments": ""}},
        {"index": 1, "id": "call_cut", "function": {"name": "workspaces", "arguments": ""}},
    ]
    return _chunks(
        {"choices": [{"delta": {"tool_calls": calls}}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '{"action": "status"}'}},
                            {"index": 1, "function": {"arguments": PARTIAL}},
                        ]
                    }
                }
            ]
        },
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
    )


def _completions_text(text):
    return _chunks(
        {"choices": [{"delta": {"content": text}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    )


def _gemini(parts, finish):
    chunk = {"candidates": [{"content": {"parts": parts}, "finishReason": finish}]}
    return _FakeStreamResponse(["data: " + json.dumps(chunk)])


def _ollama(message, reason):
    return _FakeStreamResponse(
        [json.dumps({"message": message, "done": True, "done_reason": reason})]
    )


class _Codex(CodexProvider):
    """Codex with an empty model catalog (no network)."""

    async def _load_model_catalog(self, client):
        return {}


# Each provider: (provider, model, streams for the two calls, ids of the finished calls,
# ids of the cut-off calls). The first reply is cut off by the output cap.
def _anthropic_case():
    client = _FakeAsyncClient(
        stream_responses=_claude(_claude_cut_off_in_call(), _claude_text("Done."))
    )
    return AnthropicProvider("k", client_factory=lambda: client), "claude-opus-5-5", client


def _openai_case():
    client = _FakeAsyncClient(stream_responses=[_responses_cut_off(), _responses_text("Done.")])
    return OpenAIProvider("k", client_factory=lambda: client), "gpt-4.1", client


def _codex_case():
    client = _FakeAsyncClient(stream_responses=[_responses_cut_off(), _responses_text("Done.")])
    provider = _Codex("token", client_factory=lambda: client, transport="sse")
    return provider, "gpt-5.3-codex-spark", client


def _completions_case():
    client = _FakeAsyncClient(stream_responses=[_completions_cut_off(), _completions_text("Done.")])
    provider = OpenAIProvider(
        "k", base_url="https://compatible.test/v1", client_factory=lambda: client
    )
    return provider, "local-model", client


def _gemini_case():
    client = _FakeAsyncClient(
        stream_responses=[
            _gemini(
                [{"functionCall": {"name": "runtime", "args": {"action": "status"}}}], "MAX_TOKENS"
            ),
            _gemini([{"text": "Done."}], "STOP"),
        ]
    )
    return GeminiProvider("k", client_factory=lambda: client), "gemini-3.8-flash", client


def _ollama_case():
    client = _FakeAsyncClient(
        stream_responses=[
            _ollama(
                {
                    "tool_calls": [
                        {
                            "id": "call_a",
                            "function": {"name": "runtime", "arguments": {"action": "status"}},
                        }
                    ]
                },
                "length",
            ),
            _ollama({"content": "Done."}, "stop"),
        ]
    )
    return OllamaProvider("http://localhost:11434", client_factory=lambda: client), "qwen3", client


class _Tools:
    def __init__(self):
        self.calls = []

        async def run(arguments, name):
            self.calls.append((name, arguments))
            return ToolExecutionResult(status="ok", content={"ok": True})

        self._tools = {
            name: ToolDefinition(
                name=name,
                description=name,
                parameters_schema={"type": "object"},
                execute=lambda arguments, name=name: run(arguments, name),
            )
            for name in ("runtime", "workspaces")
        }

    def list_tools(self):
        return list(self._tools.values())

    def get_tool(self, name):
        return self._tools.get(name)


async def _run_engine(provider, model, tools=None):
    tools = tools or _Tools()
    events = []

    async def sink(event):
        events.append(event)

    engine = AgentRuntimeEngine(provider=SentinelProviderAdapter(provider), tool_registry=tools)
    result = await engine.run_turn(
        RunTurnRequest(
            new_items=[ConversationItem(id="u", role="user", content=[TextBlock(text="go")])],
            config=GenerationConfig(model=model, stream=True),
        ),
        sink=sink,
    )
    return result, events, tools


def _stored_calls(result):
    return [
        block.id
        for item in result.history
        for block in item.content
        if isinstance(block, ToolCallBlock)
    ]


def _notes(result):
    return [
        item
        for item in result.history
        if item.metadata.get("notice", {}).get("title") == "Reply cut off"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,finished,cut",
    [
        (_anthropic_case, ["toolu_a"], ["toolu_cut"]),
        (_openai_case, ["call_a"], ["call_cut"]),
        (_codex_case, ["call_a"], ["call_cut"]),
        (_completions_case, ["call_a"], ["call_cut"]),
        (_gemini_case, None, []),
        (_ollama_case, ["call_a"], []),
    ],
    ids=["anthropic", "openai", "codex", "chat-completions", "gemini", "ollama"],
)
async def test_cut_off_reply_runs_finished_calls_notes_the_rest_and_continues(case, finished, cut):
    provider, model, client = case()

    result, events, tools = await _run_engine(provider, model)

    assert result.status == "completed"
    assert len(client.stream_calls) == 2  # the loop continued after the cut-off
    assert tools.calls == [("runtime", {"action": "status"})]  # cut calls never run
    stored = _stored_calls(result)
    assert len(stored) == 1 and (finished is None or stored == finished)
    assert not set(cut) & set(stored)
    tool_results = [item.content[0].tool_call_id for item in result.history if item.role == "tool"]
    assert tool_results == stored  # no result for a call that was never stored
    [note] = _notes(result)
    text = note.content[0].text
    assert "hit the output limit" in text and "Continue" in text
    if cut:
        assert "the unfinished call to workspaces was not run" in text
    # The note follows the finished call's result, so every history format accepts it.
    roles = [item.role for item in result.history]
    assert roles[roles.index("tool") + 1] == "user"
    assert [e.tool_call.id for e in events if e.type == "toolcall_discard"] == cut
    final = result.history[-1]
    assert final.role == "assistant" and final.content[0].text == "Done."


@pytest.mark.asyncio
async def test_claude_cut_off_history_replays_without_the_unfinished_call():
    provider, model, client = _anthropic_case()

    await _run_engine(provider, model)

    messages = client.stream_calls[1]["json"]["messages"]
    replayed = json.dumps(messages)
    assert "toolu_cut" not in replayed
    assert messages[-2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_a",
        "content": json.dumps({"ok": True}),
        "is_error": False,
    }
    assert "hit the output limit (8192 tokens)" in messages[-1]["content"][0]["text"]


@pytest.mark.asyncio
async def test_responses_cut_off_history_has_no_orphan_call():
    provider, model, client = _codex_case()

    await _run_engine(provider, model)

    items = client.stream_calls[1]["json"]["input"]
    calls = {item["call_id"] for item in items if item.get("type") == "function_call"}
    outputs = {item["call_id"] for item in items if item.get("type") == "function_call_output"}
    assert calls == outputs == {"call_a"}


@pytest.mark.asyncio
async def test_text_only_cut_off_keeps_the_text_and_continues():
    client = _FakeAsyncClient(
        stream_responses=_claude(_claude_text("Part one", "max_tokens"), _claude_text("Part two"))
    )

    result, _events, tools = await _run_engine(
        AnthropicProvider("k", client_factory=lambda: client), "claude-opus-5-5"
    )

    assert result.status == "completed"
    texts = [
        block.text for item in result.history if item.role == "assistant" for block in item.content
    ]
    assert texts == ["Part one", "Part two"]
    [note] = _notes(result)
    assert "unfinished call" not in note.content[0].text
    assert tools.calls == []


@pytest.mark.asyncio
async def test_repeated_cut_offs_stop_with_a_clear_error():
    client = _FakeAsyncClient(
        stream_responses=_claude(*[_claude_cut_off_in_call(complete_call=False)] * 4)
    )

    result, events, tools = await _run_engine(
        AnthropicProvider("k", client_factory=lambda: client), "claude-opus-5-5"
    )

    assert len(client.stream_calls) == 3
    assert result.status == "error"
    assert "output limit 3 times in a row" in result.error
    assert any(e.type == "error" and "output limit" in e.error for e in events)
    assert len(_notes(result)) == 2
    assert tools.calls == [] and _stored_calls(result) == []


def test_claude_non_streamed_cut_off_flags_the_trailing_call():
    data = {
        "content": [
            {"type": "text", "text": "Writing"},
            {"type": "tool_use", "id": "toolu_cut", "name": "workspaces", "input": {}},
        ],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 1, "output_tokens": 8192},
    }
    client = _FakeAsyncClient(post_response=_FakeResponse(data))
    provider = AnthropicProvider("k", client_factory=lambda: client)

    message = asyncio.run(provider.chat([UserMessage(content="go")], "claude-opus-5-5"))

    assert message.stop_reason == "length"
    assert [call.id for call in message.incomplete_tool_calls] == ["toolu_cut"]
    assert [type(block) for block in message.content] == [TextContent]
    assert all(block["type"] == "text" for block in message.responses_output)


def test_chat_completions_non_streamed_cut_off_flags_unparseable_call():
    data = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "tool_calls": [
                        {"id": "call_cut", "function": {"name": "workspaces", "arguments": PARTIAL}}
                    ]
                },
            }
        ]
    }
    client = _FakeAsyncClient(post_response=_FakeResponse(data))
    provider = OpenAIProvider(
        "k", base_url="https://compatible.test/v1", client_factory=lambda: client
    )

    message = asyncio.run(provider.chat([UserMessage(content="go")], "local-model"))

    assert message.stop_reason == "length"
    assert message.content == []
    assert [call.id for call in message.incomplete_tool_calls] == ["call_cut"]


def test_gemini_cap_spent_on_thinking_is_a_cut_off_not_an_error():
    client = _FakeAsyncClient(stream_responses=[_gemini([], "MAX_TOKENS")])
    provider = GeminiProvider("k", client_factory=lambda: client)

    async def collect():
        return [event async for event in provider.stream([UserMessage(content="go")])]

    events = asyncio.run(collect())

    assert events[-1].type == "done" and events[-1].stop_reason == "length"


# --- Failed attempts: retry and fallback on a clean slate ---------------------------------


def _claude_dies_in_call():
    """Claude streams thinking and half a call, then the stream fails (an SSE error)."""
    return [
        {"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 10}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "drafting"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_dead", "name": "workspaces"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": PARTIAL},
        },
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]


class _ScriptedProvider(LLMProvider):
    """A provider whose replies are scripted; records the history of every request."""

    def __init__(self, name, replies):
        self._name = name
        self._replies = list(replies)
        self.requests = []

    @property
    def name(self):
        return self._name

    async def chat(self, *args, **kwargs):
        raise NotImplementedError

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        self.requests.append(list(messages))
        reply = self._replies.pop(0)
        yield AgentEvent(type="start")
        if reply == "fail-mid-call":
            yield AgentEvent(type="text_delta", delta="Partial words", content_index=0)
            yield AgentEvent(
                type="toolcall_start",
                content_index=1,
                tool_call=ToolCallContent(id="call_dead", name="workspaces"),
            )
            yield AgentEvent(type="toolcall_delta", content_index=1, delta=PARTIAL)
            raise TransientProviderError("connection dropped")
        if reply == "call":
            yield AgentEvent(
                type="toolcall_start",
                content_index=0,
                tool_call=ToolCallContent(id="call_4204", name="workspaces"),
            )
            yield AgentEvent(type="toolcall_delta", content_index=0, delta='{"action": "source"}')
            yield AgentEvent(
                type="done",
                stop_reason="tool_use",
                message=AssistantMessage(
                    model="gpt-6.1-sol",
                    provider=self._name,
                    responses_output=[
                        {
                            "type": "function_call",
                            "call_id": "call_4204",
                            "name": "workspaces",
                            "arguments": '{"action": "source"}',
                        }
                    ],
                ),
            )
            return
        yield AgentEvent(type="text_delta", delta=reply, content_index=0)
        yield AgentEvent(type="done", stop_reason="stop")


def _tier(*configs, retries=1):
    rc = ReasoningConfig()
    models = [
        TierModelConfig(provider=provider, model=model, reasoning_config=rc)
        for provider, model in configs
    ]
    tier = TierProvider(
        {TierName.NORMAL: TierConfig(primary=models[0], fallbacks=models[1:])},
        max_retries=retries,
        sleep_func=lambda _: asyncio.sleep(0),
    )
    tier._announce_fallback = lambda *args: None
    return tier


def _assert_clean_turn(result, events, tools, dead_id):
    """The failed attempt left nothing behind: no call, no result, no mixed turn."""
    assert result.status == "completed"
    assert tools.calls == [("workspaces", {"action": "source"})]
    assert _stored_calls(result) == ["call_4204"]
    assert [item.content[0].tool_call_id for item in result.history if item.role == "tool"] == [
        "call_4204"
    ]
    assert dead_id not in json.dumps([item.metadata for item in result.history], default=str)
    assert any(e.type == "stream_reset" for e in events)
    assert [e.tool_call.id for e in events if e.type == "toolcall_discard"] == [dead_id]
    first_turn = next(item for item in result.history if item.role == "assistant")
    assert first_turn.content == [
        ToolCallBlock(id="call_4204", name="workspaces", arguments={"action": "source"})
    ]


@pytest.mark.asyncio
async def test_claude_failing_mid_call_falls_back_with_the_same_clean_history():
    client = _FakeAsyncClient(stream_responses=_claude(_claude_dies_in_call()))
    claude = AnthropicProvider("k", client_factory=lambda: client)
    codex = _ScriptedProvider("openai-codex", ["call", "Done."])

    result, events, tools = await _run_engine(
        _tier((claude, "claude-opus-5-5"), (codex, "gpt-6.1-sol")), "normal"
    )

    _assert_clean_turn(result, events, tools, "toolu_dead")
    # The fallback answered the very request Claude got, with untouched history.
    assert [type(message) for message in codex.requests[0]] == [UserMessage]
    assert result.history[1].metadata["provider"] == "openai-codex"
    # Its next request replays only its own call, with that call's result: no orphan.
    _, items = CodexProvider("token")._to_responses_input(codex.requests[1])
    calls = {item["call_id"] for item in items if item.get("type") == "function_call"}
    outputs = {item["call_id"] for item in items if item.get("type") == "function_call_output"}
    assert calls == outputs == {"call_4204"}
    assert not any(e.type == "error" for e in events)  # the run never looked failed


@pytest.mark.asyncio
async def test_retry_within_a_provider_discards_the_failed_attempt():
    provider = _ScriptedProvider("openai-codex", ["fail-mid-call", "call", "Done."])

    result, events, tools = await _run_engine(_tier((provider, "gpt-6.1-sol"), retries=2), "normal")

    _assert_clean_turn(result, events, tools, "call_dead")
    assert provider.requests[0] == provider.requests[1]  # retried with the identical history
    texts = [
        block.text
        for item in result.history
        for block in item.content
        if isinstance(block, TextBlock)
    ]
    assert "Partial words" not in texts


@pytest.mark.asyncio
async def test_failure_on_every_provider_leaves_no_partial_turn():
    provider = _ScriptedProvider("openai-codex", ["fail-mid-call"])

    with pytest.raises(RuntimeError, match="All providers failed"):
        await _run_engine(_tier((provider, "gpt-6.1-sol")), "normal")


@pytest.mark.asyncio
async def test_claude_cut_off_is_not_a_provider_failure():
    client = _FakeAsyncClient(
        stream_responses=_claude(_claude_cut_off_in_call(), _claude_text("Done."))
    )
    claude = AnthropicProvider("k", client_factory=lambda: client)
    codex = _ScriptedProvider("openai-codex", [])

    result, _events, tools = await _run_engine(
        _tier((claude, "claude-opus-5-5"), (codex, "gpt-6.1-sol")), "normal"
    )

    assert codex.requests == []  # no fallback
    assert tools.calls == [("runtime", {"action": "status"})]
    assert len(_notes(result)) == 1


@pytest.mark.asyncio
async def test_fallback_persists_nothing_of_the_failed_attempt():
    """Through Sentinel's runtime adapter: only the fallback's call and result reach the DB."""
    from app.models import Message
    from app.services.agent.runtime_support import SentinelRuntimeSupport
    from app.services.agent_runtime_adapters.runtime import SentinelLoopRuntimeAdapter
    from app.services.tools import ToolExecutor, ToolRegistry
    from app.services.tools.registry import ToolDefinition as AppToolDefinition
    from tests.fake_db import FakeDB
    from tests.test_runtime_support import _new_session

    db = FakeDB()
    session = _new_session(db)
    await db.commit()
    ran = []

    async def workspaces(payload, runtime):
        ran.append(payload)
        return {"ok": True}

    registry = ToolRegistry()
    registry.register(
        AppToolDefinition(
            name="workspaces",
            description="Workspaces",
            parameters_schema={"type": "object", "additionalProperties": True},
            execute=workspaces,
        )
    )

    class Context:
        async def build(self, *args, **kwargs):
            return [UserMessage(content="create an omarchy distro")]

    client = _FakeAsyncClient(stream_responses=_claude(_claude_dies_in_call()))
    claude = AnthropicProvider("k", client_factory=lambda: client)
    codex = _ScriptedProvider("openai-codex", ["call", "Done."])
    support = SentinelRuntimeSupport(
        provider=_tier((claude, "claude-opus-5-5"), (codex, "gpt-6.1-sol")),
        context_builder=Context(),
        tool_registry=registry,
        tool_executor=ToolExecutor(registry),
    )
    events = []

    async def sink(event):
        events.append(event)

    result = await SentinelLoopRuntimeAdapter(
        loop=support, db=db, session_id=session.id, persist_incremental=True
    ).run_turn(
        RunTurnRequest(
            conversation_id=str(session.id),
            new_items=[ConversationItem(id="u", role="user", content=[TextBlock(text="go")])],
            config=GenerationConfig(model="normal", stream=True),
        ),
        sink=sink,
    )

    assert result.status == "completed"
    assert ran == [{"action": "source"}]
    rows = db.storage[Message]
    assert [row.tool_call_id for row in rows if row.role == "tool_result"] == ["call_4204"]
    calls = [
        call["id"]
        for row in rows
        if row.role == "assistant"
        for call in (row.metadata_json or {}).get("tool_calls") or []
    ]
    assert calls == ["call_4204"]
    assert "toolu_dead" not in json.dumps(
        [(row.content, row.metadata_json) for row in rows], default=str
    )
    assert [e.type for e in events if e.type in {"toolcall_discard", "stream_reset"}] == [
        "toolcall_discard",
        "stream_reset",
    ]


def test_discard_and_reset_reach_the_websocket():
    from app.services.ws.ws_manager import ConnectionManager
    from app.services.ws.ws_stream_service import _phase_from_sentinel_event
    from sentral import AgentEvent as RuntimeEvent
    from sentral.llm.runtime_conversions import runtime_event_to_sentinel_event

    discard = runtime_event_to_sentinel_event(
        RuntimeEvent(
            type="toolcall_discard", tool_call=ToolCallBlock(id="toolu_cut", name="workspaces")
        )
    )
    reset = runtime_event_to_sentinel_event(RuntimeEvent(type="stream_reset"))

    assert ConnectionManager()._event_payload(discard) == {
        "type": "toolcall_discard",
        "tool_call": {"id": "toolu_cut", "name": "workspaces", "arguments": {}},
    }
    assert ConnectionManager()._event_payload(reset) == {"type": "stream_reset"}
    assert _phase_from_sentinel_event(reset) == "thinking"


# --- Output cap: the model's, not the tier's ----------------------------------------------


def test_output_cap_is_the_models():
    from sentral.llm.model_limits import DEFAULT_MAX_OUTPUT_TOKENS, model_context, output_cap

    assert output_cap("claude-opus-5-5") == 128_000
    assert output_cap("claude-fable-5-1") == 128_000
    assert output_cap("claude-opus-5-5", 4096) == 4096  # an explicit smaller cap stands
    assert output_cap("claude-opus-5-5", 10_000_000) == 128_000  # never past the model's
    assert output_cap("gpt-6.1-sol") == DEFAULT_MAX_OUTPUT_TOKENS
    assert output_cap("unknown-model") == DEFAULT_MAX_OUTPUT_TOKENS
    assert model_context("claude-opus-5-5")["context_token_budget"] == 1_000_000 - 128_000


@pytest.mark.parametrize("tier", ["fast", "normal", "hard"])
def test_every_tier_sends_the_models_cap(tier):
    from app.config import Settings
    from app.services.llm.factory import build_tier_provider_from_settings

    settings = Settings().model_copy(
        update={
            "anthropic_api_key": "test-key",
            "openai_api_key": "test-key",
            "gemini_api_key": "test-key",
            "ollama_base_url": "https://models.example",
            "ollama_model": "custom:4b",
            "provider_order": ["anthropic", "openai", "gemini", "ollama"],
            "automatic_providers": ["anthropic", "openai", "gemini", "ollama"],
        }
    )
    router = build_tier_provider_from_settings(settings)
    config = router._tiers[TierName(tier)]
    for candidate in [config.primary, *config.fallbacks, *config.manual_options]:
        assert candidate.reasoning_config.max_tokens is None
    claude = next(c for c in config.manual_options if c.provider.name == "anthropic")
    options = AnthropicProvider._generation_options(claude.reasoning_config, claude.model)
    assert options["max_tokens"] == 128_000


def test_ollama_cap_stays_within_its_context():
    provider = OllamaProvider("http://localhost:11434")
    payload = provider._payload(
        [UserMessage(content="go")], "qwen3", None, 0.7, ReasoningConfig(), None, True
    )
    assert payload["options"]["num_predict"] == 8192
    assert payload["options"]["num_predict"] <= payload["options"]["num_ctx"] // 4
