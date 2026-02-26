from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import UUID

from sqlalchemy import select
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Message as MessageModel, Session as SessionModel
from app.models.system import SystemSetting

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MSG_LEN = 4096
TELEGRAM_OWNER_PAIRING_TTL_SECONDS = 600
TELEGRAM_CHAT_ROUTES_KEY = "telegram_chat_routes"


def mask_telegram_token(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]


async def _upsert_setting(key: str, value: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = result.scalars().first()
        if setting is None:
            db.add(SystemSetting(key=key, value=value))
        else:
            setting.value = value
        await db.commit()


async def _delete_setting(key: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        setting = result.scalars().first()
        if setting is not None:
            await db.delete(setting)
            await db.commit()


async def persist_telegram_settings(
    *,
    bot_token: str,
    owner_user_id: str,
    target_session_id: str | None = None,
    owner_chat_id: str | None = None,
    owner_telegram_user_id: str | None = None,
) -> None:
    await _upsert_setting("telegram_bot_token", bot_token)
    await _upsert_setting("telegram_owner_user_id", owner_user_id)
    if target_session_id:
        await _upsert_setting("telegram_target_session_id", target_session_id)
    else:
        await _delete_setting("telegram_target_session_id")
    if owner_chat_id:
        await _upsert_setting("telegram_owner_chat_id", owner_chat_id)
    else:
        await _delete_setting("telegram_owner_chat_id")
    if owner_telegram_user_id:
        await _upsert_setting("telegram_owner_telegram_user_id", owner_telegram_user_id)
    else:
        await _delete_setting("telegram_owner_telegram_user_id")


async def clear_telegram_settings() -> None:
    await _delete_setting("telegram_bot_token")
    await _delete_setting("telegram_owner_user_id")
    await _delete_setting("telegram_target_session_id")
    await _delete_setting("telegram_owner_chat_id")
    await _delete_setting("telegram_owner_telegram_user_id")
    await _delete_setting("telegram_pairing_code_hash")
    await _delete_setting("telegram_pairing_code_expires_at")
    await _delete_setting(TELEGRAM_CHAT_ROUTES_KEY)


def _pairing_code_hash(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _pairing_not_expired(expires_at_iso: str | None) -> bool:
    expires_at = _parse_iso_datetime(expires_at_iso)
    return bool(expires_at and expires_at > _utcnow())


async def issue_owner_pairing_code() -> tuple[str, str]:
    code = secrets.token_hex(3).upper()
    expires_at = (_utcnow() + timedelta(seconds=TELEGRAM_OWNER_PAIRING_TTL_SECONDS)).isoformat()
    settings.telegram_pairing_code_hash = _pairing_code_hash(code)
    settings.telegram_pairing_code_expires_at = expires_at
    await _upsert_setting("telegram_pairing_code_hash", settings.telegram_pairing_code_hash)
    await _upsert_setting("telegram_pairing_code_expires_at", expires_at)
    return (code, expires_at)


async def clear_owner_pairing_code() -> None:
    settings.telegram_pairing_code_hash = None
    settings.telegram_pairing_code_expires_at = None
    await _delete_setting("telegram_pairing_code_hash")
    await _delete_setting("telegram_pairing_code_expires_at")


async def stop_telegram_bridge(app_state: object) -> None:
    stop_event = getattr(app_state, "telegram_stop_event", None)
    bridge = getattr(app_state, "telegram_bridge", None)
    task = getattr(app_state, "telegram_task", None)

    if stop_event is not None:
        stop_event.set()

    if bridge is not None:
        await bridge.stop()

    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            pass

    app_state.telegram_bridge = None
    app_state.telegram_stop_event = None
    app_state.telegram_task = None


async def resolve_latest_active_root_session_id_for_user(user_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionModel).where(
                SessionModel.user_id == user_id,
                SessionModel.status == "active",
                SessionModel.parent_session_id.is_(None),
            )
        )
        sessions = result.scalars().all()
        if not sessions:
            return None
        sessions.sort(
            key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return str(sessions[0].id)


async def reconcile_telegram_target_session(user_id: str) -> str | None:
    """Use existing active target when valid; otherwise fall back to latest active root."""
    existing = settings.telegram_target_session_id
    existing_session: SessionModel | None = None
    if isinstance(existing, str) and existing.strip():
        try:
            parsed = UUID(existing.strip())
        except ValueError:
            parsed = None
        if parsed is not None:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(SessionModel).where(
                        SessionModel.id == parsed,
                        SessionModel.user_id == user_id,
                        SessionModel.parent_session_id.is_(None),
                    )
                )
                existing_session = result.scalars().first()
                if existing_session is not None and existing_session.status == "active":
                    return str(existing_session.id)

    latest_active = await resolve_latest_active_root_session_id_for_user(user_id)
    if latest_active:
        settings.telegram_target_session_id = latest_active
        await _upsert_setting("telegram_target_session_id", latest_active)
        return latest_active

    if existing_session is not None:
        return str(existing_session.id)
    return None


async def start_telegram_bridge(app_state: object) -> bool:
    token = settings.telegram_bot_token
    if not token:
        return False

    await stop_telegram_bridge(app_state)

    ws_manager = getattr(app_state, "ws_manager", None)
    run_registry = getattr(app_state, "agent_run_registry", None)
    agent_loop = getattr(app_state, "agent_loop", None)
    owner_user_id = settings.telegram_owner_user_id
    if not owner_user_id and settings.telegram_target_session_id:
        owner_user_id = await resolve_owner_user_id_from_session(
            settings.telegram_target_session_id
        )
        if owner_user_id:
            settings.telegram_owner_user_id = owner_user_id
            await _upsert_setting("telegram_owner_user_id", owner_user_id)
    if not owner_user_id:
        owner_user_id = settings.dev_user_id
    settings.telegram_target_session_id = await reconcile_telegram_target_session(owner_user_id)

    bridge = TelegramBridge(
        bot_token=token,
        user_id=owner_user_id,
        agent_loop=agent_loop,
        run_registry=run_registry,
        ws_manager=ws_manager,
        db_factory=AsyncSessionLocal,
    )

    stop_event = asyncio.Event()
    task = asyncio.create_task(bridge.start(stop_event))
    app_state.telegram_bridge = bridge
    app_state.telegram_stop_event = stop_event
    app_state.telegram_task = task
    return True


async def resolve_owner_user_id_from_session(session_id: str | None) -> str | None:
    if not session_id:
        return None
    try:
        parsed = UUID(session_id)
    except (ValueError, TypeError):
        return None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == parsed))
        session = result.scalars().first()
        if session is None:
            return None
        return session.user_id


