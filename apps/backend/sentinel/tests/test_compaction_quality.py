import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Message, Session, SessionSummary
from app.services.agent.context_builder import ContextBuilder
from app.services.sessions.compaction import CompactionService
from app.services.sessions.compaction_generation import batches, current_selection, generate_handoff
from app.services.sessions.handoff import Handoff, remap_summary_sources, render_summary
from app.services.sessions.history import context_history
from app.services.sessions.history_retrieval import read_history, search_history
from sentral.llm.generic.types import AssistantMessage, TextContent
from tests.compaction_fixtures import HandoffProvider, handoff_response
from tests.fake_db import FakeDB


def seed(db, count=24):
    session = Session(user_id="local", title="Quality", status="active")
    db.add(session)
    for index in range(count):
        db.add(
            Message(
                session_id=session.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"Do not lose correction {index}",
                metadata_json={},
            )
        )
    return session


class Recording(HandoffProvider):
    def __init__(
        self,
        *,
        bad_audit=False,
        broken_strong=False,
        foreign_source=False,
        truncated=False,
        on_call=None,
    ):
        self.calls = []
        self.bad_audit, self.broken_strong = bad_audit, broken_strong
        self.foreign_source, self.truncated, self.on_call = foreign_source, truncated, on_call

    async def chat(self, messages, model, **kwargs):
        self.calls.append((model, messages))
        assert kwargs["tools"] == [] and kwargs["tool_choice"] == "none"
        if self.on_call:
            self.on_call()
            self.on_call = None
        if self.broken_strong and model == "hard":
            raise RuntimeError("Strong model unavailable")
        raw = handoff_response(messages)
        data = json.loads(raw)
        if "approved" in data and self.bad_audit:
            data = {"approved": False, "issues": ["Missing workspace restriction"]}
        elif self.foreign_source and "constraints" in data:
            data["constraints"][0]["sources"] = [str(uuid4())]
        return AssistantMessage(
            content=[TextContent(text=json.dumps(data))],
            model=model,
            provider=self.name,
            stop_reason="length" if self.truncated else "stop",
        )


@pytest.mark.parametrize("failure", ["bad_audit", "foreign_source", "truncated"])
def test_quality_failures_preserve_history_and_boundary(failure):
    async def run():
        db = FakeDB()
        session = seed(db)
        before = list(db.storage[Message])
        with pytest.raises(ValueError, match="Compaction failed"):
            await CompactionService(Recording(**{failure: True})).compact_session(
                db, session_id=session.id, user_id="local"
            )
        assert db.storage[Message] == before
        assert not db.storage[SessionSummary]

    asyncio.run(run())


def test_strong_failure_falls_back_to_actual_session_selection():
    async def run():
        db = FakeDB()
        session = seed(db)
        db.storage[Message][-1].metadata_json = {"generation": {"resolved_model": "current-model"}}
        provider = Recording(broken_strong=True)
        await CompactionService(provider).compact_session(
            db, session_id=session.id, user_id="local"
        )
        assert [call[0] for call in provider.calls] == ["hard", "current-model", "current-model"]
        assert db.storage[SessionSummary][0].summary["compaction_selection"]["fallbacks"]

    asyncio.run(run())


def test_legacy_migration_reads_original_history_and_renders_all_legacy_fields():
    async def run():
        db = FakeDB()
        session = seed(db, 30)
        legacy = {
            "summary_text": "Short",
            "key_decisions": ["Decision"],
            "tool_results": ["Result"],
            "open_tasks": ["Unfinished"],
            "through_message_id": str(db.storage[Message][9].id),
        }
        assert all(word in render_summary(legacy) for word in ("Decision", "Result", "Unfinished"))
        db.add(SessionSummary(session_id=session.id, summary=legacy))
        provider = Recording()
        await CompactionService(provider).compact_session(
            db, session_id=session.id, user_id="local"
        )
        first = json.loads(provider.calls[0][1][-1]["content"])
        assert first["source_messages"][0]["content"] == "Do not lose correction 0"
        archived = db.storage[Message][-1].metadata_json["previous_summary"]
        assert archived == legacy

    asyncio.run(run())


def test_new_messages_during_generation_remain_active():
    async def run():
        db = FakeDB()
        session = seed(db)

        def append():
            db.add(
                Message(
                    session_id=session.id, role="user", content="New instruction", metadata_json={}
                )
            )

        await CompactionService(Recording(on_call=append)).compact_session(
            db, session_id=session.id, user_id="local"
        )
        _, active = await context_history(db, session.id)
        assert active[-1].content == "New instruction"

    asyncio.run(run())


