from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from telegram.constants import ParseMode

from app.config import Settings
from app.models import Session, SessionBinding
from app.services.araios.runtime_services import configure_runtime_services, reset_runtime_services
from app.services.araios.system_modules.telegram.module import MODULE as TELEGRAM_MODULE
from app.services.instance_runtime_context import (
    InstanceRuntimeContext,
    instance_runtime_context_registry,
)
from app.services.sessions import session_bindings
from app.services.sub_agents import SubAgentOrchestrator
from app.services.telegram import TelegramBridge
from app.services.telegram.bridge import _telegram_tool_result_summary
from app.services.tools import ToolExecutor, ToolRegistry
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.triggers.trigger_scheduler import TriggerScheduler
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _make_instance_settings(**overrides) -> Settings:
    """Build a per-instance Settings stub exposing telegram_* + dev_user_id."""
    instance_settings = Settings(_env_file=None)
    instance_settings.dev_user_id = overrides.pop("dev_user_id", "admin")
    instance_settings.telegram_bot_token = overrides.pop("telegram_bot_token", None)
    instance_settings.telegram_owner_user_id = overrides.pop("telegram_owner_user_id", None)
    instance_settings.telegram_owner_chat_id = overrides.pop("telegram_owner_chat_id", None)
    instance_settings.telegram_owner_telegram_user_id = overrides.pop(
        "telegram_owner_telegram_user_id", None
    )
    for key, value in overrides.items():
        setattr(instance_settings, key, value)
    return instance_settings


def _db_factory_for(db: FakeDB):
    """Return an async_sessionmaker-like callable yielding the given FakeDB."""

    class _DBFactory:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return None

    return lambda: _DBFactory()


def _build_bridge(
    *,
    db: FakeDB,
    user_id: str,
    instance_settings: Settings | None = None,
    agent_runtime_support=None,
) -> TelegramBridge:
    return TelegramBridge(
        bot_token="dummy",
        user_id=user_id,
        agent_runtime_support=agent_runtime_support,
        run_registry=object(),
        ws_manager=object(),
        db_factory=_db_factory_for(db),
        instance_settings=instance_settings or _make_instance_settings(),
    )


def _build_context(
    *,
    name: str,
    db: FakeDB,
    instance_settings: Settings,
    telegram_bridge: TelegramBridge | None,
    agent_runtime_support=None,
) -> InstanceRuntimeContext:
    """Build an InstanceRuntimeContext wired to a per-instance FakeDB + bridge."""
    session_factory = _db_factory_for(db)
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutor(tool_registry)
    return InstanceRuntimeContext(
        name=name,
        database_name=f"sentinel_{name}_test",
        instance_settings=instance_settings,
        session_factory=session_factory,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        agent_runtime_support=agent_runtime_support,
        trigger_scheduler=TriggerScheduler(
            agent_runtime_support=agent_runtime_support,
            tool_executor=tool_executor,
            db_factory=None,
        ),
        sub_agent_orchestrator=SubAgentOrchestrator(),
        background_tasks=[],
        telegram_bridge=telegram_bridge,
    )


def _register_context(context: InstanceRuntimeContext) -> None:
    instance_runtime_context_registry._contexts[context.name] = context  # noqa: SLF001


def _clear_registry() -> None:
    instance_runtime_context_registry._contexts.clear()  # noqa: SLF001


def _telegram_tool():
    reset_runtime_services()
    configure_runtime_services(app_state=SimpleNamespace())
    return TELEGRAM_MODULE.to_tool_definitions()[0]


def test_owner_dm_route_requires_explicit_session_when_missing():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    private_chat = SimpleNamespace(id=123, type="private", title=None)
    owner_user = SimpleNamespace(id=123, full_name="Owner", first_name="Owner")

    session_id, scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=private_chat,
            user=owner_user,
            metadata={"telegram_is_owner": True},
        )
    )
    assert scope == "owner_dm_missing"
    assert session_id is None
    assert db.storage[Session] == []
    assert db.storage[SessionBinding] == []


