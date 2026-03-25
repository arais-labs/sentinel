from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.config import settings
from app.models import Session, SessionBinding
from app.services.araios.runtime_services import configure_runtime_services, reset_runtime_services
from app.services.araios.system_modules.telegram.module import MODULE as TELEGRAM_MODULE
from app.services.sessions import session_bindings
from app.services.telegram import (
    TelegramBridge,
    start_telegram_bridge,
)
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _telegram_tool(app_state):
    reset_runtime_services()
    configure_runtime_services(app_state=app_state)
    return TELEGRAM_MODULE.to_tool_definitions()[0]


def _build_bridge(*, db: FakeDB, user_id: str) -> TelegramBridge:
    class _DBFactory:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return None

    return TelegramBridge(
        bot_token="dummy",
        user_id=user_id,
        agent_loop=None,
        run_registry=object(),
        ws_manager=object(),
        db_factory=lambda: _DBFactory(),
    )


def test_owner_dm_route_creates_main_session_when_missing():
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
    assert scope == "owner_main"
    assert session_id is not None

    sessions = db.storage[Session]
    bindings = db.storage[SessionBinding]
    assert len(sessions) == 1
    assert sessions[0].id == session_id
    assert sessions[0].title == "Main"
    assert any(
        b.binding_type == session_bindings.MAIN_BINDING_TYPE
        and b.binding_key == session_bindings.MAIN_BINDING_KEY
        and b.session_id == session_id
        and b.is_active
        for b in bindings
    )


def test_owner_dm_route_uses_canonical_main_binding():
    db = FakeDB()
    older = Session(user_id="admin", title="Old")
    newer = Session(user_id="admin", title="New")
    db.add(older)
    db.add(newer)
    _run(
        session_bindings.set_main_session(
            db,
            user_id="admin",
            session_id=older.id,
        )
    )
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

    assert scope == "owner_main"
    assert session_id == older.id


def test_telegram_manage_tool_configure_sets_owner_and_ensures_main():
    app_state = type(
        "State", (), {"telegram_bridge": None, "telegram_stop_event": None, "telegram_task": None}
    )()
    tool = _telegram_tool(app_state)

    old_token = settings.telegram_bot_token
    old_owner = settings.telegram_owner_user_id
    old_dev_user = settings.dev_user_id
    settings.dev_user_id = "dev-admin"
    settings.telegram_bot_token = None
    settings.telegram_owner_user_id = None

    class _DBFactory:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    try:
        with (
            patch(
                "app.services.telegram.resolve_owner_user_id_from_session",
                new=AsyncMock(return_value="admin"),
            ),
            patch(
                "app.services.telegram.persist_telegram_settings",
                new=AsyncMock(return_value=None),
            ) as persist_mock,
            patch(
                "app.services.telegram.start_telegram_bridge",
                new=AsyncMock(return_value=True),
            ) as start_mock,
            patch(
                "app.services.araios.system_modules.telegram.handlers.AsyncSessionLocal",
                return_value=_DBFactory(),
            ),
            patch(
                "app.services.araios.system_modules.telegram.handlers.session_bindings.resolve_or_create_main_session",
                new=AsyncMock(return_value=Session(user_id="admin", title="Main")),
            ),
            patch(
                "app.services.telegram.resolve_latest_active_root_session_id_for_user",
                new=AsyncMock(return_value="main-session-id"),
            ),
        ):
            result = _run(
                tool.execute(
                    {
                        "command": "configure",
                        "bot_token": "12345:abcde",
                    },
                    ToolRuntimeContext(session_id=uuid4()),
                )
            )

        assert result["success"] is True
        assert settings.telegram_owner_user_id == "admin"
        assert result.get("main_session_id") == "main-session-id"
        persist_mock.assert_awaited_once()
        start_mock.assert_awaited_once()
    finally:
        settings.telegram_bot_token = old_token
        settings.telegram_owner_user_id = old_owner
        settings.dev_user_id = old_dev_user


def test_telegram_manage_tool_requires_session_for_mutations():
    app_state = type(
        "State", (), {"telegram_bridge": None, "telegram_stop_event": None, "telegram_task": None}
    )()
    tool = _telegram_tool(app_state)

    with pytest.raises(ToolValidationError, match="session"):
        _run(tool.execute({"command": "start"}, ToolRuntimeContext()))


def test_telegram_manage_tool_bind_owner_requires_connected_chat():
    app_state = type(
        "State", (), {"telegram_bridge": None, "telegram_stop_event": None, "telegram_task": None}
    )()
    tool = _telegram_tool(app_state)

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
                    ToolRuntimeContext(session_id=uuid4()),
                )
            )