def test_concurrent_boundary_change_is_not_overwritten():
    async def run():
        db = FakeDB()
        session = seed(db)
        other = {
            "summary_text": "Other checkpoint",
            "through_message_id": str(db.storage[Message][1].id),
        }

        def change():
            db.add(SessionSummary(session_id=session.id, summary=other))

        with pytest.raises(ValueError, match="Another compaction"):
            await CompactionService(Recording(on_call=change)).compact_session(
                db, session_id=session.id, user_id="local"
            )
        assert db.storage[SessionSummary][0].summary == other

    asyncio.run(run())


def test_full_handoff_is_injected_and_fork_references_are_remapped():
    async def run():
        db = FakeDB()
        session = seed(db)
        await CompactionService(Recording()).compact_session(
            db, session_id=session.id, user_id="local"
        )
        payload = db.storage[SessionSummary][0].summary
        context = await ContextBuilder(default_system_prompt="Base").build(db, session.id)
        rendered = "\n".join(
            item.content for item in context if getattr(item, "role", None) == "system"
        )
        for item in Handoff.model_validate(payload["handoff"]).evidence():
            assert item.text in rendered
            assert all(str(source) in rendered for source in item.sources)
        fork = copy.deepcopy(payload)
        original = next(iter(Handoff.model_validate(fork["handoff"]).evidence())).sources[0]
        replacement = uuid4()
        remap_summary_sources(fork, {str(original): replacement})
        assert str(original) not in fork["summary_text"]
        assert str(replacement) in fork["summary_text"]
        assert str(original) in payload["summary_text"]

    asyncio.run(run())


def test_batches_never_truncate_unicode_or_large_messages():
    records = [{"content": "🙂" * 10, "id": i} for i in range(5)]
    chunks = list(batches(records, 110))
    assert [row for chunk in chunks for row in chunk] == records
    with pytest.raises(ValueError, match="One history message"):
        list(batches(records, 10))


def test_session_selection_preserves_provider_reasoning_and_fast_mode():
    message = Message(
        role="assistant",
        content="done",
        metadata_json={
            "generation": {
                "requested_tier": "normal",
                "provider": "openai-codex",
                "model_selection": {"reasoning_level": "high", "fast_mode": True},
            }
        },
    )
    assert current_selection([message]) == (
        "sentinel:normal:openai-codex:high:fast",
        "openai-codex",
    )


def test_real_user_selection_takes_precedence():
    message = Message(
        role="user",
        content="Go",
        metadata_json={
            "generation": {"requested_tier": "normal", "provider": "anthropic"},
            "model_selection": {
                "provider_id": "openai-codex",
                "reasoning_level": "high",
                "fast_mode": True,
            },
        },
    )
    assert current_selection([message]) == (
        "sentinel:normal:openai-codex:high:fast",
        "openai-codex",
    )


def test_changed_source_content_prevents_publication():
    async def run():
        db = FakeDB()
        session = seed(db)

        def mutate():
            db.storage[Message][0].content = "Final tool result replaced an in-progress snapshot"

        with pytest.raises(ValueError, match="content changed"):
            await CompactionService(Recording(on_call=mutate)).compact_session(
                db, session_id=session.id, user_id="local"
            )
        assert not db.storage[SessionSummary]

    asyncio.run(run())


@pytest.mark.parametrize("rejected", [False, True])
def test_all_returned_compaction_calls_are_accounted_even_on_failure(rejected):
    from app.services.sessions.usage import conversation_usage

    class Paid(Recording):
        async def chat(self, *args, **kwargs):
            result = await super().chat(*args, **kwargs)
            result.provider_usage = {
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "price_kind": "metered",
                "price": None,
            }
            return result

    async def run():
        db = FakeDB()
        session = seed(db)
        provider = Paid(bad_audit=rejected)
        try:
            await CompactionService(provider).compact_session(
                db, session_id=session.id, user_id="local"
            )
        except ValueError:
            assert rejected
        snapshots = [
            m for m in db.storage[Message] if (m.metadata_json or {}).get("source") == "usage"
        ]
        assert len(snapshots) == len(provider.calls)
        usage = await conversation_usage(db, session.id)
        assert usage["input_tokens"] == 10 * len(provider.calls)
        assert bool(db.storage[SessionSummary]) is not rejected

    asyncio.run(run())