class TelegramBridge:
    """Bridges Telegram chats to Sentinel with deterministic per-channel routing."""

    def __init__(
        self,
        *,
        bot_token: str,
        user_id: str,
        agent_loop: object | None,
        run_registry: object,
        ws_manager: object,
        db_factory: object,
    ) -> None:
        self._bot_token = bot_token
        self._user_id = user_id
        self._agent_loop = agent_loop
        self._run_registry = run_registry
        self._ws_manager = ws_manager
        self._db_factory = db_factory

        self._app: Application | None = None
        self._queue: asyncio.Queue[tuple[Update, dict]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._bot_username: str | None = None
        self._can_read_all_group_messages: bool | None = None
        self._connected_chats: dict[int, dict] = {}

    # -- public properties ---------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bot_username(self) -> str | None:
        return self._bot_username

    @property
    def connected_chats(self) -> dict[int, dict]:
        return dict(self._connected_chats)

    @property
    def can_read_all_group_messages(self) -> bool | None:
        return self._can_read_all_group_messages

    def update_agent_loop(self, agent_loop: object) -> None:
        self._agent_loop = agent_loop

    def _owner_chat_id(self) -> int | None:
        raw = settings.telegram_owner_chat_id
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return int(raw.strip())
        except ValueError:
            return None

    def _is_owner_sender(self, chat: object | None, user: object | None) -> bool:
        if chat is None or getattr(chat, "type", None) != "private":
            return False
        expected_user_id = settings.telegram_owner_telegram_user_id
        if (
            expected_user_id
            and user is not None
            and str(getattr(user, "id", "")) == str(expected_user_id)
        ):
            return True
        owner_chat_id = self._owner_chat_id()
        if owner_chat_id is not None and int(getattr(chat, "id", 0)) == owner_chat_id:
            return True
        return False

    def _should_reply_inline(self, chat: object | None, metadata: dict) -> bool:
        return bool(
            chat is not None
            and getattr(chat, "type", None) == "private"
            and metadata.get("telegram_is_owner")
        )

    @staticmethod
    def _to_int(value: object) -> int | None:
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_json_dict(raw: str) -> dict:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _build_telegram_audit_line(
        *,
        chat_id: object | None,
        chat_type: str,
        delivered: bool,
        fallback_used: bool,
    ) -> str:
        safe_chat_id = "unknown" if chat_id is None else str(chat_id)
        safe_chat_type = (chat_type or "unknown").lower()
        if delivered:
            mode = "fallback" if fallback_used else "tool"
            return (
                f"Telegram audit: sent reply to chat_id={safe_chat_id} "
                f"({safe_chat_type}, mode={mode})"
            )
        return f"Telegram audit: no reply sent to chat_id={safe_chat_id} ({safe_chat_type})"

    async def _normalize_non_inline_assistant_output(
        self,
        db: object,
        *,
        session_id: UUID,
        created_after: datetime,
        chat_id: object | None,
        chat_type: str,
        delivered: bool,
        fallback_used: bool,
    ) -> None:
        result = await db.execute(select(MessageModel).where(MessageModel.session_id == session_id))
        messages = [
            item
            for item in result.scalars().all()
            if item.role == "assistant"
            and (item.created_at or datetime.min.replace(tzinfo=UTC)) >= created_after
        ]
        messages.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))

        non_empty = [item for item in messages if (item.content or "").strip()]
        audit_line = self._build_telegram_audit_line(
            chat_id=chat_id,
            chat_type=chat_type,
            delivered=delivered,
            fallback_used=fallback_used,
        )
        audit_metadata = {
            "source": "telegram_audit",
            "telegram_chat_id": chat_id,
            "telegram_chat_type": chat_type,
            "telegram_delivery_mode": "fallback" if fallback_used else "tool",
            "telegram_delivery_success": delivered,
        }

        if not non_empty:
            db.add(
                MessageModel(
                    session_id=session_id,
                    role="assistant",
                    content=audit_line,
                    metadata_json=audit_metadata,
                )
            )
            await db.commit()
            return

        for item in non_empty[:-1]:
            item.content = ""

        tail = non_empty[-1]
        tail.content = audit_line
        existing_metadata = tail.metadata_json if isinstance(tail.metadata_json, dict) else {}
        tail.metadata_json = {**existing_metadata, **audit_metadata}
        await db.commit()

    @staticmethod
    def _json_string(value: object) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _route_key(
        *,
        chat_type: str,
        chat_id: int | None,
        sender_user_id: int | None,
    ) -> str | None:
        if chat_id is None:
            return None
        normalized = (chat_type or "").lower()
        if normalized in {"group", "supergroup"}:
            return f"group:{chat_id}"
        if normalized == "private":
            return f"dm:{chat_id}:{sender_user_id or 0}"
        return None

    async def _load_chat_routes(self, db: object) -> dict[str, dict]:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == TELEGRAM_CHAT_ROUTES_KEY))
        setting = result.scalars().first()
        if setting is None:
            return {}
        try:
            parsed = json.loads(setting.value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        routes: dict[str, dict] = {}
        for key, value in parsed.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            routes[key] = value
        return routes

    async def _save_chat_routes(self, db: object, routes: dict[str, dict]) -> None:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == TELEGRAM_CHAT_ROUTES_KEY))
        setting = result.scalars().first()
        payload = self._json_string(routes)
        if setting is None:
            db.add(SystemSetting(key=TELEGRAM_CHAT_ROUTES_KEY, value=payload))
        else:
            setting.value = payload
        await db.commit()

    async def _resolve_owner_main_session_with_db(self, db: object) -> UUID | None:
        """Resolve owner DM route session (canonical main session)."""
        target_session_id = settings.telegram_target_session_id
        target_session: SessionModel | None = None
        if target_session_id:
            try:
                parsed_target = UUID(target_session_id)
            except ValueError:
                parsed_target = None
            if parsed_target is not None:
                target_result = await db.execute(
                    select(SessionModel).where(
                        SessionModel.id == parsed_target,
                        SessionModel.user_id == self._user_id,
                        SessionModel.parent_session_id.is_(None),
                    )
                )
                target_session = target_result.scalars().first()
                if target_session is not None:
                    if target_session.status != "active":
                        target_session.status = "active"
                        target_session.ended_at = None
                        await db.commit()
                    return target_session.id

        result = await db.execute(
            select(SessionModel).where(
                SessionModel.user_id == self._user_id,
                SessionModel.status == "active",
                SessionModel.parent_session_id.is_(None),
            )
        )
        sessions = result.scalars().all()
        if sessions:
            sessions.sort(
                key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            session = sessions[0]
            settings.telegram_target_session_id = str(session.id)
            await _upsert_setting("telegram_target_session_id", str(session.id))
            return session.id

        # If no active root exists, revive newest root.
        fallback_result = await db.execute(
            select(SessionModel).where(
                SessionModel.user_id == self._user_id,
                SessionModel.parent_session_id.is_(None),
            )
        )
        fallback_roots = fallback_result.scalars().all()
        if fallback_roots:
            fallback_roots.sort(
                key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC),
                reverse=True,
            )
            session = fallback_roots[0]
            session.status = "active"
            session.ended_at = None
            await db.commit()
            settings.telegram_target_session_id = str(session.id)
            await _upsert_setting("telegram_target_session_id", str(session.id))
            return session.id

        session = SessionModel(
            user_id=self._user_id,
            agent_id="dev-agent",
            title="Main",
            status="active",
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        settings.telegram_target_session_id = str(session.id)
        await _upsert_setting("telegram_target_session_id", str(session.id))
        return session.id

    async def _resolve_or_create_routed_session(
        self,
        db: object,
        *,
        route_key: str,
        chat_type: str,
        chat_id: int,
        chat_title: str | None,
        sender_user_id: int | None,
        sender_name: str | None,
    ) -> tuple[UUID | None, str]:
        routes = await self._load_chat_routes(db)
        route = routes.get(route_key) if isinstance(routes.get(route_key), dict) else None
        if route is not None:
            raw_session_id = route.get("session_id")
            if isinstance(raw_session_id, str) and raw_session_id.strip():
                try:
                    parsed_session_id = UUID(raw_session_id.strip())
                except ValueError:
                    parsed_session_id = None
                if parsed_session_id is not None:
                    found = await db.execute(
                        select(SessionModel).where(
                            SessionModel.id == parsed_session_id,
                            SessionModel.user_id == self._user_id,
                            SessionModel.parent_session_id.is_(None),
                        )
                    )
                    existing = found.scalars().first()
                    if existing is not None:
                        if existing.status != "active":
                            existing.status = "active"
                            existing.ended_at = None
                            await db.commit()
                        route["last_seen_at"] = datetime.now(UTC).isoformat()
                        route["chat_title"] = chat_title or route.get("chat_title") or ""
                        routes[route_key] = route
                        await self._save_chat_routes(db, routes)
                        return (existing.id, "existing")

        normalized_type = chat_type.lower()
        if normalized_type in {"group", "supergroup"}:
            session_title = f"TG Group · {chat_title or str(chat_id)}"
            guardrail_level = "untrusted_group"
        else:
            display = (sender_name or "").strip() or str(sender_user_id or chat_id)
            session_title = f"TG DM · {display}"
            guardrail_level = "untrusted_private"

        session = SessionModel(
            user_id=self._user_id,
            agent_id="dev-agent",
            title=session_title,
            status="active",
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)

        routes[route_key] = {
            "session_id": str(session.id),
            "chat_id": chat_id,
            "chat_type": normalized_type,
            "chat_title": chat_title or "",
            "sender_user_id": sender_user_id,
            "sender_name": sender_name or "",
            "guardrail_level": guardrail_level,
            "created_at": datetime.now(UTC).isoformat(),
            "last_seen_at": datetime.now(UTC).isoformat(),
        }
        await self._save_chat_routes(db, routes)
        return (session.id, "created")

    async def _resolve_inbound_session(
        self,
        db: object,
        *,
        chat: object | None,
        user: object | None,
        metadata: dict,
    ) -> tuple[UUID | None, str]:
        if self._should_reply_inline(chat, metadata):
            return (await self._resolve_owner_main_session_with_db(db), "owner_main")

        chat_type = str(getattr(chat, "type", "")).lower()
        chat_id = self._to_int(getattr(chat, "id", None))
        sender_user_id = self._to_int(getattr(user, "id", None))
        route_key = self._route_key(
            chat_type=chat_type,
            chat_id=chat_id,
            sender_user_id=sender_user_id,
        )
        if route_key is None or chat_id is None:
            return (None, "unknown")

        sender_name = None
        if user is not None:
            sender_name = getattr(user, "full_name", None) or getattr(user, "first_name", None)
        chat_title = getattr(chat, "title", None) if chat is not None else None

        session_id, _ = await self._resolve_or_create_routed_session(
            db,
            route_key=route_key,
            chat_type=chat_type,
            chat_id=chat_id,
            chat_title=chat_title,
            sender_user_id=sender_user_id,
            sender_name=sender_name,
        )
        if chat_type in {"group", "supergroup"}:
            return (session_id, "group_channel")
        return (session_id, "dm_channel")

    async def send_message(self, chat_id: int, text: str) -> bool:
        """Send a message to a specific Telegram chat. Returns True on success."""
        if not self._running or self._app is None:
            return False
        try:
            await self._send_chunked_to_chat(chat_id, text)
            return True
        except Exception:
            logger.exception("Failed to send message to chat %s", chat_id)
            return False

    async def _send_chunked_to_chat(self, chat_id: int, text: str) -> None:
        """Send a message to a chat_id, splitting at Telegram's 4096-char limit."""
        while text:
            chunk = text[:TELEGRAM_MAX_MSG_LEN]
            text = text[TELEGRAM_MAX_MSG_LEN:]
            await self._app.bot.send_message(chat_id=chat_id, text=chunk)

    # -- lifecycle -----------------------------------------------------------

    async def start(self, stop_event: asyncio.Event) -> None:
        """Start the Telegram bot with long-polling + message worker."""
        try:
            app = Application.builder().token(self._bot_token).build()
            self._app = app

            app.add_handler(CommandHandler("start", self._handle_start))
            app.add_handler(CommandHandler("status", self._handle_status))
            app.add_handler(CommandHandler("link", self._handle_link))
            app.add_handler(CommandHandler("ask", self._handle_ask))
            app.add_handler(
                MessageHandler(
                    (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, self._handle_message
                )
            )

            # initialize() calls get_me() internally and validates the token
            await app.initialize()

            # Guard: stop() may have been called concurrently during initialize()
            if self._app is None:
                logger.info("Telegram bridge stopped during initialization")
                return

            self._bot_username = app.bot.username
            bot_me = await app.bot.get_me()
            self._can_read_all_group_messages = bool(
                getattr(bot_me, "can_read_all_group_messages", False)
            )
            self._running = True

            logger.info(
                "Telegram bridge started as @%s (can_read_all_group_messages=%s)",
                self._bot_username,
                self._can_read_all_group_messages,
            )

            self._worker_task = asyncio.create_task(self._message_worker())

            await app.updater.start_polling(drop_pending_updates=True)
            await app.start()

            # Wait until stop is signalled
            await stop_event.wait()

        except Exception:
            logger.exception("Telegram bridge failed to start")
        finally:
            await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        if self._app is not None:
            try:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                if self._app.running:
                    await self._app.stop()
                await self._app.shutdown()
            except Exception:
                logger.exception("Error stopping Telegram bridge")
            self._app = None
        self._can_read_all_group_messages = None

        logger.info("Telegram bridge stopped")

    # -- handlers ------------------------------------------------------------

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            return
        logger.info("Telegram /start: chat_id=%s type=%s", chat.id, chat.type)

        chat_info = {
            "chat_id": chat.id,
            "chat_type": chat.type,
            "title": chat.title or chat.full_name or str(chat.id),
            "connected_at": datetime.now(UTC).isoformat(),
        }
        if user is not None:
            chat_info["user_id"] = user.id
            chat_info["user_name"] = user.full_name or user.first_name or "Unknown"
            if user.username:
                chat_info["username"] = user.username
        self._connected_chats[chat.id] = chat_info

        group_hint = ""
        if chat.type in ("group", "supergroup"):
            group_hint = (
                "\n\nGroup note:\n"
                "- If Telegram privacy mode is enabled for this bot, use /ask <message>\n"
                "- Or mention the bot in your message"
            )
            if self._can_read_all_group_messages is False:
                group_hint += (
                    "\n- Full group capture is currently OFF in Telegram settings "
                    "(BotFather /setprivacy -> Disable)"
                )

        await update.message.reply_text(
            f"Connected to Sentinel agent.\n"
            f"Chat registered as {chat.type}.\n"
            f"Send any message to interact with the agent."
            f"{group_hint}"
        )

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        agent_available = self._agent_loop is not None
        status = "online" if agent_available else "no provider configured"
        group_mode = (
            "all-group-messages"
            if self._can_read_all_group_messages
            else "privacy-mode (commands/mentions/replies only)"
        )
        await update.message.reply_text(f"Sentinel status: {status}\nGroup mode: {group_mode}")

    async def _handle_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return
        if chat.type != "private":
            await update.message.reply_text(
                "Owner linking must be done in a private DM with the bot."
            )
            return
        if settings.telegram_owner_user_id is None:
            await update.message.reply_text("Telegram owner is not configured yet in Sentinel.")
            return
        args = context.args or []
        code = args[0].strip() if args and isinstance(args[0], str) else ""
        if not code:
            await update.message.reply_text("Usage: /link <PAIRING_CODE>")
            return
        if not _pairing_not_expired(settings.telegram_pairing_code_expires_at):
            await update.message.reply_text(
                "No active pairing code or it has expired. Generate a new code from Sentinel."
            )
            return
        expected_hash = settings.telegram_pairing_code_hash or ""
        received_hash = _pairing_code_hash(code)
        if not expected_hash or not hmac.compare_digest(expected_hash, received_hash):
            await update.message.reply_text("Invalid pairing code.")
            return

        settings.telegram_owner_telegram_user_id = str(user.id)
        settings.telegram_owner_chat_id = str(chat.id)
        await _upsert_setting(
            "telegram_owner_telegram_user_id", settings.telegram_owner_telegram_user_id
        )
        await _upsert_setting("telegram_owner_chat_id", settings.telegram_owner_chat_id)
        await clear_owner_pairing_code()
        await update.message.reply_text(
            "Owner link successful. This DM is now treated as the trusted owner channel."
        )

    async def _handle_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        args = context.args or []
        text = " ".join(arg for arg in args if isinstance(arg, str)).strip()
        if not text:
            await update.message.reply_text("Usage: /ask <message>")
            return
        chat = update.effective_chat
        if chat is not None:
            logger.info("Telegram /ask: chat_id=%s type=%s", chat.id, chat.type)
        await self._enqueue_message(update, text_override=text)

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._enqueue_message(update)

    def _resolve_incoming_text(self, update: Update, text_override: str | None = None) -> str:
        if text_override is not None:
            return text_override.strip()
        if not update.message:
            return ""
        return (update.message.text or update.message.caption or "").strip()

    async def _enqueue_message(self, update: Update, *, text_override: str | None = None) -> None:
        if not update.message:
            return
        incoming_text = self._resolve_incoming_text(update, text_override)
        if not incoming_text:
            return

        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            return

        # Register chat if not already registered
        if chat.id not in self._connected_chats:
            chat_info = {
                "chat_id": chat.id,
                "chat_type": chat.type,
                "title": chat.title
                or (chat.full_name if hasattr(chat, "full_name") else None)
                or str(chat.id),
                "connected_at": datetime.now(UTC).isoformat(),
            }
            if user is not None:
                chat_info["user_id"] = user.id
                chat_info["user_name"] = user.full_name or user.first_name or "Unknown"
                if user.username:
                    chat_info["username"] = user.username
            self._connected_chats[chat.id] = chat_info
        is_owner = self._is_owner_sender(chat, user)

        metadata = {
            "source": "telegram",
            "telegram_chat_id": chat.id,
            "telegram_chat_type": chat.type,
            "telegram_is_owner": is_owner,
        }
        if chat.title:
            metadata["telegram_chat_title"] = chat.title
        if user:
            metadata["telegram_user_name"] = user.full_name or user.first_name or "Unknown"
            metadata["telegram_user_id"] = user.id
            if user.username:
                metadata["telegram_username"] = user.username
        if text_override:
            metadata["telegram_text_override"] = incoming_text

        logger.info(
            "Telegram inbound: chat_id=%s type=%s user_id=%s owner=%s",
            chat.id,
            chat.type,
            metadata.get("telegram_user_id"),
            metadata.get("telegram_is_owner"),
        )
        await self._queue.put((update, metadata))

    # -- sequential message worker -------------------------------------------

    async def _message_worker(self) -> None:
        """Process queued Telegram messages one at a time."""
        while self._running:
            try:
                update, metadata = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            try:
                await self._process_message(update, metadata)
            except Exception:
                logger.exception("Error processing Telegram message")
                try:
                    await update.message.reply_text("An error occurred processing your message.")
                except Exception:
                    pass

    async def _process_message(self, update: Update, metadata: dict) -> None:
        if self._agent_loop is None:
            await update.message.reply_text(
                "No AI provider configured. Please set up a provider in Sentinel settings."
            )
            return

        text_override = metadata.get("telegram_text_override")
        text = self._resolve_incoming_text(
            update, str(text_override) if isinstance(text_override, str) else None
        )
        chat = update.effective_chat
        user = update.effective_user

        # Keep persisted user text clean; transport/source details stay in metadata.
        content = text

        # Persist user message
        from sqlalchemy import select, func

        async with self._db_factory() as db:
            session_id, route_scope = await self._resolve_inbound_session(
                db,
                chat=chat,
                user=user,
                metadata=metadata,
            )
            if session_id is None:
                await update.message.reply_text("Could not resolve agent session.")
                return

            metadata["telegram_route_scope"] = route_scope
            if route_scope in {"group_channel", "dm_channel"}:
                metadata["telegram_untrusted_channel"] = True
            if route_scope == "dm_channel":
                metadata["telegram_guardrail_level"] = "untrusted_private"
            elif route_scope == "group_channel":
                metadata["telegram_guardrail_level"] = "untrusted_group"

        session_key = str(session_id)

        # Check if agent is already running on this session
        busy = await self._run_registry.is_running(session_key)
        if busy:
            await update.message.reply_text(
                "The agent is currently processing another request. Please wait..."
            )
            # Retry with back-off
            for attempt in range(12):
                await asyncio.sleep(5)
                if not await self._run_registry.is_running(session_key):
                    break
            else:
                await update.message.reply_text(
                    "Agent is still busy after 60 seconds. Please try again later."
                )
                return

        async with self._db_factory() as db:

            count_result = await db.execute(
                select(func.count())
                .select_from(MessageModel)
                .where(MessageModel.session_id == session_id)
            )
            is_first_message = count_result.scalar_one() == 0

            message = MessageModel(
                session_id=session_id,
                role="user",
                content=content,
                metadata_json=metadata,
            )
            db.add(message)
            await db.commit()
            await db.refresh(message)

            # Broadcast to web clients
            await self._ws_manager.broadcast_message_ack(
                session_key,
                str(message.id),
                message.content,
                message.created_at,
            )

            # Name session if first message
            if is_first_message and self._agent_loop is not None and route_scope == "owner_main":
                asyncio.create_task(
                    self._name_session(session_id, text, self._ws_manager, self._agent_loop)
                )

            # Broadcast thinking state to web
            await self._ws_manager.broadcast_agent_thinking(session_key)

            # Run agent
            from app.services.llm.types import AgentEvent

            inline_reply_mode = route_scope == "owner_main"
            expected_chat_id = self._to_int(getattr(chat, "id", None))
            tool_delivery_success = False
            tool_delivery_chat_id: int | None = None

            async def _on_event(event: AgentEvent) -> None:
                nonlocal tool_delivery_success, tool_delivery_chat_id
                await self._ws_manager.broadcast_agent_event(session_key, event)
                if inline_reply_mode:
                    return
                if event.type != "tool_result" or event.tool_result is None:
                    return
                tool_result = event.tool_result
                if tool_result.tool_name != "send_telegram_message" or tool_result.is_error:
                    return
                payload = self._parse_json_dict(tool_result.content)
                if payload.get("success") is not True:
                    return
                outbound_chat_id = self._to_int(payload.get("chat_id"))
                if outbound_chat_id is None:
                    return
                if expected_chat_id is not None and outbound_chat_id != expected_chat_id:
                    return
                tool_delivery_success = True
                tool_delivery_chat_id = outbound_chat_id

            run_task = asyncio.create_task(
                self._agent_loop.run(
                    db,
                    session_id,
                    content,
                    persist_user_message=False,
                    on_event=_on_event,
                    model="hint:reasoning",
                    max_iterations=25,
                    allow_high_risk=True,
                )
            )

            registered = await self._run_registry.register(session_key, run_task)
            if not registered:
                run_task.cancel()
                await update.message.reply_text("Agent is already processing this session.")
                return

            try:
                result = await run_task
                final_text = result.final_text if result else ""

                if inline_reply_mode:
                    if final_text:
                        await self._send_chunked(update, final_text)
                    else:
                        await update.message.reply_text("(Agent produced no text response)")

                    # Send any collected image attachments (e.g. browser screenshots)
                    if result and result.attachments:
                        for att in result.attachments:
                            image_b64 = att.get("base64")
                            if image_b64:
                                await self._send_photo(chat.id, image_b64)
                else:
                    fallback_used = False
                    chat_id = getattr(chat, "id", None)
                    chat_type = str(getattr(chat, "type", "unknown"))
                    # Safety net: if model forgot to call send_telegram_message for this inbound
                    # chat, send the final text directly so Telegram still gets a response.
                    if (
                        not tool_delivery_success
                        and chat_id is not None
                        and isinstance(final_text, str)
                        and final_text.strip()
                    ):
                        await self._send_chunked_to_chat(int(chat_id), final_text.strip())
                        tool_delivery_success = True
                        tool_delivery_chat_id = int(chat_id)
                        fallback_used = True

                    await self._normalize_non_inline_assistant_output(
                        db,
                        session_id=session_id,
                        created_after=message.created_at or datetime.min.replace(tzinfo=UTC),
                        chat_id=tool_delivery_chat_id if tool_delivery_chat_id is not None else chat_id,
                        chat_type=chat_type,
                        delivered=tool_delivery_success,
                        fallback_used=fallback_used,
                    )
                    logger.info(
                        "Telegram non-inline processed: chat_id=%s type=%s delivered=%s fallback=%s",
                        getattr(chat, "id", None),
                        getattr(chat, "type", None),
                        tool_delivery_success,
                        fallback_used,
                    )

            except asyncio.CancelledError:
                await update.message.reply_text("Agent run was cancelled.")
            except Exception as exc:
                logger.exception("Agent run failed for Telegram message")
                await self._ws_manager.broadcast_agent_error(session_key, str(exc))
                await self._ws_manager.broadcast_done(session_key, "error")
                await update.message.reply_text("An error occurred while processing your request.")
            finally:
                await self._run_registry.clear(session_key, run_task)

                # Auto-compaction
                try:
                    from app.services.compaction import CompactionService

                    await CompactionService(
                        provider=self._agent_loop.provider
                    ).auto_compact_if_needed(db, session_id=session_id)
                except Exception:
                    pass

    # -- helpers -------------------------------------------------------------

    async def _resolve_default_session(self) -> UUID | None:
        """Backward-compatible helper for owner main route resolution."""
        async with self._db_factory() as db:
            return await self._resolve_owner_main_session_with_db(db)

    async def _send_chunked(self, update: Update, text: str) -> None:
        """Send a message, splitting at Telegram's 4096-char limit."""
        while text:
            chunk = text[:TELEGRAM_MAX_MSG_LEN]
            text = text[TELEGRAM_MAX_MSG_LEN:]
            try:
                await update.message.reply_text(chunk)
            except Exception:
                logger.exception("Failed to send Telegram chunk")

    async def _send_photo(
        self, chat_id: int, image_base64: str, caption: str | None = None
    ) -> None:
        """Send a base64-encoded image as a photo to a Telegram chat."""
        if self._app is None:
            return
        try:
            image_data = base64.b64decode(image_base64)
            bio = BytesIO(image_data)
            bio.name = "screenshot.png"
            await self._app.bot.send_photo(chat_id=chat_id, photo=bio, caption=caption)
        except Exception:
            logger.exception("Failed to send photo to chat %s", chat_id)

    async def _name_session(
        self,
        session_id: UUID,
        first_message: str,
        manager: object,
        agent_loop: object,
    ) -> None:
        """Generate a short session title from the first message."""
        from app.services.llm.types import TextContent, UserMessage

        prompt = (
            "Generate a very short title (3-6 words max) for a chat session that starts with "
            "this message. Reply with ONLY the title, no quotes, no punctuation at the end.\n\n"
            f"Message: {first_message[:300]}"
        )
        try:
            result = await agent_loop.provider.chat(
                [UserMessage(content=prompt)],
                model="hint:fast",
                tools=[],
                temperature=0.3,
            )
            title = ""
            for block in result.content:
                if isinstance(block, TextContent):
                    title += block.text
            title = title.strip()[:80]
            if not title:
                return

            async with self._db_factory() as db:
                from sqlalchemy import select

                db_result = await db.execute(
                    select(SessionModel).where(SessionModel.id == session_id)
                )
                session = db_result.scalars().first()
                if session is None:
                    return
                session.title = title
                await db.commit()

            await manager.broadcast(
                str(session_id),
                {
                    "type": "session_named",
                    "session_id": str(session_id),
                    "title": title,
                },
            )
        except Exception:
            logger.warning("Auto-naming failed for session %s", session_id, exc_info=True)


def send_telegram_message_tool(app_state_ref: object) -> "ToolDefinition":
    """Factory for the send_telegram_message tool. Uses lazy app_state reference."""
    from typing import Any

    from app.services.tools.registry import ToolDefinition

    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
        if bridge is None or not bridge.is_running:
            return {"success": False, "error": "Telegram bridge is not running"}

        chat_id = payload.get("chat_id")
        message = payload.get("message")
        allow_owner_chat = bool(payload.get("allow_owner_chat", False))
        owner_chat_id_raw = settings.telegram_owner_chat_id
        owner_chat_id: int | None = None
        if isinstance(owner_chat_id_raw, str) and owner_chat_id_raw.strip():
            try:
                owner_chat_id = int(owner_chat_id_raw.strip())
            except ValueError:
                owner_chat_id = None

        if not isinstance(message, str) or not message.strip():
            return {"success": False, "error": "message must be a non-empty string"}

        # If no chat_id, try to find a connected chat
        connected = bridge.connected_chats
        if chat_id is None:
            if len(connected) == 1:
                chat_id = next(iter(connected.keys()))
            elif len(connected) == 0:
                return {
                    "success": False,
                    "error": "No Telegram chats connected. A user must send /start to the bot first.",
                }
            else:
                chat_list = [
                    f"  - {info.get('title', 'Unknown')} (chat_id: {cid}, type: {info.get('chat_type', '?')})"
                    for cid, info in connected.items()
                ]
                return {
                    "success": False,
                    "error": "Multiple chats connected. Specify chat_id.\n" + "\n".join(chat_list),
                }

        if not isinstance(chat_id, int):
            try:
                chat_id = int(chat_id)
            except (ValueError, TypeError):
                return {"success": False, "error": f"Invalid chat_id: {chat_id}"}

        if owner_chat_id is not None and chat_id == owner_chat_id and not allow_owner_chat:
            return {
                "success": False,
                "error": (
                    "Refusing to send to owner Telegram DM by tool. "
                    "Owner messages should flow through the shared session/UI bridge."
                ),
            }

        ok = await bridge.send_message(chat_id, message.strip())
        if ok:
            return {"success": True, "chat_id": chat_id, "message_sent": message.strip()[:200]}
        return {"success": False, "error": "Failed to send message"}

    return ToolDefinition(
        name="send_telegram_message",
        description=(
            "Send a message to a connected Telegram chat (group or DM). "
            "If only one chat is connected, chat_id can be omitted. "
            "Use this when asked to message someone on Telegram. "
            "By default this refuses owner DM chat to keep owner flow in shared session/UI."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["message"],
            "properties": {
                "chat_id": {
                    "type": "integer",
                    "description": "Telegram chat ID to send to. Optional if only one chat is connected.",
                },
                "message": {
                    "type": "string",
                    "description": "The text message to send.",
                },
                "allow_owner_chat": {
                    "type": "boolean",
                    "description": "Optional override to allow sending directly to owner DM chat.",
                },
            },
        },
        execute=_execute,
    )


def telegram_manage_integration_tool(app_state_ref: object) -> "ToolDefinition":
    """Tool for managing Telegram integration (configure/start/stop/status/disable)."""
    from typing import Any

    from app.services.tools.registry import ToolDefinition

    async def _status_payload() -> dict[str, Any]:
        bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
        connected = bridge.connected_chats if bridge else {}
        return {
            "running": bool(bridge and bridge.is_running),
            "bot_username": bridge.bot_username if bridge else None,
            "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
            "connected_chat_count": len(connected),
            "connected_chats": connected,
            "token_configured": bool(settings.telegram_bot_token),
            "masked_token": mask_telegram_token(settings.telegram_bot_token),
            "owner_user_id": settings.telegram_owner_user_id or settings.dev_user_id,
            "target_session_id": settings.telegram_target_session_id,
            "owner_chat_id": settings.telegram_owner_chat_id,
            "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
        }

    async def _resolve_owner(payload: dict[str, Any]) -> tuple[str | None, str | None]:
        owner_user_id = payload.get("owner_user_id")
        owner_session_id = payload.get("owner_session_id")
        if owner_user_id is not None:
            if not isinstance(owner_user_id, str) or not owner_user_id.strip():
                return (None, "owner_user_id must be a non-empty string")
            return (owner_user_id.strip(), None)
        if owner_session_id is not None:
            if not isinstance(owner_session_id, str) or not owner_session_id.strip():
                return (None, "owner_session_id must be a non-empty string")
            resolved = await resolve_owner_user_id_from_session(owner_session_id.strip())
            if not resolved:
                return (None, f"Could not resolve owner from session_id: {owner_session_id}")
            return (resolved, None)
        fallback = settings.telegram_owner_user_id or settings.dev_user_id
        return (fallback, None)

    async def _resolve_target_session(payload: dict[str, Any]) -> tuple[str | None, str | None]:
        target_session_id = payload.get("target_session_id")
        if target_session_id is None:
            owner_session_id = payload.get("owner_session_id")
            if isinstance(owner_session_id, str) and owner_session_id.strip():
                return (owner_session_id.strip(), None)
            return (settings.telegram_target_session_id, None)
        if not isinstance(target_session_id, str) or not target_session_id.strip():
            return (None, "target_session_id must be a non-empty string")
        try:
            UUID(target_session_id.strip())
        except ValueError:
            return (None, f"Invalid target_session_id: {target_session_id}")
        return (target_session_id.strip(), None)

    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        action_raw = payload.get("action", "status")
        action = str(action_raw).strip().lower()

        if action == "status":
            return {"success": True, "action": action, **(await _status_payload())}

        if action == "stop":
            await stop_telegram_bridge(app_state_ref)
            return {"success": True, "action": action, **(await _status_payload())}

        if action == "disable":
            await stop_telegram_bridge(app_state_ref)
            settings.telegram_bot_token = None
            settings.telegram_owner_user_id = None
            settings.telegram_target_session_id = None
            settings.telegram_owner_chat_id = None
            await clear_telegram_settings()
            return {"success": True, "action": action, **(await _status_payload())}

        if action == "configure":
            bot_token = payload.get("bot_token")
            if not isinstance(bot_token, str) or not bot_token.strip():
                return {"success": False, "error": "bot_token is required for action=configure"}
            owner_user_id, owner_error = await _resolve_owner(payload)
            if owner_error:
                return {"success": False, "error": owner_error}
            target_session_id, target_error = await _resolve_target_session(payload)
            if target_error:
                return {"success": False, "error": target_error}

            owner_changed = settings.telegram_owner_user_id != owner_user_id
            settings.telegram_bot_token = bot_token.strip()
            settings.telegram_owner_user_id = owner_user_id
            settings.telegram_target_session_id = target_session_id
            if owner_changed:
                settings.telegram_owner_chat_id = None
                settings.telegram_owner_telegram_user_id = None
                await _delete_setting("telegram_owner_chat_id")
                await _delete_setting("telegram_owner_telegram_user_id")
            await persist_telegram_settings(
                bot_token=settings.telegram_bot_token,
                owner_user_id=settings.telegram_owner_user_id or settings.dev_user_id,
                target_session_id=settings.telegram_target_session_id,
                owner_chat_id=settings.telegram_owner_chat_id,
                owner_telegram_user_id=settings.telegram_owner_telegram_user_id,
            )
            await start_telegram_bridge(app_state_ref)
            return {"success": True, "action": action, **(await _status_payload())}

        if action == "start":
            owner_user_id, owner_error = await _resolve_owner(payload)
            if owner_error:
                return {"success": False, "error": owner_error}
            target_session_id, target_error = await _resolve_target_session(payload)
            if target_error:
                return {"success": False, "error": target_error}
            if owner_user_id:
                owner_changed = settings.telegram_owner_user_id != owner_user_id
                settings.telegram_owner_user_id = owner_user_id
                if settings.telegram_bot_token:
                    await _upsert_setting("telegram_owner_user_id", owner_user_id)
                if owner_changed:
                    settings.telegram_owner_chat_id = None
                    settings.telegram_owner_telegram_user_id = None
                    await _delete_setting("telegram_owner_chat_id")
                    await _delete_setting("telegram_owner_telegram_user_id")
            if target_session_id:
                settings.telegram_target_session_id = target_session_id
                await _upsert_setting("telegram_target_session_id", target_session_id)
            if not settings.telegram_bot_token:
                return {"success": False, "error": "No Telegram bot token configured"}
            await start_telegram_bridge(app_state_ref)
            return {"success": True, "action": action, **(await _status_payload())}

        return {
            "success": False,
            "error": f"Unsupported action: {action}",
        }

    return ToolDefinition(
        name="telegram_manage_integration",
        description=(
            "Manage Telegram integration for Sentinel. "
            "Actions: status, configure, start, stop, disable. "
            "Use configure with bot_token to connect a bot and bind it to an owner user/session."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "configure", "start", "stop", "disable"],
                    "description": "Integration action to run.",
                },
                "bot_token": {
                    "type": "string",
                    "description": "Telegram bot token. Required for action=configure.",
                },
                "owner_user_id": {
                    "type": "string",
                    "description": "Optional Sentinel user_id to bind as Telegram owner.",
                },
                "owner_session_id": {
                    "type": "string",
                    "description": "Optional Sentinel session_id to resolve owner user automatically.",
                },
                "target_session_id": {
                    "type": "string",
                    "description": "Optional active root session_id where inbound Telegram messages should be routed.",
                },
            },
        },
        execute=_execute,
    )