def test_owner_dm_route_uses_owner_active_binding():
    db = FakeDB()
    older = Session(user_id="admin", title="Old")
    newer = Session(user_id="admin", title="New")
    db.add(older)
    db.add(newer)
    _run(session_bindings.set_owner_active_session(db, user_id="admin", session_id=older.id))
    bridge = _build_bridge(db=db, user_id="admin")
    private_chat = SimpleNamespace(id=123, type="private", title=None)
    owner_user = SimpleNamespace(id=123, full_name="Owner", first_name="Owner")

    session_id, scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=private_chat,
            user=owner_user,
            metadata={"telegram_is_owner": True},
        )
    )

    assert scope == "owner_dm"
    assert session_id == older.id


def test_telegram_manage_tool_configure_sets_owner():
    db = FakeDB()
    instance_settings = _make_instance_settings(
        dev_user_id="dev-admin",
        telegram_bot_token=None,
        telegram_owner_user_id=None,
    )
    context = _build_context(
        name="main",
        db=db,
        instance_settings=instance_settings,
        telegram_bridge=None,
    )
    _register_context(context)
    tool = _telegram_tool()

    rebuilt_settings = _make_instance_settings(
        dev_user_id="dev-admin",
        telegram_owner_user_id="admin",
    )
    rebuilt = _build_context(
        name="main",
        db=db,
        instance_settings=rebuilt_settings,
        telegram_bridge=None,
    )

    async def _fake_rebuild(*, app_state, context):  # noqa: ARG001
        _register_context(rebuilt)
        return rebuilt

    try:
        with (
            patch(
                "app.services.telegram.resolve_owner_user_id_from_session",
                new=AsyncMock(return_value="admin"),
            ),
            patch(
                "app.services.araios.system_modules.telegram.handlers._persist_telegram_settings",
                new=AsyncMock(return_value=None),
            ) as persist_mock,
            patch.object(
                instance_runtime_context_registry,
                "rebuild_context",
                new=AsyncMock(side_effect=_fake_rebuild),
            ) as rebuild_mock,
        ):
            result = _run(
                tool.execute(
                    {
                        "command": "configure",
                        "bot_token": "12345:abcde",
                    },
                    ToolRuntimeContext(session_id=uuid4(), instance_name="main"),
                )
            )

        assert result["success"] is True
        assert result.get("owner_user_id") == "admin"
        assert "main_session_id" not in result
        persist_mock.assert_awaited_once()
        rebuild_mock.assert_awaited_once()
    finally:
        _clear_registry()


def test_telegram_manage_tool_requires_session_for_mutations():
    db = FakeDB()
    context = _build_context(
        name="main",
        db=db,
        instance_settings=_make_instance_settings(),
        telegram_bridge=None,
    )
    _register_context(context)
    tool = _telegram_tool()

    try:
        with pytest.raises(ToolValidationError, match="session"):
            _run(tool.execute({"command": "start"}, ToolRuntimeContext(instance_name="main")))
    finally:
        _clear_registry()


def test_telegram_manage_tool_requires_active_instance_runtime():
    _clear_registry()
    tool = _telegram_tool()
    with pytest.raises(ToolValidationError, match="No active instance runtime for Telegram"):
        _run(tool.execute({"command": "status"}, ToolRuntimeContext(instance_name="missing")))


def test_telegram_manage_tool_bind_owner_requires_connected_chat():
    db = FakeDB()
    context = _build_context(
        name="main",
        db=db,
        instance_settings=_make_instance_settings(),
        telegram_bridge=None,
    )
    _register_context(context)
    tool = _telegram_tool()

    try:
        with patch(
            "app.services.telegram.resolve_owner_user_id_from_session",
            new=AsyncMock(return_value="admin"),
        ):
            with pytest.raises(ToolExecutionError, match="Chat not connected"):
                _run(
                    tool.execute(
                        {
                            "command": "bind_owner",
                            "chat_id": 12345,
                        },
                        ToolRuntimeContext(session_id=uuid4(), instance_name="main"),
                    )
                )
    finally:
        _clear_registry()


