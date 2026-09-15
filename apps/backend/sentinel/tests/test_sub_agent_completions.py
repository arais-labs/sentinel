import asyncio
import pytest
from uuid import uuid4

from app.models import Message, Session
from app.services.sub_agents.accounting import wakeup_model
from app.services.sub_agents.completions import deliver_completion
from app.services.sessions.agent_run_registry import AgentRunRegistry
from tests.fake_db import FakeDB
from tests.test_sub_agent_orchestrator import _add_task


def test_active_parent_receives_completion_once_without_late_wakeup():
    async def check():
        db = FakeDB()
        parent = Session(user_id="local", title="parent", status="active")
        db.add(parent)
        child = _add_task(db, parent)
        child.status = "completed"
        child.result = {"turn_id": str(uuid4()), "final_text": "Actual result"}
        registry = AgentRunRegistry()
        wakeups = []

        async def wakeup(sid):
            wakeups.append(sid)

        registry.configure_idle_interjections_callback(wakeup)
        blocker = asyncio.Event()
        running = await registry.start(str(parent.id), blocker.wait())
        assert await deliver_completion(db, child, registry)
        assert not await deliver_completion(db, child, registry)
        assert not wakeups
        delivered = registry.drain_interjections(str(parent.id))
        assert len(delivered) == 1
        assert "Actual result" in delivered[0].content[0].text
        blocker.set()
        await running
        await registry.clear(str(parent.id), running)
        assert not wakeups
        child.result = {"turn_id": str(uuid4()), "final_text": "Follow-up result"}
        assert await deliver_completion(db, child, registry)
        assert wakeups == [str(parent.id)]
        followup = registry.drain_interjections(str(parent.id))[0]
        assert followup.id != delivered[0].id
        assert followup.metadata["turn_id"] != delivered[0].metadata["turn_id"]

    asyncio.run(check())


def test_cancelled_report_never_relabels_opening_commentary_as_result():
    async def check():
        db = FakeDB()
        parent = Session(user_id="local", title="parent", status="active")
        db.add(parent)
        task = _add_task(db, parent)
        task.status = "cancelled"
        task.result = {
            "turn_id": str(uuid4()),
            "final_text": "I will inspect the modules",
            "error": "Generation stopped by user",
        }
        registry = AgentRunRegistry()
        await deliver_completion(db, task, registry)
        report = db.storage[Message][0]
        assert "I will inspect" not in report.content
        assert "no final answer was produced" in report.content
        assert not registry.has_interjections(str(parent.id))

    asyncio.run(check())


def test_wakeups_inherit_model_and_honor_explicit_user_selection():
    async def check():
        db = FakeDB()
        parent = Session(user_id="local", title="parent", status="active")
        db.add(parent)
        db.add(
            Message(
                session_id=parent.id,
                role="user",
                content="test",
                metadata_json={
                    "generation": {
                        "requested_tier": "hard",
                        "model_selection": {
                            "reasoning_level": "high",
                            "fast_mode": True,
                        },
                    }
                },
            )
        )
        assert await wakeup_model(db, parent.id, {}) == "sentinel:hard:auto:high:fast"
        assert (
            await wakeup_model(db, parent.id, {"source": "sub_agent", "steering": "pending"})
            == "sentinel:hard:auto:high:fast"
        )
        assert (
            await wakeup_model(
                db,
                parent.id,
                {
                    "generation": {
                        "requested_tier": "fast",
                        "model_selection": {"reasoning_level": "low"},
                    }
                },
            )
            == "sentinel:fast:auto:low"
        )

    asyncio.run(check())