def test_multiple_batches_and_repeated_handoff_preserve_all_sections():
    class Carry(Recording):
        last = None
        drafts = 0

        def model_context(self, model):
            return {"context_window_tokens": 80000}

        async def chat(self, messages, model, **kwargs):
            response = await super().chat(messages, model, **kwargs)
            data = json.loads(response.content[0].text)
            if "approved" in data:
                return response
            request = json.loads(messages[-1]["content"])
            if self.last:
                for fact in Handoff.model_validate(self.last).evidence():
                    assert fact.text in request["previous_handoff"]
                data = copy.deepcopy(self.last)
            else:
                for key in (
                    "constraints",
                    "decisions",
                    "artifacts",
                    "results",
                    "next_steps",
                    "pending_obligations",
                    "superseded",
                ):
                    data[key] = [
                        {
                            "text": f"Distinct original {key}",
                            "sources": [request["source_messages"][0]["message_id"]],
                        }
                    ]
            self.drafts += 1
            data["results"].append(
                {
                    "text": f"New observation {self.drafts}",
                    "sources": [request["source_messages"][0]["message_id"]],
                }
            )
            self.last = data
            response.content = [TextContent(text=json.dumps(data))]
            return response

    async def run():
        provider = Carry()
        messages = [
            Message(
                id=uuid4(),
                role="user",
                content="source " * 600,
                created_at=datetime.now(UTC),
                metadata_json={},
            )
            for _ in range(20)
        ]
        payload = await generate_handoff(provider, messages, None, ("normal", None))
        count = provider.drafts
        assert count > 1
        payload = await generate_handoff(provider, messages[:2], payload, ("normal", None))
        assert provider.drafts > count
        rendered = render_summary(payload)
        assert "Distinct original pending_obligations" in rendered
        assert "New observation 1" in rendered
        assert f"New observation {provider.drafts}" in rendered

    asyncio.run(run())


def test_history_grouped_tool_registration():
    from app.services.modules.builtins.conversation_history.module import MODULE
    from app.services.modules.tool_adapter import build_module_tools

    tools = build_module_tools(MODULE)
    assert len(tools) == 1 and tools[0].name == "conversation_history"
    schema = tools[0].parameters_schema
    assert set(schema["properties"]["action"]["enum"]) == {"read", "search"}
    assert "session_id" not in schema["properties"]
    assert {"message_id", "page_size", "offset", "field"} <= schema["properties"].keys()


@pytest.mark.asyncio
async def test_real_sqlite_compaction_and_scoped_paginated_retrieval(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Session.metadata.create_all(
                conn, tables=[Session.__table__, Message.__table__, SessionSummary.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid, other = uuid4(), uuid4()
    origin = datetime.now(UTC)
    identifiers = []
    async with factory() as db:
        db.add_all(
            [
                Session(id=sid, user_id="local", title="One"),
                Session(id=other, user_id="local", title="Other"),
            ]
        )
        for index in range(24):
            mid = uuid4()
            identifiers.append(mid)
            db.add(
                Message(
                    id=mid,
                    session_id=sid,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"needle {index} " + "x" * 2000,
                    metadata_json={"source": "web", "tool_calls": [{"name": "test"}]},
                    created_at=origin + timedelta(seconds=index),
                )
            )
        foreign = uuid4()
        db.add(
            Message(
                id=foreign, session_id=other, role="user", content="secret needle", metadata_json={}
            )
        )
        await db.commit()
        await CompactionService(Recording()).compact_session(db, session_id=sid, user_id="local")
        summary, active = await context_history(db, sid)
        assert len(active) == 10
        old = await read_history(
            db, session_id=sid, message_id=identifiers[0], limit=20, before=0, after=1
        )
        assert old["message"]["content"].startswith("needle 0")
        assert old["message"]["next_offset"] == 20
        continued = await read_history(
            db, session_id=sid, message_id=identifiers[0], offset=20, limit=20
        )
        assert continued["message"]["offset"] == 20
        page = await search_history(db, session_id=sid, query="needle", limit=3)
        assert len(page["matches"]) == 3
        page2 = await search_history(
            db,
            session_id=sid,
            query="needle",
            limit=3,
            before_message_id=UUID(page["next_before_message_id"]),
        )
        assert not (
            {m["message_id"] for m in page["matches"]} & {m["message_id"] for m in page2["matches"]}
        )
        for operation in (
            read_history(db, session_id=sid, message_id=foreign),
            search_history(db, session_id=sid, query="needle", before_message_id=foreign),
        ):
            with pytest.raises(ValueError, match="not readable"):
                await operation
        calls = await read_history(
            db, session_id=sid, message_id=identifiers[0], field="tool_calls"
        )
        assert '"name":"test"' in calls["message"]["content"]
        tools = await search_history(db, session_id=sid, query="test", limit=2)
        assert len(tools["matches"]) == 2
        assert "test" in tools["matches"][0]["tool_calls_excerpt"]
        assert summary.summary["schema_version"] == 2
    await engine.dispose()
