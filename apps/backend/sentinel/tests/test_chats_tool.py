"""The shared chats tool: context across chats and direct agent-to-agent messages."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Message, Session, SessionKind, SubAgentTask, Workspace
from app.services.modules.builtins.chats.module import MODULE
from app.services.modules.tool_adapter import build_module_tools
from app.services.sessions.activity import chat_activity, tool_summary
from app.services.sessions.visibility import conversation_history, search_conversations
from app.services.sub_agents.messaging import send_message
from app.services.tools.registry import ToolRuntimeContext
from app.services.voice.session import voice_session
from sentral.errors import ToolValidationError


@pytest_asyncio.fixture
async def voice_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            db.factory = factory
            yield db
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_work_context_limits_each_chat_and_refreshes_without_waking_agents(voice_db):
    db = voice_db
    first, second, child, other_user = [uuid4() for _ in range(4)]
    now = datetime.now(UTC)
    for chat_id in (first, second, child, other_user):
        db.add(
            Session(
                id=chat_id,
                user_id="other" if chat_id == other_user else "local",
                parent_session_id=first if chat_id == child else None,
                title=str(chat_id),
            )
        )
        for index in range(8):
            db.add(
                Message(
                    session_id=chat_id,
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"Message {index}",
                    created_at=now,
                )
            )
        db.add(Message(session_id=chat_id, role="assistant", content=" ", created_at=now))
        db.add(
            Message(
                session_id=chat_id, role="system", content="private system prompt", created_at=now
            )
        )
        for index in range(5):
            db.add(
                Message(
                    session_id=chat_id,
                    role="tool_result",
                    tool_name="check",
                    content='{"summary":"Check %s","large_payload":"not for voice"}' % index,
                    metadata_json={"is_error": index == 4},
                    created_at=now,
                )
            )
    await db.commit()
    running = AsyncMock(side_effect=lambda id: id == first)
    context = await chat_activity(db, running)
    chats = {row["chat_id"]: row for row in context["chats"]}
    assert set(chats) == {str(first), str(second)}
    for chat in chats.values():
        assert [m["content"] for m in chat["messages"]] == [f"Message {n}" for n in range(3, 8)]
        assert [t["summary"] for t in chat["tools"]] == [f"Check {n}" for n in range(2, 5)]
        assert chat["tools"][-1]["status"] == "failed"
    assert chats[str(first)]["running"] is True
    assert chats[str(second)]["running"] is False
    db.add(
        Message(session_id=first, role="assistant", content="New ongoing update", created_at=now)
    )
    await db.commit()
    refreshed = await chat_activity(db, running, [first])
    assert len(refreshed["chats"]) == 1
    assert refreshed["chats"][0]["messages"][-1]["content"] == "New ongoing update"


@pytest.mark.asyncio
async def test_search_finds_old_work_not_title_guesses_and_scopes_evidence(voice_db):
    db = voice_db
    owner, misleading, foreign, child = [uuid4() for _ in range(4)]
    for chat_id in (owner, misleading, foreign, child):
        db.add(
            Session(
                id=chat_id,
                user_id="other" if chat_id == foreign else "local",
                title="BUG-636 deployment",
                parent_session_id=owner if chat_id == child else None,
            )
        )
    await db.flush()
    original = Message(
        session_id=owner, role="user", content="prefix " * 500 + "Fix BUG-636 deployment ownership"
    )
    db.add(original)
    for index in range(40):
        db.add(Message(session_id=owner, role="assistant", content=f"Later update {index}"))
    for chat_id, role in [
        (misleading, "assistant"),
        (foreign, "user"),
        (child, "user"),
        (owner, "system"),
        (owner, "tool"),
    ]:
        db.add(
            Message(
                session_id=chat_id,
                role=role,
                content="Unrelated chat" if chat_id == misleading else "BUG-636 deployment",
            )
        )
    await db.commit()
    result = await search_conversations(db, user_id="local", query="BUG-636 deployment")
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["chat_id"] == str(owner)
    assert match["message_id"] == str(original.id)
    assert "BUG-636" in match["content"] and match["truncated"]
    assert result["next_offset"] is None
    assert not (await search_conversations(db, user_id="local", query="BUG-636", chat_id=foreign))[
        "matches"
    ]


@pytest.mark.asyncio
async def test_history_paginates_identical_timestamps_without_duplicates(voice_db):
    db = voice_db
    chat_id, other = uuid4(), uuid4()
    db.add_all([Session(id=chat_id, user_id="local"), Session(id=other, user_id="local")])
    now = datetime.now(UTC)
    rows = [
        Message(
            session_id=chat_id,
            role="user" if index == 0 else "assistant",
            content=f"Message {index}",
            created_at=now,
        )
        for index in range(7)
    ]
    foreign_cursor = Message(session_id=other, role="user", content="Other", created_at=now)
    db.add_all([*rows, foreign_cursor])
    await db.commit()
    first = await conversation_history(db, user_id="local", chat_id=chat_id, limit=3)
    assert [row["content"] for row in first["messages"]] == ["Message 4", "Message 5", "Message 6"]
    from uuid import UUID

    second = await conversation_history(
        db,
        user_id="local",
        chat_id=chat_id,
        limit=3,
        before_message_id=UUID(first["next_before_message_id"]),
    )
    assert [row["content"] for row in second["messages"]] == ["Message 1", "Message 2", "Message 3"]
    last = await conversation_history(
        db,
        user_id="local",
        chat_id=chat_id,
        limit=3,
        before_message_id=UUID(second["next_before_message_id"]),
    )
    assert [row["content"] for row in last["messages"]] == ["Message 0"]
    assert last["next_before_message_id"] is None
    with pytest.raises(ValueError, match="not a visible message"):
        await conversation_history(
            db, user_id="local", chat_id=chat_id, before_message_id=foreign_cursor.id
        )


@pytest.mark.asyncio
async def test_activity_uses_message_recency_and_includes_workspace_and_request(voice_db):
    db = voice_db
    now = datetime.now(UTC)
    old = now - timedelta(days=2)
    workspace = Workspace(name="Backend", directory="/work/backend", machine_id=uuid4())
    db.add(workspace)
    await db.flush()
    first, second = [
        Session(user_id="local", title="Same title", updated_at=old, workspace_id=workspace.id)
        for _ in range(2)
    ]
    db.add_all([first, second])
    await db.flush()
    db.add_all(
        [
            Message(session_id=first.id, role="user", content="Own the migrations", created_at=old),
            Message(
                session_id=first.id, role="assistant", content="Migration finished", created_at=now
            ),
            Message(session_id=second.id, role="user", content="Own the UI", created_at=old),
            Message(
                session_id=second.id,
                role="assistant",
                content="UI update",
                created_at=now - timedelta(minutes=1),
            ),
        ]
    )
    await db.commit()
    running = AsyncMock(return_value=False)
    first_page = await chat_activity(db, running, limit=1)
    assert first_page["next_offset"] == 1
    assert first_page["chats"][0]["chat_id"] == str(first.id)
    assert first_page["chats"][0]["original_request"] == "Own the migrations"
    assert first_page["chats"][0]["workspace"]["directory"] == "/work/backend"
    updates = await chat_activity(db, running, since=now - timedelta(seconds=30))
    assert [row["chat_id"] for row in updates["chats"]] == [str(first.id)]


def test_tool_summaries_do_not_dump_structured_results_or_claim_pending_work_finished():
    row = SimpleNamespace(
        metadata_json={"pending": True},
        tool_name="shell",
        content='{"output":"sensitive large result"}',
        created_at=datetime.now(UTC),
    )
    result = tool_summary(row)
    assert result["status"] == "pending"
    assert "sensitive" not in result["summary"]
    row.metadata_json = {"summary": "x" * 1000}
    assert len(tool_summary(row)["summary"]) == 300


@pytest.mark.asyncio
async def test_voice_session_is_hidden_from_activity_and_listing(voice_db):
    db = voice_db
    chat = Session(user_id="local", title="Real chat")
    db.add(chat)
    voice = await voice_session(db)
    assert voice.kind == SessionKind.VOICE and (await voice_session(db)).id == voice.id
    db.add(Message(session_id=voice.id, role="user", content="Spoken request"))
    await db.commit()
    context = await chat_activity(db, AsyncMock(return_value=False))
    assert [row["chat_id"] for row in context["chats"]] == [str(chat.id)]
    tools = build_module_tools(MODULE)
    runtime = ToolRuntimeContext(
        session_id=chat.id, db_session_factory=db.factory, agent_mode="normal"
    )
    listed = await tools[0].execute({"action": "list"}, runtime)
    assert [row["chat_id"] for row in listed["chats"]] == [str(chat.id)]
    assert not (await search_conversations(db, user_id="local", query="Spoken"))["matches"]


@pytest.mark.asyncio
async def test_any_main_agent_can_message_any_main_chat_or_voice(voice_db):
    db = voice_db
    first, second = (
        Session(user_id="local", title="Deploy"),
        Session(user_id="local", title="Review"),
    )
    db.add_all([first, second])
    await db.commit()
    registry = SimpleNamespace(enqueue_interjection=Mock(), notify_idle_interjections=AsyncMock())
    result = await send_message(
        db,
        sender_id=first.id,
        target=str(second.id),
        content="Ship it",
        orchestrator=None,
        run_registry=registry,
    )
    assert result["delivery"] == "queued" and result["target"] == 'chat "Review"'
    registry.notify_idle_interjections.assert_awaited_once_with(str(second.id))
    delivered = registry.enqueue_interjection.call_args.args[1]
    assert delivered.metadata["steering"] == "pending"
    assert delivered.metadata["sender_session_id"] == str(first.id)
    assert 'Message from chat "Deploy"' in delivered.content[0].text
    to_voice = await send_message(
        db,
        sender_id=second.id,
        target="voice",
        content="Done",
        orchestrator=None,
        run_registry=registry,
    )
    voice = await voice_session(db)
    assert to_voice["target_session_id"] == str(voice.id) and to_voice["target"] == "Voice"
    from_voice = await send_message(
        db,
        sender_id=voice.id,
        target=str(first.id),
        content="Status?",
        orchestrator=None,
        run_registry=registry,
    )
    assert "Message from Voice" in registry.enqueue_interjection.call_args.args[1].content[0].text
    assert from_voice["delivery"] == "queued"
    with pytest.raises(ToolValidationError, match="another agent"):
        await send_message(
            db, sender_id=first.id, target=str(first.id), content="x", orchestrator=None
        )
    with pytest.raises(ToolValidationError, match="not found"):
        await send_message(
            db, sender_id=first.id, target=str(uuid4()), content="x", orchestrator=None
        )


@pytest.mark.asyncio
async def test_delegated_agents_stay_inside_their_conversation(voice_db):
    db = voice_db
    root, other = Session(user_id="local", title="Root"), Session(user_id="local", title="Other")
    db.add_all([root, other])
    await db.flush()
    child = Session(user_id="local", title="Child", parent_session_id=root.id)
    db.add(child)
    await db.flush()
    db.add(
        SubAgentTask(
            session_id=root.id, objective="help", result={"child_session_id": str(child.id)}
        )
    )
    await db.commit()
    with pytest.raises(ToolValidationError, match="in this conversation"):
        await send_message(
            db, sender_id=child.id, target=str(other.id), content="x", orchestrator=None
        )
    with pytest.raises(ToolValidationError, match="in this conversation"):
        await send_message(db, sender_id=child.id, target="voice", content="x", orchestrator=None)
    parent = await send_message(
        db, sender_id=child.id, target="parent", content="Report", orchestrator=None
    )
    assert parent["target_session_id"] == str(root.id)
    with pytest.raises(ToolValidationError, match="delegated"):
        await send_message(
            db, sender_id=other.id, target=str(child.id), content="x", orchestrator=None
        )


@pytest.mark.asyncio
async def test_create_and_stop_are_voice_only(voice_db):
    db = voice_db
    chat = Session(user_id="local", title="Chat")
    db.add(chat)
    voice = await voice_session(db)
    await db.commit()
    tool = build_module_tools(MODULE)[0]
    chat_actions = set(tool.parameters_schema["properties"]["action"]["enum"])
    voice_actions = set(tool.voice_parameters_schema["properties"]["action"]["enum"])
    assert {"list", "activity", "search", "history", "send", "inbox"} <= chat_actions
    assert not {"create", "stop"} & chat_actions
    assert {"create", "stop"} <= voice_actions
    assert tool.schema_for("voice") is tool.voice_parameters_schema
    assert tool.schema_for("normal") is tool.parameters_schema
    as_chat = ToolRuntimeContext(
        session_id=chat.id, db_session_factory=db.factory, agent_mode="normal"
    )
    with pytest.raises(ToolValidationError, match="Voice agent only"):
        await tool.execute({"action": "create", "title": "New"}, as_chat)
    as_voice = ToolRuntimeContext(
        session_id=voice.id, db_session_factory=db.factory, agent_mode="voice"
    )
    created = await tool.execute({"action": "create", "title": "New chat"}, as_voice)
    assert created["status"] == "created"
    listed = await tool.execute({"action": "list", "query": "new"}, as_voice)
    assert [row["title"] for row in listed["chats"]] == ["New chat"]