def test_telegram_manage_tool_start_clears_owner_binding_on_owner_change():
    db = FakeDB()
    instance_settings = _make_instance_settings(
        telegram_bot_token="12345:abcde",
        telegram_owner_user_id="old-admin",
        telegram_owner_chat_id="12345",
        telegram_owner_telegram_user_id="777",
    )
    context = _build_context(
        name="main",
        db=db,
        instance_settings=instance_settings,
        telegram_bridge=None,
    )
    _register_context(context)
    tool = _telegram_tool()

    rebuilt_settings = _make_instance_settings(
        telegram_bot_token="12345:abcde",
        telegram_owner_user_id="new-admin",
    )
    rebuilt = _build_context(
        name="main",
        db=db,
        instance_settings=rebuilt_settings,
        telegram_bridge=None,
    )

    async def _fake_rebuild(*, app_state, context):  # noqa: ARG001
        _register_context(rebuilt)
        return rebuilt

    try:
        with (
            patch(
                "app.services.telegram.resolve_owner_user_id_from_session",
                new=AsyncMock(return_value="new-admin"),
            ),
            patch(
                "app.services.araios.system_modules.telegram.handlers._upsert_setting",
                new=AsyncMock(return_value=None),
            ) as upsert_mock,
            patch(
                "app.services.araios.system_modules.telegram.handlers._delete_setting",
                new=AsyncMock(return_value=None),
            ) as delete_mock,
            patch.object(
                instance_runtime_context_registry,
                "rebuild_context",
                new=AsyncMock(side_effect=_fake_rebuild),
            ) as rebuild_mock,
        ):
            result = _run(
                tool.execute(
                    {
                        "command": "start",
                    },
                    ToolRuntimeContext(session_id=uuid4(), instance_name="main"),
                )
            )
        assert result["success"] is True
        assert result.get("owner_user_id") == "new-admin"
        assert "main_session_id" not in result
        upsert_mock.assert_any_await(context, "telegram_owner_user_id", "new-admin")
        delete_mock.assert_any_await(context, "telegram_owner_chat_id")
        delete_mock.assert_any_await(context, "telegram_owner_telegram_user_id")
        rebuild_mock.assert_awaited_once()
    finally:
        _clear_registry()


def test_telegram_send_refuses_owner_chat_by_default():
    class _Bridge:
        is_running = True
        connected_chats = {12345: {"chat_type": "private", "title": "Owner"}}

        @property
        def bot_username(self):
            return "sentinel_bot"

        async def send_message(self, chat_id: int, text: str) -> bool:
            return True

    db = FakeDB()
    instance_settings = _make_instance_settings(telegram_owner_chat_id="12345")
    context = _build_context(
        name="main",
        db=db,
        instance_settings=instance_settings,
        telegram_bridge=_Bridge(),
    )
    _register_context(context)
    tool = _telegram_tool()

    try:
        with pytest.raises(ToolExecutionError, match="owner Telegram DM"):
            _run(
                tool.execute(
                    {"command": "send", "chat_id": 12345, "message": "hello"},
                    ToolRuntimeContext(instance_name="main"),
                )
            )
    finally:
        _clear_registry()


def test_per_instance_bridges_keep_independent_db_and_runtime_support():
    db_a = FakeDB()
    db_b = FakeDB()
    factory_a = _db_factory_for(db_a)
    factory_b = _db_factory_for(db_b)
    support_a = object()
    support_b = object()

    bridge_a = TelegramBridge(
        bot_token="token-a",
        user_id="owner-a",
        agent_runtime_support=support_a,
        run_registry=object(),
        ws_manager=object(),
        db_factory=factory_a,
        instance_settings=_make_instance_settings(
            telegram_bot_token="token-a", telegram_owner_user_id="owner-a"
        ),
    )
    bridge_b = TelegramBridge(
        bot_token="token-b",
        user_id="owner-b",
        agent_runtime_support=support_b,
        run_registry=object(),
        ws_manager=object(),
        db_factory=factory_b,
        instance_settings=_make_instance_settings(
            telegram_bot_token="token-b", telegram_owner_user_id="owner-b"
        ),
    )

    assert bridge_a is not bridge_b
    assert bridge_a._db_factory is factory_a  # noqa: SLF001
    assert bridge_b._db_factory is factory_b  # noqa: SLF001
    assert bridge_a._agent_runtime_support is support_a  # noqa: SLF001
    assert bridge_b._agent_runtime_support is support_b  # noqa: SLF001
    assert bridge_a._user_id == "owner-a"  # noqa: SLF001
    assert bridge_b._user_id == "owner-b"  # noqa: SLF001
    assert bridge_a._bot_token == "token-a"  # noqa: SLF001
    assert bridge_b._bot_token == "token-b"  # noqa: SLF001