@pytest.mark.parametrize("incremental", [False, True])
def test_completion_enters_active_runtime_context_before_parent_finishes(incremental):
    from sentral import (
        GenerationConfig,
        RunTurnRequest,
        ConversationItem,
        TextBlock,
    )
    from app.services.agent_runtime_adapters.runtime import SentinelLoopRuntimeAdapter
    from sentral.llm.generic.types import (
        AssistantMessage,
        TextContent,
        ToolCallContent,
    )
    from tests.test_sub_agent_orchestrator import (
        _SequenceProvider,
        _build_base_runtime_support,
    )

    async def check():
        db = FakeDB()
        parent = Session(user_id="local", title="parent", status="active")
        db.add(parent)
        child = _add_task(db, parent)
        child.status = "completed"
        child.result = {"turn_id": str(uuid4()), "final_text": "Child found the answer"}
        registry = AgentRunRegistry()
        seen = []
        events = []

        async def sink(event):
            events.append(event)

        class Provider(_SequenceProvider):
            async def chat(self, messages, *args, **kwargs):
                seen.append(messages)
                if self.calls == 0:
                    await deliver_completion(db, child, registry)
                else:
                    notices = [e for e in events if e.type == "notice_message"]
                    assert len(notices) == 1
                    from sentral.llm.runtime_conversions import (
                        runtime_event_to_sentinel_event,
                    )
                    from app.services.ws.ws_manager import ConnectionManager

                    payload = ConnectionManager()._event_payload(
                        runtime_event_to_sentinel_event(notices[0])
                    )
                    assert payload["message"]["metadata"]["steering"] == "delivered"
                    assert (
                        "Child found the answer" in payload["message"]["metadata"]["notice"]["body"]
                    )
                return await super().chat(messages, *args, **kwargs)

        provider = Provider(
            [
                AssistantMessage(
                    content=[ToolCallContent(id="tool", name="missing", arguments={})],
                    stop_reason="tool_use",
                ),
                AssistantMessage(content=[TextContent(text="Integrated child answer")]),
            ]
        )
        adapter = SentinelLoopRuntimeAdapter(
            loop=_build_base_runtime_support(provider),
            db=db,
            session_id=parent.id,
            persist_incremental=incremental,
        )
        running = await registry.start(
            str(parent.id),
            adapter.run_turn(
                RunTurnRequest(
                    conversation_id=str(parent.id),
                    new_items=[
                        ConversationItem(id="user", role="user", content=[TextBlock(text="test")])
                    ],
                    config=GenerationConfig(model="hard", stream=True, max_iterations=0),
                    interjection_source=lambda: registry.drain_interjections(str(parent.id)),
                ),
                sink=sink,
            ),
        )
        await running
        await registry.clear(str(parent.id), running)
        assert len(seen) == 2
        assert any("Child found the answer" in str(message.content) for message in seen[1])
        reports = [
            m for m in db.storage[Message] if (m.metadata_json or {}).get("source") == "sub_agent"
        ]
        assert len(reports) == 1
        assert reports[0].metadata_json["steering"] == "delivered"
        assert reports[0].metadata_json["notice"]["title"] == "Sub-agent report"
        assert "Child found the answer" in reports[0].metadata_json["notice"]["body"]
        assert not registry.has_interjections(str(parent.id))

    asyncio.run(check())


@pytest.mark.parametrize("incremental", [False, True])
def test_runtime_notice_streams_then_persists_with_same_display_identity(incremental):
    from sentral import (
        ConversationItem,
        GenerationConfig,
        RunTurnRequest,
        TextBlock,
    )
    from app.services.agent_runtime_adapters.runtime import SentinelLoopRuntimeAdapter
    from sentral.llm.generic.types import AssistantMessage, TextContent
    from tests.test_sub_agent_orchestrator import (
        _SequenceProvider,
        _build_base_runtime_support,
    )

    async def check():
        db = FakeDB()
        session = Session(user_id="local", status="active")
        db.add(session)
        queued = [
            ConversationItem(
                id="job-report",
                role="system",
                content=[TextBlock(text="**Done**")],
                metadata={"notice": {"title": "Background job report"}},
            )
        ]
        events = []

        def drain():
            items = queued[:]
            queued.clear()
            return items

        async def sink(event):
            events.append(event)

        class Provider(_SequenceProvider):
            async def chat(self, *args, **kwargs):
                assert any(event.type == "notice_message" for event in events)
                return await super().chat(*args, **kwargs)

        adapter = SentinelLoopRuntimeAdapter(
            loop=_build_base_runtime_support(
                Provider([AssistantMessage(content=[TextContent(text="Done")])])
            ),
            db=db,
            session_id=session.id,
            persist_incremental=incremental,
        )
        await adapter.run_turn(
            RunTurnRequest(
                conversation_id=str(session.id),
                new_items=[],
                config=GenerationConfig(model="hard", stream=True),
                interjection_source=drain,
            ),
            sink=sink,
        )
        notices = [e.metadata["conversation_message"] for e in events if e.type == "notice_message"]
        saved = [m for m in db.storage[Message] if (m.metadata_json or {}).get("notice")]
        assert len(notices) == len(saved) == 1
        assert saved[0].role == "system"
        assert saved[0].metadata_json["presentation"] == notices[0]["metadata"]["presentation"]

    asyncio.run(check())
