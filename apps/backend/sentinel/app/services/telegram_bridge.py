from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any, Awaitable, Protocol
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
from app.services import session_bindings
from app.services.llm.ids import TierName
from app.services.messages import telegram_ingress_metadata
from app.services.system_settings import delete_system_setting, upsert_system_setting
from app.services.tools.executor import ToolExecutionError, ToolValidationError

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MSG_LEN = 4096
TELEGRAM_OWNER_PAIRING_TTL_SECONDS = 600
TELEGRAM_BUSY_POLL_ATTEMPTS = 12
TELEGRAM_BUSY_POLL_INTERVAL_SECONDS = 5


class _RunRegistryProtocol(Protocol):
    async def register(self, session_id: str, task: asyncio.Task[object]) -> bool: ...

    async def clear(self, session_id: str, task: asyncio.Task[object] | None = None) -> None: ...

    async def is_running(self, session_id: str) -> bool: ...


class _WSManagerProtocol(Protocol):
    async def broadcast_message_ack(
        self,
        session_id: str,
        message_id: str,
        content: str,
        created_at: datetime | None,
        metadata: dict | None = None,
    ) -> None: ...

    async def broadcast_agent_thinking(self, session_id: str) -> None: ...

    async def broadcast_agent_event(self, session_id: str, event: "AgentEvent") -> None: ...

    async def broadcast_agent_error(self, session_id: str, message: str) -> None: ...

    async def broadcast_done(self, session_id: str, stop_reason: str) -> None: ...

    async def broadcast(self, session_id: str, data: dict) -> None: ...