def test_telegram_manage_tool_start_clears_owner_binding_on_owner_change():
    app_state = type(
        "State", (), {"telegram_bridge": None, "telegram_stop_event": None, "telegram_task": None}
    )()
    tool = _telegram_tool(app_state)

    old_token = settings.telegram_bot_token
    old_owner = settings.telegram_owner_user_id
    old_owner_chat = settings.telegram_owner_chat_id
    old_owner_tg_user = settings.telegram_owner_telegram_user_id

    settings.telegram_bot_token = "12345:abcde"
    settings.telegram_owner_user_id = "old-admin"
    settings.telegram_owner_chat_id = "12345"
    settings.telegram_owner_telegram_user_id = "777"

    class _DBFactory:
        async def __aenter__(self):
            return FakeDB()

        async def __aexit__(self, exc_type, exc, tb):
            return None

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
            patch(
                "app.services.telegram.start_telegram_bridge",
                new=AsyncMock(return_value=True),
            ) as start_mock,
            patch(
                "app.services.araios.system_modules.telegram.handlers.AsyncSessionLocal",
                return_value=_DBFactory(),
            ),
            patch(
                "app.services.araios.system_modules.telegram.handlers.session_bindings.resolve_or_create_main_session",
                new=AsyncMock(return_value=Session(user_id="new-admin", title="Main")),
            ),
            patch(
                "app.services.telegram.resolve_latest_active_root_session_id_for_user",
                new=AsyncMock(return_value="main-session-id"),
            ),
        ):
            result = _run(
                tool.execute(
                    {
                        "command": "start",
                    },
                    ToolRuntimeContext(session_id=uuid4()),
                )
            )
        assert result["success"] is True
        assert settings.telegram_owner_user_id == "new-admin"
        assert settings.telegram_owner_chat_id is None
        assert settings.telegram_owner_telegram_user_id is None
        upsert_mock.assert_awaited_with("telegram_owner_user_id", "new-admin")
        delete_mock.assert_any_await("telegram_owner_chat_id")
        delete_mock.assert_any_await("telegram_owner_telegram_user_id")
        start_mock.assert_awaited_once()
    finally:
        settings.telegram_bot_token = old_token
        settings.telegram_owner_user_id = old_owner
        settings.telegram_owner_chat_id = old_owner_chat
        settings.telegram_owner_telegram_user_id = old_owner_tg_user


def test_send_telegram_message_tool_refuses_owner_chat_by_default():
    class _Bridge:
        is_running = True
        connected_chats = {12345: {"chat_type": "private", "title": "Owner"}}

        @property
        def bot_username(self):
            return "sentinel_bot"

        async def send_message(self, chat_id: int, text: str) -> bool:
            return True

    app_state = type("State", (), {"telegram_bridge": _Bridge()})()
    tool = _telegram_tool(app_state)

    old_owner_chat = settings.telegram_owner_chat_id
    settings.telegram_owner_chat_id = "12345"
    try:
        with pytest.raises(ToolExecutionError, match="owner Telegram DM"):
            _run(tool.execute({"command": "send", "chat_id": 12345, "message": "hello"}, ToolRuntimeContext()))
    finally:
        settings.telegram_owner_chat_id = old_owner_chat


def test_start_telegram_bridge_uses_dev_owner_when_owner_unset():
    app_state = type(
        "State",
        (),
        {
            "ws_manager": object(),
            "agent_run_registry": object(),
            "agent_loop": object(),
            "telegram_bridge": None,
            "telegram_stop_event": None,
            "telegram_task": None,
        },
    )()

    old_token = settings.telegram_bot_token
    old_owner = settings.telegram_owner_user_id
    old_dev_user = settings.dev_user_id

    settings.telegram_bot_token = "12345:abcde"
    settings.telegram_owner_user_id = None
    settings.dev_user_id = "dev-admin"
    try:
        with patch(
            "app.services.telegram.TelegramBridge.start",
            new=AsyncMock(return_value=None),
        ):
            started = _run(start_telegram_bridge(app_state))
        assert started is True
        assert app_state.telegram_bridge is not None
        assert app_state.telegram_bridge._user_id == "dev-admin"  # noqa: SLF001
    finally:
        settings.telegram_bot_token = old_token
        settings.telegram_owner_user_id = old_owner
        settings.dev_user_id = old_dev_user


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
    bridge = _build_bridge(db=db, user_id="admin")
    old_owner_chat = settings.telegram_owner_chat_id
    old_owner_tg_user = settings.telegram_owner_telegram_user_id
    settings.telegram_owner_chat_id = "123"
    settings.telegram_owner_telegram_user_id = None
    try:
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
    finally:
        settings.telegram_owner_chat_id = old_owner_chat
        settings.telegram_owner_telegram_user_id = old_owner_tg_user


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
