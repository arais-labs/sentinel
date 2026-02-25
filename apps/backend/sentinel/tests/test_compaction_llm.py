from __future__ import annotations

import asyncio

from app.models import Message, Session, SessionSummary
from app.services.agent.context_builder import ContextBuilder
from app.services.compaction import CompactionService
from app.services.llm.base import LLMProvider
from app.services.llm.types import AgentEvent, AssistantMessage, TextContent, TokenUsage
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


class _MockSummaryProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "mock-summary"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
        return AssistantMessage(
            content=[
                TextContent(
                    text='{"key_decisions":["A"],"tool_results":["B"],"open_tasks":["C"],"context_summary":"Compact summary"}'
                )
            ],
            model="mock",
            provider="mock",
            usage=TokenUsage(input_tokens=10, output_tokens=20),
            stop_reason="stop",
        )

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None):
        if False:
            yield AgentEvent(type="done", stop_reason="stop")
        return


def _seed_session_with_messages(db: FakeDB, *, count: int = 12) -> Session:
    session = Session(user_id="dev-admin", status="active", title="compact")
    db.add(session)
    for i in range(count):
        db.add(
            Message(
                session_id=session.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"message {i} with enough words to compact",
                metadata_json={},
            )
        )
    return session


def test_compaction_uses_llm_structured_summary_when_provider_available():
    db = FakeDB()
    session = _seed_session_with_messages(db, count=14)
    provider = _MockSummaryProvider()

    service = CompactionService(provider=provider)
    result = _run(service.compact_session(db, session_id=session.id, user_id="dev-admin"))

    assert result.raw_token_count > result.compressed_token_count
    assert provider.calls
    assert provider.calls[0]["model"] == "hint:fast"
    assert provider.calls[0]["temperature"] == 0.3

    summary = db.storage[SessionSummary][0]
    assert summary.summary["key_decisions"] == ["A"]
    assert summary.summary["tool_results"] == ["B"]
    assert summary.summary["open_tasks"] == ["C"]
    assert summary.summary["context_summary"] == "Compact summary"


def test_compaction_falls_back_without_provider():
    db = FakeDB()
    session = _seed_session_with_messages(db, count=14)
    service = CompactionService(provider=None)

    result = _run(service.compact_session(db, session_id=session.id, user_id="dev-admin"))
    assert result.raw_token_count > 0
    assert result.compressed_token_count > 0

    summary = db.storage[SessionSummary][0]
    assert "summary_text" in summary.summary
    assert "message" in summary.summary["summary_text"]


def test_context_builder_includes_session_summary_when_available():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="ctx")
    db.add(session)
    db.add(
        SessionSummary(
            session_id=session.id,
            summary={"summary_text": "Decision log"},
            raw_token_count=100,
            compressed_token_count=20,
        )
    )

    builder = ContextBuilder(default_system_prompt="Base")
    context = _run(builder.build(db, session.id))
    system_text = "\n".join(item.content for item in context if getattr(item, "role", "") == "system")
    assert "Session summary" in system_text
    assert "Decision log" in system_text