class _AgentLoopProtocol(Protocol):
    provider: Any

    async def run(
        self,
        db: Any,
        session_id: UUID,
        user_message: str | list[Any],
        *,
        system_prompt: str | None = None,
        max_iterations: int = 50,
        temperature: float = 0.7,
        model: str = TierName.NORMAL.value,
        allow_high_risk: bool = False,
        persist_user_message: bool = True,
        stream: bool = True,
        timeout_seconds: float | None = None,
        on_event: Any = None,
        inject_queue: asyncio.Queue[str] | None = None,
        persist_incremental: bool = False,
        user_metadata: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass(slots=True)
class _RouteContext:
    """Resolved routing metadata for one inbound Telegram message."""

    session_id: UUID
    session_key: str
    route_scope: str
    inline_reply_mode: bool
    chat_id: int | None
    chat_type: str


@dataclass(slots=True)
class _PersistedInboundMessage:
    """Persisted Telegram user message details used in downstream delivery steps."""

    message: MessageModel
    is_first_message: bool


@dataclass(slots=True)
class _ToolDeliveryState:
    """Tracks whether outbound Telegram delivery occurred via tool or fallback path."""

    expected_chat_id: int | None
    delivered: bool = False
    delivered_chat_id: int | None = None
    fallback_used: bool = False


def mask_telegram_token(value: str | None) -> str | None:
    """Return a display-safe token preview for UI/status payloads."""
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return value[:4] + "..." + value[-4:]


async def _upsert_setting(key: str, value: str) -> None:
    """Insert or update a single system setting key/value."""
    async with AsyncSessionLocal() as db:
        await upsert_system_setting(db, key=key, value=value)


async def _delete_setting(key: str) -> None:
    """Delete a system setting key when present."""
    async with AsyncSessionLocal() as db:
        await delete_system_setting(db, key=key)


async def persist_telegram_settings(
    *,
    bot_token: str,
    owner_user_id: str,
    owner_chat_id: str | None = None,
    owner_telegram_user_id: str | None = None,
) -> None:
    """Persist integration settings to DB-backed system settings keys."""
    await _upsert_setting("telegram_bot_token", bot_token)
    await _upsert_setting("telegram_owner_user_id", owner_user_id)
    if owner_chat_id:
        await _upsert_setting("telegram_owner_chat_id", owner_chat_id)
    else:
        await _delete_setting("telegram_owner_chat_id")
    if owner_telegram_user_id:
        await _upsert_setting("telegram_owner_telegram_user_id", owner_telegram_user_id)
    else:
        await _delete_setting("telegram_owner_telegram_user_id")


async def clear_telegram_settings() -> None:
    """Remove all persisted Telegram integration settings and pairing state."""
    await _delete_setting("telegram_bot_token")
    await _delete_setting("telegram_owner_user_id")
    await _delete_setting("telegram_owner_chat_id")
    await _delete_setting("telegram_owner_telegram_user_id")
    await _delete_setting("telegram_pairing_code_hash")
    await _delete_setting("telegram_pairing_code_expires_at")


def _pairing_code_hash(raw_code: str) -> str:
    """Return SHA-256 digest for pairing code comparison."""
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    """UTC clock helper to keep datetime creation consistent."""
    return datetime.now(UTC)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse persisted ISO timestamp into timezone-aware UTC datetime."""
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
    """True when a pairing code expiry timestamp is still in the future."""
    expires_at = _parse_iso_datetime(expires_at_iso)
    return bool(expires_at and expires_at > _utcnow())


async def issue_owner_pairing_code() -> tuple[str, str]:
    """Create a short-lived owner pairing code for Telegram DM ownership linking."""
    code = secrets.token_hex(3).upper()
    expires_at = (_utcnow() + timedelta(seconds=TELEGRAM_OWNER_PAIRING_TTL_SECONDS)).isoformat()
    settings.telegram_pairing_code_hash = _pairing_code_hash(code)
    settings.telegram_pairing_code_expires_at = expires_at
    await _upsert_setting("telegram_pairing_code_hash", settings.telegram_pairing_code_hash)
    await _upsert_setting("telegram_pairing_code_expires_at", expires_at)
    return (code, expires_at)


async def clear_owner_pairing_code() -> None:
    """Clear in-memory and persisted owner pairing code state."""
    settings.telegram_pairing_code_hash = None
    settings.telegram_pairing_code_expires_at = None
    await _delete_setting("telegram_pairing_code_hash")
    await _delete_setting("telegram_pairing_code_expires_at")


async def stop_telegram_bridge(app_state: object) -> None:
    """Stop bridge worker/polling task and clear runtime app_state handles."""
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
    """Return newest active root session id for a user, if any."""
    try:
        async with AsyncSessionLocal() as db:
            session_id = await session_bindings.resolve_main_session_id(db, user_id=user_id)
            return str(session_id) if session_id is not None else None
    except Exception:
        return None


async def start_telegram_bridge(app_state: object) -> bool:
    """Start bridge when token exists and sync owner/target route defaults first."""
    token = settings.telegram_bot_token
    if not token:
        return False

    await stop_telegram_bridge(app_state)

    ws_manager = getattr(app_state, "ws_manager", None)
    run_registry = getattr(app_state, "agent_run_registry", None)
    agent_loop = getattr(app_state, "agent_loop", None)
    owner_user_id = settings.telegram_owner_user_id
    if not owner_user_id:
        owner_user_id = settings.dev_user_id

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
    """Resolve owner user_id from an explicit Sentinel session id."""
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
        agent_loop: _AgentLoopProtocol | None,
        run_registry: _RunRegistryProtocol,
        ws_manager: _WSManagerProtocol,
        db_factory: Any,
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

    def update_agent_loop(self, agent_loop: _AgentLoopProtocol) -> None:
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
        """Reduce non-inline assistant text to one audit line in routed channel sessions."""
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

    async def _resolve_owner_main_session_with_db(self, db: object) -> UUID | None:
        """Resolve owner DM route session from canonical binding table."""
        session = await session_bindings.resolve_or_create_main_session(
            db,
            user_id=self._user_id,
            agent_id="dev-agent",
        )
        await db.commit()
        await db.refresh(session)
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
        """Resolve existing routed session or create one deterministic channel session."""
        normalized_type = chat_type.lower()
        binding_type = (
            session_bindings.TELEGRAM_GROUP_BINDING_TYPE
            if normalized_type in {"group", "supergroup"}
            else session_bindings.TELEGRAM_DM_BINDING_TYPE
        )

        existing = await session_bindings.get_active_binding_session(
            db,
            user_id=self._user_id,
            binding_type=binding_type,
            binding_key=route_key,
        )
        metadata_payload = {
            "chat_id": chat_id,
            "chat_type": normalized_type,
            "chat_title": chat_title or "",
            "sender_user_id": sender_user_id,
            "sender_name": sender_name or "",
        }
        if existing is not None:
            await session_bindings.bind_session(
                db,
                user_id=self._user_id,
                binding_type=binding_type,
                binding_key=route_key,
                session_id=existing.id,
                metadata=metadata_payload,
            )
            await db.commit()
            return (existing.id, "existing")

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
        )
        db.add(session)
        await db.flush()
        await session_bindings.bind_session(
            db,
            user_id=self._user_id,
            binding_type=binding_type,
            binding_key=route_key,
            session_id=session.id,
            metadata={**metadata_payload, "guardrail_level": guardrail_level},
        )
        await db.commit()
        await db.refresh(session)
        return (session.id, "created")

    async def _resolve_inbound_session(
        self,
        db: object,
        *,
        chat: object | None,
        user: object | None,
        metadata: dict,
    ) -> tuple[UUID | None, str]:
        """Map inbound Telegram chat/user tuple to the correct Sentinel session."""
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
        """Extract text payload from message body/caption with optional override."""
        if text_override is not None:
            return text_override.strip()
        if not update.message:
            return ""
        return (update.message.text or update.message.caption or "").strip()

    async def _enqueue_message(self, update: Update, *, text_override: str | None = None) -> None:
        """Normalize inbound update and enqueue it for serialized processing."""
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

        metadata = telegram_ingress_metadata(
            chat_id=chat.id,
            chat_type=chat.type,
            is_owner=is_owner,
            chat_title=chat.title,
            user_name=(user.full_name or user.first_name or "Unknown") if user else None,
            user_id=user.id if user else None,
            username=user.username if user else None,
        )
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

    @staticmethod
    def _apply_route_guardrails(metadata: dict, route_scope: str) -> None:
        """Annotate metadata with guardrail flags derived from route scope."""
        metadata["telegram_route_scope"] = route_scope
        if route_scope in {"group_channel", "dm_channel"}:
            metadata["telegram_untrusted_channel"] = True
        if route_scope == "dm_channel":
            metadata["telegram_guardrail_level"] = "untrusted_private"
        elif route_scope == "group_channel":
            metadata["telegram_guardrail_level"] = "untrusted_group"

    async def _resolve_route_context(
        self,
        db: Any,
        *,
        chat: object | None,
        user: object | None,
        metadata: dict,
    ) -> _RouteContext | None:
        """Resolve inbound update to a target session and route mode."""
        session_id, route_scope = await self._resolve_inbound_session(
            db,
            chat=chat,
            user=user,
            metadata=metadata,
        )
        if session_id is None:
            return None
        self._apply_route_guardrails(metadata, route_scope)
        chat_id = self._to_int(getattr(chat, "id", None))
        chat_type = str(getattr(chat, "type", "unknown"))
        return _RouteContext(
            session_id=session_id,
            session_key=str(session_id),
            route_scope=route_scope,
            inline_reply_mode=(route_scope == "owner_main"),
            chat_id=chat_id,
            chat_type=chat_type,
        )

    async def _wait_for_session_ready(self, update: Update, *, session_key: str) -> bool:
        """Wait for active run on target session to finish before starting another."""
        busy = await self._run_registry.is_running(session_key)
        if not busy:
            return True
        await update.message.reply_text(
            "The agent is currently processing another request. Please wait..."
        )
        for _ in range(TELEGRAM_BUSY_POLL_ATTEMPTS):
            await asyncio.sleep(TELEGRAM_BUSY_POLL_INTERVAL_SECONDS)
            if not await self._run_registry.is_running(session_key):
                return True
        await update.message.reply_text(
            "Agent is still busy after 60 seconds. Please try again later."
        )
        return False

    async def _persist_inbound_user_message(
        self,
        db: Any,
        *,
        route: _RouteContext,
        content: str,
        metadata: dict,
    ) -> _PersistedInboundMessage:
        """Persist inbound Telegram user message and detect first-message sessions."""
        from sqlalchemy import func, select

        count_result = await db.execute(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.session_id == route.session_id)
        )
        is_first_message = count_result.scalar_one() == 0

        message = MessageModel(
            session_id=route.session_id,
            role="user",
            content=content,
            metadata_json=metadata,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return _PersistedInboundMessage(message=message, is_first_message=is_first_message)

    async def _deliver_inline_owner_reply(
        self,
        update: Update,
        *,
        chat_id: int | None,
        final_text: str,
        attachments: list[dict[str, Any]],
    ) -> None:
        """Deliver owner DM response directly in Telegram chat."""
        if final_text:
            await self._send_chunked(update, final_text)
        else:
            await update.message.reply_text("(Agent produced no text response)")
        if chat_id is None:
            return
        for att in attachments:
            image_b64 = att.get("base64")
            if image_b64:
                await self._send_photo(chat_id, image_b64)

    async def _deliver_non_inline_reply(
        self,
        db: Any,
        *,
        route: _RouteContext,
        persisted: _PersistedInboundMessage,
        final_text: str,
        delivery_state: _ToolDeliveryState,
    ) -> None:
        """Deliver routed-channel response via tool or fallback, then persist audit line."""
        if (
            not delivery_state.delivered
            and route.chat_id is not None
            and isinstance(final_text, str)
            and final_text.strip()
        ):
            await self._send_chunked_to_chat(route.chat_id, final_text.strip())
            delivery_state.delivered = True
            delivery_state.delivered_chat_id = route.chat_id
            delivery_state.fallback_used = True

        await self._normalize_non_inline_assistant_output(
            db,
            session_id=route.session_id,
            created_after=persisted.message.created_at or datetime.min.replace(tzinfo=UTC),
            chat_id=(
                delivery_state.delivered_chat_id
                if delivery_state.delivered_chat_id is not None
                else route.chat_id
            ),
            chat_type=route.chat_type,
            delivered=delivery_state.delivered,
            fallback_used=delivery_state.fallback_used,
        )
        logger.info(
            "Telegram non-inline processed: chat_id=%s type=%s delivered=%s fallback=%s",
            route.chat_id,
            route.chat_type,
            delivery_state.delivered,
            delivery_state.fallback_used,
        )

    async def _auto_compact_after_run(self, db: Any, *, session_id: UUID) -> None:
        """Run best-effort auto-compaction after each Telegram-triggered run."""
        try:
            from app.services.compaction import CompactionService

            await CompactionService(
                provider=self._agent_loop.provider
            ).auto_compact_if_needed(db, session_id=session_id)
        except Exception:
            return

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
        """Persist inbound message, run agent loop, and deliver Telegram/web outputs."""
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

        async with self._db_factory() as db:
            route = await self._resolve_route_context(
                db,
                chat=chat,
                user=user,
                metadata=metadata,
            )
            if route is None:
                await update.message.reply_text("Could not resolve agent session.")
                return

        if not await self._wait_for_session_ready(update, session_key=route.session_key):
            return

        async with self._db_factory() as db:
            persisted = await self._persist_inbound_user_message(
                db,
                route=route,
                content=content,
                metadata=metadata,
            )

            await self._ws_manager.broadcast_message_ack(
                route.session_key,
                str(persisted.message.id),
                persisted.message.content,
                persisted.message.created_at,
            )

            if persisted.is_first_message and route.inline_reply_mode:
                asyncio.create_task(
                    self._name_session(route.session_id, text, self._ws_manager, self._agent_loop)
                )

            await self._ws_manager.broadcast_agent_thinking(route.session_key)

            from app.services.llm.generic.types import AgentEvent

            delivery_state = _ToolDeliveryState(expected_chat_id=route.chat_id)

            async def _on_event(event: AgentEvent) -> None:
                await self._ws_manager.broadcast_agent_event(route.session_key, event)
                if route.inline_reply_mode:
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
                if (
                    delivery_state.expected_chat_id is not None
                    and outbound_chat_id != delivery_state.expected_chat_id
                ):
                    return
                delivery_state.delivered = True
                delivery_state.delivered_chat_id = outbound_chat_id

            run_task = asyncio.create_task(
                self._agent_loop.run(
                    db,
                    route.session_id,
                    content,
                    persist_user_message=False,
                    on_event=_on_event,
                    model=TierName.NORMAL.value,
                    max_iterations=25,
                    allow_high_risk=True,
                )
            )

            registered = await self._run_registry.register(route.session_key, run_task)
            if not registered:
                run_task.cancel()
                await update.message.reply_text("Agent is already processing this session.")
                return

            try:
                result = await run_task
                final_text = result.final_text if result else ""

                if route.inline_reply_mode:
                    await self._deliver_inline_owner_reply(
                        update,
                        chat_id=route.chat_id,
                        final_text=final_text,
                        attachments=result.attachments if result else [],
                    )
                else:
                    await self._deliver_non_inline_reply(
                        db,
                        route=route,
                        persisted=persisted,
                        final_text=final_text,
                        delivery_state=delivery_state,
                    )

            except asyncio.CancelledError:
                await update.message.reply_text("Agent run was cancelled.")
            except Exception as exc:
                logger.exception("Agent run failed for Telegram message")
                await self._ws_manager.broadcast_agent_error(route.session_key, str(exc))
                await self._ws_manager.broadcast_done(route.session_key, "error")
                await update.message.reply_text("An error occurred while processing your request.")
            finally:
                await self._run_registry.clear(route.session_key, run_task)
                await self._auto_compact_after_run(db, session_id=route.session_id)

    # -- helpers -------------------------------------------------------------

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
        manager: _WSManagerProtocol,
        agent_loop: _AgentLoopProtocol,
    ) -> None:
        """Generate a short session title from the first message."""
        from app.services.llm.generic.types import TextContent, UserMessage

        prompt = (
            "Generate a very short title (3-6 words max) for a chat session that starts with "
            "this message. Reply with ONLY the title, no quotes, no punctuation at the end.\n\n"
            f"Message: {first_message[:300]}"
        )
        try:
            result = await agent_loop.provider.chat(
                [UserMessage(content=prompt)],
                model=TierName.FAST.value,
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
            raise ToolExecutionError("Telegram bridge is not running")

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
            raise ToolValidationError("Field 'message' must be a non-empty string")

        # If no chat_id, try to find a connected chat
        connected = bridge.connected_chats
        if chat_id is None:
            if len(connected) == 1:
                chat_id = next(iter(connected.keys()))
            elif len(connected) == 0:
                raise ToolExecutionError(
                    "No Telegram chats connected. A user must send /start to the bot first."
                )
            else:
                chat_list = [
                    f"  - {info.get('title', 'Unknown')} (chat_id: {cid}, type: {info.get('chat_type', '?')})"
                    for cid, info in connected.items()
                ]
                raise ToolValidationError(
                    "Multiple chats connected. Specify chat_id.\n" + "\n".join(chat_list)
                )

        if not isinstance(chat_id, int):
            try:
                chat_id = int(chat_id)
            except (ValueError, TypeError):
                raise ToolValidationError(f"Invalid chat_id: {chat_id}") from None

        if owner_chat_id is not None and chat_id == owner_chat_id and not allow_owner_chat:
            raise ToolExecutionError(
                "Refusing to send to owner Telegram DM by tool. "
                "Owner messages should flow through the shared session/UI bridge."
            )

        ok = await bridge.send_message(chat_id, message.strip())
        if ok:
            return {"success": True, "chat_id": chat_id, "message_sent": message.strip()[:200]}
        raise ToolExecutionError("Failed to send message")

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
    """Tool for managing Telegram integration using UI-equivalent behavior."""
    from typing import Any

    from app.services.tools.registry import ToolDefinition

    async def _status_payload(*, status_user_id: str | None = None) -> dict[str, Any]:
        bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
        connected = bridge.connected_chats if bridge else {}
        owner_user_id = settings.telegram_owner_user_id or settings.dev_user_id
        effective_status_user_id = status_user_id or owner_user_id
        main_session_id = await resolve_latest_active_root_session_id_for_user(
            effective_status_user_id
        )
        return {
            "running": bool(bridge and bridge.is_running),
            "bot_username": bridge.bot_username if bridge else None,
            "can_read_all_group_messages": bridge.can_read_all_group_messages if bridge else None,
            "connected_chat_count": len(connected),
            "connected_chats": connected,
            "token_configured": bool(settings.telegram_bot_token),
            "masked_token": mask_telegram_token(settings.telegram_bot_token),
            "owner_user_id": owner_user_id,
            "main_session_id": main_session_id,
            "owner_chat_id": settings.telegram_owner_chat_id,
            "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
        }

    async def _ensure_owner_main_session(owner_user_id: str) -> str | None:
        async with AsyncSessionLocal() as db:
            session = await session_bindings.resolve_or_create_main_session(
                db,
                user_id=owner_user_id,
                agent_id=None,
            )
            await db.commit()
            await db.refresh(session)
            return str(session.id)

    async def _resolve_actor_user_id(
        payload: dict[str, Any], *, required: bool
    ) -> str | None:
        session_id = payload.get("session_id")
        if session_id is None:
            if required:
                raise ToolValidationError(
                    "Field 'session_id' is required for this action and must reference an active session"
                )
            return None
        if not isinstance(session_id, str) or not session_id.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        resolved = await resolve_owner_user_id_from_session(session_id.strip())
        if not resolved:
            raise ToolValidationError(f"session_id references unknown session: {session_id}")
        return resolved

    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        action_raw = payload.get("action", "status")
        action = str(action_raw).strip().lower()
        mutating_actions = {
            "configure",
            "start",
            "stop",
            "delete_config",
            "disable",
            "bind_owner",
            "clear_owner",
        }
        actor_user_id = await _resolve_actor_user_id(
            payload,
            required=action in mutating_actions,
        )

        if action == "status":
            return {
                "success": True,
                "action": action,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action == "stop":
            await stop_telegram_bridge(app_state_ref)
            return {
                "success": True,
                "action": action,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action == "configure":
            bot_token = payload.get("bot_token")
            if not isinstance(bot_token, str) or not bot_token.strip():
                raise ToolValidationError(
                    "Field 'bot_token' must be a non-empty string for action=configure"
                )
            if actor_user_id is None:
                raise ToolValidationError("Could not resolve actor from session_id")

            owner_changed = bool(
                settings.telegram_owner_user_id and settings.telegram_owner_user_id != actor_user_id
            )
            settings.telegram_bot_token = bot_token.strip()
            settings.telegram_owner_user_id = actor_user_id
            if owner_changed:
                settings.telegram_owner_chat_id = None
                settings.telegram_owner_telegram_user_id = None
            main_session_id = await _ensure_owner_main_session(actor_user_id)
            await persist_telegram_settings(
                bot_token=settings.telegram_bot_token,
                owner_user_id=settings.telegram_owner_user_id or settings.dev_user_id,
                owner_chat_id=settings.telegram_owner_chat_id,
                owner_telegram_user_id=settings.telegram_owner_telegram_user_id,
            )
            started = await start_telegram_bridge(app_state_ref)
            if not started:
                raise ToolExecutionError("Failed to start Telegram bridge")
            return {
                "success": True,
                "action": action,
                "main_session_id": main_session_id,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action == "start":
            if not settings.telegram_bot_token:
                raise ToolExecutionError("No Telegram bot token configured")
            if actor_user_id is None:
                raise ToolValidationError("Could not resolve actor from session_id")
            owner_changed = bool(
                settings.telegram_owner_user_id and settings.telegram_owner_user_id != actor_user_id
            )
            settings.telegram_owner_user_id = actor_user_id
            await _upsert_setting("telegram_owner_user_id", actor_user_id)
            if owner_changed:
                settings.telegram_owner_chat_id = None
                settings.telegram_owner_telegram_user_id = None
                await _delete_setting("telegram_owner_chat_id")
                await _delete_setting("telegram_owner_telegram_user_id")
            main_session_id = await _ensure_owner_main_session(actor_user_id)
            started = await start_telegram_bridge(app_state_ref)
            if not started:
                raise ToolExecutionError("Failed to start Telegram bridge")
            return {
                "success": True,
                "action": action,
                "main_session_id": main_session_id,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action in {"delete_config", "disable"}:
            await stop_telegram_bridge(app_state_ref)
            settings.telegram_bot_token = None
            settings.telegram_owner_user_id = None
            settings.telegram_owner_chat_id = None
            settings.telegram_owner_telegram_user_id = None
            await _delete_setting("telegram_bot_token")
            await _delete_setting("telegram_owner_user_id")
            await _delete_setting("telegram_owner_chat_id")
            await _delete_setting("telegram_owner_telegram_user_id")
            return {
                "success": True,
                "action": action,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action == "bind_owner":
            if actor_user_id is None:
                raise ToolValidationError("Could not resolve actor from session_id")
            chat_id = payload.get("chat_id")
            if not isinstance(chat_id, int) or isinstance(chat_id, bool):
                raise ToolValidationError("Field 'chat_id' must be an integer for action=bind_owner")

            bridge: TelegramBridge | None = getattr(app_state_ref, "telegram_bridge", None)
            connected = bridge.connected_chats if bridge else {}
            chat_info = connected.get(chat_id)
            if chat_info is None:
                raise ToolExecutionError("Chat not connected. Send /start from owner DM first.")
            if str(chat_info.get("chat_type", "")).lower() != "private":
                raise ToolValidationError("Owner binding requires a private DM chat")

            requested_tg_user_id = payload.get("telegram_user_id")
            if requested_tg_user_id is not None and (
                not isinstance(requested_tg_user_id, str) or not requested_tg_user_id.strip()
            ):
                raise ToolValidationError("Field 'telegram_user_id' must be a non-empty string")
            inferred_tg_user_id = chat_info.get("user_id")
            owner_tg_user_id = (
                requested_tg_user_id.strip()
                if isinstance(requested_tg_user_id, str)
                else (
                    str(inferred_tg_user_id)
                    if inferred_tg_user_id is not None
                    else None
                )
            )

            settings.telegram_owner_user_id = actor_user_id
            settings.telegram_owner_chat_id = str(chat_id)
            settings.telegram_owner_telegram_user_id = owner_tg_user_id
            await _upsert_setting("telegram_owner_user_id", actor_user_id)
            await _upsert_setting("telegram_owner_chat_id", settings.telegram_owner_chat_id)
            if owner_tg_user_id:
                await _upsert_setting("telegram_owner_telegram_user_id", owner_tg_user_id)
            else:
                await _delete_setting("telegram_owner_telegram_user_id")

            return {
                "success": True,
                "action": action,
                "owner_chat_id": settings.telegram_owner_chat_id,
                "owner_telegram_user_id": settings.telegram_owner_telegram_user_id,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        if action == "clear_owner":
            settings.telegram_owner_chat_id = None
            settings.telegram_owner_telegram_user_id = None
            await _delete_setting("telegram_owner_chat_id")
            await _delete_setting("telegram_owner_telegram_user_id")
            return {
                "success": True,
                "action": action,
                **(await _status_payload(status_user_id=actor_user_id)),
            }

        raise ToolValidationError(f"Unsupported action: {action}")

    return ToolDefinition(
        name="telegram_manage_integration",
        description=(
            "Manage Telegram integration for Sentinel. "
            "Actions: status, configure, start, stop, delete_config, bind_owner, clear_owner. "
            "Action disable remains as a backward-compatible alias of delete_config."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "configure",
                        "start",
                        "stop",
                        "delete_config",
                        "bind_owner",
                        "clear_owner",
                        "disable",
                    ],
                    "description": "Integration action to run.",
                },
                "bot_token": {
                    "type": "string",
                    "description": "Telegram bot token. Required for action=configure.",
                },
                "chat_id": {
                    "type": "integer",
                    "description": "Required for action=bind_owner. Must be a connected private Telegram chat_id.",
                },
                "telegram_user_id": {
                    "type": "string",
                    "description": "Optional Telegram user id override for action=bind_owner.",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Sentinel session_id used to resolve acting authenticated user. "
                        "Required for all mutating actions."
                    ),
                },
            },
        },
        execute=_execute,
    )