def test_configure_rejects_duplicate_bot_token_across_instances():
    db_a = FakeDB()
    db_b = FakeDB()
    other_settings = _make_instance_settings(
        telegram_bot_token="dup-token",
        telegram_owner_user_id="owner-other",
    )
    other_context = _build_context(
        name="other",
        db=db_a,
        instance_settings=other_settings,
        telegram_bridge=None,
    )
    acting_context = _build_context(
        name="main",
        db=db_b,
        instance_settings=_make_instance_settings(),
        telegram_bridge=None,
    )
    _register_context(other_context)
    _register_context(acting_context)
    tool = _telegram_tool()

    try:
        with patch(
            "app.services.telegram.resolve_owner_user_id_from_session",
            new=AsyncMock(return_value="admin"),
        ):
            with pytest.raises(
                ToolValidationError, match="Telegram bot token already used by another instance"
            ):
                _run(
                    tool.execute(
                        {
                            "command": "configure",
                            "bot_token": "dup-token",
                        },
                        ToolRuntimeContext(session_id=uuid4(), instance_name="main"),
                    )
                )
    finally:
        _clear_registry()


def test_rebuild_context_stops_old_bridge():
    db = FakeDB()
    stop_mock = AsyncMock(return_value=None)
    old_bridge = _build_bridge(db=db, user_id="admin")
    old_bridge.stop = stop_mock

    instance_settings = _make_instance_settings(telegram_bot_token="12345:abcde")
    context = _build_context(
        name="main",
        db=db,
        instance_settings=instance_settings,
        telegram_bridge=old_bridge,
    )
    _register_context(context)

    rebuilt_settings = _make_instance_settings(telegram_bot_token="12345:abcde")
    rebuilt = _build_context(
        name="main",
        db=db,
        instance_settings=rebuilt_settings,
        telegram_bridge=None,
    )

    async def _fake_build(*, app_state, instance, session_factory):  # noqa: ARG001
        return rebuilt

    try:
        with patch(
            "app.services.instance_runtime_context._build_instance_runtime_context",
            new=AsyncMock(side_effect=_fake_build),
        ):
            result = _run(
                instance_runtime_context_registry.rebuild_context(
                    app_state=SimpleNamespace(),
                    context=context,
                )
            )
        assert result is rebuilt
        stop_mock.assert_awaited_once()
    finally:
        _clear_registry()


def test_handle_ask_queues_override_text():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    update = SimpleNamespace(
        message=SimpleNamespace(text="/ask hello world", caption=None, reply_text=AsyncMock()),
        effective_chat=SimpleNamespace(id=123, type="group", title="Ops"),
        effective_user=SimpleNamespace(
            id=42, full_name="John Smith", first_name="John", username="john"
        ),
    )
    context = SimpleNamespace(args=["hello", "world"])

    async def _exercise() -> dict:
        await bridge._handle_ask(update, context)  # noqa: SLF001
        _queued_update, metadata = await bridge._queue.get()  # noqa: SLF001
        return metadata

    metadata = _run(_exercise())
    assert metadata["telegram_text_override"] == "hello world"
    assert metadata["telegram_chat_type"] == "group"


def test_handle_ask_without_args_shows_usage_and_does_not_queue():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    reply_text = AsyncMock()
    update = SimpleNamespace(
        message=SimpleNamespace(text="/ask", caption=None, reply_text=reply_text),
        effective_chat=SimpleNamespace(id=123, type="group", title="Ops"),
        effective_user=SimpleNamespace(
            id=42, full_name="John Smith", first_name="John", username="john"
        ),
    )
    context = SimpleNamespace(args=[])

    _run(bridge._handle_ask(update, context))  # noqa: SLF001
    reply_text.assert_awaited_once()
    assert bridge._queue.empty()  # noqa: SLF001


