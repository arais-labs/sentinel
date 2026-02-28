from __future__ import annotations

import asyncio

from app.models import Message, Session, SessionSummary
from app.services.agent.context_builder import ContextBuilder
from app.services.compaction import CompactionService
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent, TokenUsage
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
    assert provider.calls[0]["model"] == "fast"
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
    system_text = "\n".join(
        item.content for item in context if getattr(item, "role", "") == "system"
    )
    assert "Session summary" in system_text
    assert "Decision log" in system_text


def test_context_builder_limits_history_by_token_budget():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="ctx-budget")
    db.add(session)

    probe_builder = ContextBuilder(default_system_prompt="Base", token_budget=1_000_000)
    fixed_context = _run(probe_builder.build(db, session.id))
    fixed_tokens = probe_builder._estimate_context_tokens(fixed_context)

    db.add(
        Message(
            session_id=session.id,
            role="user",
            content="history message one",
            token_count=30,
            metadata_json={},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="user",
            content="history message two",
            token_count=30,
            metadata_json={},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="user",
            content="history message three",
            token_count=30,
            metadata_json={},
        )
    )

    builder = ContextBuilder(default_system_prompt="Base", token_budget=fixed_tokens + 50)
    context = _run(builder.build(db, session.id))
    user_messages = [m for m in context if getattr(m, "role", "") == "user"]
    assert len(user_messages) == 1
    assert isinstance(user_messages[0].content, list)
    text_blocks = [b.text for b in user_messages[0].content if isinstance(b, TextContent)]
    assert text_blocks and text_blocks[-1] == "history message three"


def test_should_auto_compact_is_token_based_not_message_count():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="token-threshold")
    db.add(session)
    for _ in range(31):
        db.add(
            Message(
                session_id=session.id,
                role="user",
                content="ok",
                metadata_json={},
            )
        )

    service = CompactionService(provider=None)
    assert _run(service.should_auto_compact(db, session_id=session.id)) is False
    assert _run(service.should_auto_compact(db, session_id=session.id, threshold_tokens=20)) is True


def test_should_auto_compact_prefers_message_token_count_when_present():
    db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="token-count-field")
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="short",
            token_count=1200,
            metadata_json={},
        )
    )
    db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="tiny",
            token_count=800,
            metadata_json={},
        )
    )

    service = CompactionService(provider=None)
    assert (
        _run(service.should_auto_compact(db, session_id=session.id, threshold_tokens=1500)) is True
    )


def test_context_builder_formats_group_telegram_messages_for_model_only():
    db = FakeDB()
    session = Session(user_id="admin", status="active", title="telegram-group")
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role="user",
            content="Deploy status?",
            metadata_json={
                "source": "telegram",
                "telegram_chat_type": "group",
                "telegram_chat_title": "Ops",
                "telegram_chat_id": -100123,
                "telegram_user_name": "John Smith",
            },
        )
    )

    builder = ContextBuilder(default_system_prompt="Base")
    context = _run(builder.build(db, session.id))
    user_messages = [m for m in context if getattr(m, "role", "") == "user"]
    assert user_messages
    user_content = user_messages[-1].content
    assert isinstance(user_content, list)
    text_blocks = [b.text for b in user_content if isinstance(b, TextContent)]
    assert text_blocks
    assert text_blocks[-1].startswith(
        "[Telegram group 'Ops' chat_id=-100123 from John Smith direct_reply_required ui_audit_only untrusted_group] "
    )
    assert text_blocks[-1].endswith("Deploy status?")


def test_context_builder_keeps_owner_dm_telegram_messages_clean():
    db = FakeDB()
    session = Session(user_id="admin", status="active", title="telegram-owner")
    db.add(session)
    db.add(
        Message(
            session_id=session.id,
            role="user",
            content="How does telegram work here",
            metadata_json={
                "source": "telegram",
                "telegram_chat_type": "private",
                "telegram_is_owner": True,
                "telegram_user_name": "John Smith",
            },
        )
    )

    builder = ContextBuilder(default_system_prompt="Base")
    context = _run(builder.build(db, session.id))
    user_messages = [m for m in context if getattr(m, "role", "") == "user"]
    assert user_messages
    user_content = user_messages[-1].content
    assert isinstance(user_content, list)
    text_blocks = [b.text for b in user_content if isinstance(b, TextContent)]
    assert text_blocks
    assert text_blocks[-1] == "How does telegram work here"