def test_enqueue_marks_owner_by_owner_chat_id_fallback():
    db = FakeDB()
    instance_settings = _make_instance_settings(
        telegram_owner_chat_id="123",
        telegram_owner_telegram_user_id=None,
    )
    bridge = _build_bridge(db=db, user_id="admin", instance_settings=instance_settings)
    update = SimpleNamespace(
        message=SimpleNamespace(text="hello", caption=None, reply_text=AsyncMock()),
        effective_chat=SimpleNamespace(id=123, type="private", title=None, full_name="Owner"),
        effective_user=SimpleNamespace(
            id=999, full_name="John Smith", first_name="John", username="john"
        ),
    )
    context = SimpleNamespace(args=[])

    async def _exercise() -> dict:
        await bridge._handle_message(update, context)  # noqa: SLF001
        _queued_update, metadata = await bridge._queue.get()  # noqa: SLF001
        return metadata

    metadata = _run(_exercise())
    assert metadata["telegram_is_owner"] is True


def test_send_chunked_to_chat_formats_markdown_using_html_parse_mode():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    bridge._app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))  # noqa: SLF001
    bridge._running = True  # noqa: SLF001

    _run(
        bridge._send_chunked_to_chat(  # noqa: SLF001
            123,
            "**bold** `code` [site](https://example.com)\n\n```py\nprint('x')\n```",
        )
    )

    calls = bridge._app.bot.send_message.await_args_list  # noqa: SLF001
    assert len(calls) == 1
    assert calls[0].kwargs["chat_id"] == 123
    assert calls[0].kwargs["parse_mode"] == ParseMode.HTML
    rendered = calls[0].kwargs["text"]
    assert "<b>bold</b>" in rendered
    assert "<code>code</code>" in rendered
    assert '<a href="https://example.com">site</a>' in rendered
    assert "<pre>print(&#x27;x&#x27;)</pre>" in rendered


def test_send_chunked_reply_formats_markdown_using_html_parse_mode():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))

    _run(bridge._send_chunked(update, "*italic* and **bold**"))  # noqa: SLF001

    calls = update.message.reply_text.await_args_list
    assert len(calls) == 1
    assert calls[0].kwargs["parse_mode"] == ParseMode.HTML
    assert calls[0].args[0] == "<i>italic</i> and <b>bold</b>"


def test_deliver_inline_owner_reply_finalizes_existing_stream_message():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    bridge._app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))  # noqa: SLF001
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))
    streamed_message = SimpleNamespace(edit_text=AsyncMock())

    _run(
        bridge._deliver_inline_owner_reply(  # noqa: SLF001
            update,
            chat_id=123,
            final_text="**done**",
            attachments=[],
            streamed_message=streamed_message,
        )
    )

    streamed_message.edit_text.assert_awaited_once_with(
        "<b>done</b>",
        parse_mode=ParseMode.HTML,
    )
    update.message.reply_text.assert_not_called()


def test_telegram_tool_result_summary_skips_internal_telegram_tool():
    assert _telegram_tool_result_summary(tool_name="telegram", content="{}", is_error=False) is None


def test_telegram_tool_result_summary_formats_compact_code_block():
    summary = _telegram_tool_result_summary(
        tool_name="runtime",
        content="line 1\nline 2",
        is_error=False,
    )
    assert summary == "Tool Result · runtime\n\n```\nline 1\nline 2\n```"


def test_should_reply_inline_owner_private_only():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    private_chat = SimpleNamespace(id=123, type="private")
    group_chat = SimpleNamespace(id=-1001, type="group")
    assert (
        bridge._should_reply_inline(private_chat, {"telegram_is_owner": True}) is True
    )  # noqa: SLF001
    assert (
        bridge._should_reply_inline(private_chat, {"telegram_is_owner": False}) is False
    )  # noqa: SLF001
    assert (
        bridge._should_reply_inline(group_chat, {"telegram_is_owner": True}) is False
    )  # noqa: SLF001


def test_resolve_inbound_session_group_routes_to_persistent_channel():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    group_chat = SimpleNamespace(id=-100123, type="supergroup", title="Ops")
    user_a = SimpleNamespace(id=111, full_name="John Smith", first_name="John")
    user_b = SimpleNamespace(id=222, full_name="Ron Cahlon", first_name="Ron")

    first_session_id, first_scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=group_chat,
            user=user_a,
            metadata={"telegram_is_owner": False},
        )
    )
    second_session_id, second_scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=group_chat,
            user=user_b,
            metadata={"telegram_is_owner": False},
        )
    )

    assert first_scope == "group_channel"
    assert second_scope == "group_channel"
    assert first_session_id == second_session_id
    sessions = db.storage[Session]
    assert any((s.title or "").startswith("TG Group · Ops") for s in sessions)
    bindings = db.storage[SessionBinding]
    assert any(
        b.binding_type == session_bindings.TELEGRAM_GROUP_BINDING_TYPE
        and b.binding_key == "group:-100123"
        and b.session_id == first_session_id
        and b.is_active
        for b in bindings
    )


def test_resolve_inbound_session_non_owner_dm_has_private_channel_per_user():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    chat_ron = SimpleNamespace(id=555, type="private", title=None)
    ron = SimpleNamespace(id=555, full_name="Ron Cahlon", first_name="Ron")
    chat_john = SimpleNamespace(id=777, type="private", title=None)
    john = SimpleNamespace(id=777, full_name="John Smith", first_name="John")

    ron_session_1, ron_scope_1 = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=chat_ron,
            user=ron,
            metadata={"telegram_is_owner": False},
        )
    )
    ron_session_2, ron_scope_2 = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=chat_ron,
            user=ron,
            metadata={"telegram_is_owner": False},
        )
    )
    john_session, john_scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=chat_john,
            user=john,
            metadata={"telegram_is_owner": False},
        )
    )

    assert ron_scope_1 == "dm_channel"
    assert ron_scope_2 == "dm_channel"
    assert john_scope == "dm_channel"
    assert ron_session_1 == ron_session_2
    assert john_session != ron_session_1

    bindings = db.storage[SessionBinding]
    assert any(
        b.binding_type == session_bindings.TELEGRAM_DM_BINDING_TYPE
        and b.binding_key == "dm:555:555"
        and b.session_id == ron_session_1
        and b.is_active
        for b in bindings
    )
    assert any(
        b.binding_type == session_bindings.TELEGRAM_DM_BINDING_TYPE
        and b.binding_key == "dm:777:777"
        and b.session_id == john_session
        and b.is_active
        for b in bindings
    )


def _owner_instance_settings() -> Settings:
    """Instance settings where the owner is recognized via telegram user id 123."""
    return _make_instance_settings(
        telegram_owner_telegram_user_id="123",
        telegram_owner_chat_id="123",
    )


def test_handle_session_owner_dm_returns_keyboard():
    db = FakeDB()
    main = Session(user_id="admin", title="Main")
    project = Session(user_id="admin", title="Project Alpha")
    db.add(main)
    db.add(project)
    _run(session_bindings.set_owner_active_session(db, user_id="admin", session_id=main.id))

    bridge = _build_bridge(db=db, user_id="admin", instance_settings=_owner_instance_settings())
    reply_text = AsyncMock()
    update = SimpleNamespace(
        message=SimpleNamespace(reply_text=reply_text),
        effective_chat=SimpleNamespace(id=123, type="private", title=None),
        effective_user=SimpleNamespace(id=123, full_name="Owner", first_name="Owner"),
    )
    context = SimpleNamespace(args=[])

    _run(bridge._handle_session(update, context))  # noqa: SLF001

    reply_text.assert_awaited_once()
    call = reply_text.await_args
    keyboard = call.kwargs.get("reply_markup")
    assert keyboard is not None
    button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
    callback_targets = {button.callback_data for row in keyboard.inline_keyboard for button in row}
    assert any("Main" in text for text in button_texts)
    assert any("Project Alpha" in text for text in button_texts)
    assert f"sess:{main.id}" in callback_targets
    # The current owner DM session is marked with the checkmark prefix.
    assert any(text.startswith("✅ ") and "Main" in text for text in button_texts)


def test_handle_session_refuses_non_owner():
    db = FakeDB()
    main = Session(user_id="admin", title="Main")
    db.add(main)

    bridge = _build_bridge(db=db, user_id="admin", instance_settings=_owner_instance_settings())
    reply_text = AsyncMock()
    # Group chat from a non-owner user -> gate must refuse.
    update = SimpleNamespace(
        message=SimpleNamespace(reply_text=reply_text),
        effective_chat=SimpleNamespace(id=-100, type="group", title="Ops"),
        effective_user=SimpleNamespace(id=999, full_name="Stranger", first_name="Strange"),
    )
    context = SimpleNamespace(args=[])

    _run(bridge._handle_session(update, context))  # noqa: SLF001

    reply_text.assert_awaited_once()
    call = reply_text.await_args
    assert "owner" in call.args[0].lower()
    assert call.kwargs.get("reply_markup") is None


def test_handle_session_callback_owner_switches_binding():
    db = FakeDB()
    main = Session(user_id="admin", title="Main")
    project = Session(user_id="admin", title="Project Alpha")
    db.add(main)
    db.add(project)

    bridge = _build_bridge(db=db, user_id="admin", instance_settings=_owner_instance_settings())
    query = SimpleNamespace(
        data=f"sess:{project.id}",
        from_user=SimpleNamespace(id=123, full_name="Owner", first_name="Owner"),
        message=SimpleNamespace(chat=SimpleNamespace(id=123, type="private", title=None)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(args=[])

    _run(bridge._handle_session_callback(update, context))  # noqa: SLF001

    query.answer.assert_awaited_with("Switched")
    query.edit_message_text.assert_awaited_once()

    resolved = _run(
        session_bindings.resolve_owner_active_session(db, user_id="admin", agent_id="dev-agent")
    )
    assert resolved.id == project.id


def test_handle_session_callback_refuses_non_owner_and_does_not_switch():
    db = FakeDB()
    main = Session(user_id="admin", title="Main")
    project = Session(user_id="admin", title="Project Alpha")
    db.add(main)
    db.add(project)

    bridge = _build_bridge(db=db, user_id="admin", instance_settings=_owner_instance_settings())
    query = SimpleNamespace(
        data=f"sess:{project.id}",
        from_user=SimpleNamespace(id=999, full_name="Stranger", first_name="Strange"),
        message=SimpleNamespace(chat=SimpleNamespace(id=555, type="private", title=None)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(args=[])

    _run(bridge._handle_session_callback(update, context))  # noqa: SLF001

    query.answer.assert_awaited_once_with("Not authorized", show_alert=True)
    query.edit_message_text.assert_not_called()
    # No owner_active binding was created.
    assert not any(
        b.binding_type == session_bindings.OWNER_ACTIVE_BINDING_TYPE
        for b in db.storage[SessionBinding]
    )
    resolved = _run(
        session_bindings.resolve_owner_active_session(db, user_id="admin", agent_id="dev-agent")
    )
    assert resolved is None


def test_register_bot_commands_scopes_session_to_owner_chat():
    db = FakeDB()
    settings = _make_instance_settings(telegram_owner_chat_id="4242")
    bridge = _build_bridge(db=db, user_id="admin", instance_settings=settings)
    app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=AsyncMock()))

    _run(bridge._register_bot_commands(app))  # noqa: SLF001

    calls = app.bot.set_my_commands.call_args_list
    assert len(calls) == 2
    default_cmds = [c.command for c in calls[0].args[0]]
    owner_cmds = [c.command for c in calls[1].args[0]]
    assert "session" not in default_cmds  # everyone-scope menu hides the owner command
    assert "session" in owner_cmds  # owner-chat-scope menu exposes it
    assert type(calls[1].kwargs["scope"]).__name__ == "BotCommandScopeChat"


def test_register_bot_commands_without_owner_omits_session():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")  # no owner_chat_id configured
    app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=AsyncMock()))

    _run(bridge._register_bot_commands(app))  # noqa: SLF001

    calls = app.bot.set_my_commands.call_args_list
    assert len(calls) == 1  # only the default-scope menu, no owner-scoped one
    assert "session" not in [c.command for c in calls[0].args[0]]
