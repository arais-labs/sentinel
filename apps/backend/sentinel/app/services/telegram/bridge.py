from __future__ import annotations

from app.services.sessions.compaction import CompactionService

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.models import Message as MessageModel
from app.models import Session as SessionModel
from sentral import ConversationItem, GenerationConfig, RunTurnRequest, TextBlock
import app.services.agent_runtime_adapters.runtime as runtime_adapter_module
from sentral.llm.runtime_conversions import runtime_event_to_sentinel_event
from app.services.agent_runtime_adapters.conversions import db_messages_to_runtime_items
from sentral.llm.ids import TierName
from app.services.messages import (
    build_generation_metadata,
    telegram_ingress_metadata,
    with_generation_metadata,
)
from app.services.sessions import session_bindings
from app.services.sessions.session_naming import (
    SessionNamingService,
    apply_conversation_message_delta,
    conversation_delta_for_role,
)

from .delivery import RichReplyDraft, send_rich_message
from .shared import (
    _PersistedInboundMessage,
    _RouteContext,
    _RunRegistryProtocol,
    _RuntimeSupportProtocol,
    _ToolDeliveryState,
    _WSManagerProtocol,
)

logger = logging.getLogger(__name__)


class TelegramBridge:
    """Bridges Telegram chats to Sentinel with deterministic per-channel routing."""

    def __init__(
        self,
        *,
        bot_token: str,
        user_id: str,
        agent_runtime_support: _RuntimeSupportProtocol | None,
        run_registry: _RunRegistryProtocol,
        ws_manager: _WSManagerProtocol,
        db_factory: Any,
        instance_settings: Any,
    ) -> None:
        self._bot_token = bot_token
        self._user_id = user_id
        self._agent_runtime_support = agent_runtime_support
        self._run_registry = run_registry
        self._ws_manager = ws_manager
        self._db_factory = db_factory
        self._instance_settings = instance_settings

        self._app: Application | None = None
        self._reply_tasks: set[asyncio.Task] = set()
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

    def update_agent_runtime_support(self, agent_runtime_support: _RuntimeSupportProtocol) -> None:
        self._agent_runtime_support = agent_runtime_support

    def _owner_chat_id(self) -> int | None:
        raw = self._instance_settings.telegram_owner_chat_id
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return int(raw.strip())
        except ValueError:
            return None

    def _is_owner_sender(self, chat: object | None, user: object | None) -> bool:
        if chat is None or getattr(chat, "type", None) != "private":
            return False
        expected_user_id = self._instance_settings.telegram_owner_telegram_user_id
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

    async def _resolve_owner_active_session(self, db: object) -> UUID | None:
        """Resolve owner DM route session from the explicit owner_active binding."""
        session = await session_bindings.resolve_owner_active_session(
            db,
            user_id=self._user_id,
            agent_id="dev-agent",
        )
        return session.id if session is not None else None

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
            session_id = await self._resolve_owner_active_session(db)
            if session_id is None:
                return (None, "owner_dm_missing")
            return (session_id, "owner_dm")

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
            await self._send_rich_to_chat(chat_id, text)
            return True
        except Exception:
            logger.exception("Failed to send message to chat %s", chat_id)
            return False

    async def _send_rich_to_chat(self, chat_id: int, text: str) -> None:
        """Send a native rich reply using the shared delivery path."""
        await send_rich_message(self._app.bot, chat_id, text)

    # -- lifecycle -----------------------------------------------------------

    async def _register_bot_commands(self, app: Application) -> None:
        """Publish the slash-command menu. /session is scoped to the owner's chat only.

        Re-applied on every start(); since owner binding rebuilds the bridge, the owner-scoped
        menu (with /session) tracks the current owner chat automatically.
        """
        base = [
            BotCommand("start", "Connect this chat to Sentinel"),
            BotCommand("status", "Show Telegram bridge status"),
            BotCommand("ask", "Ask the agent (needed in privacy-mode groups)"),
        ]
        try:
            await app.bot.set_my_commands(base, scope=BotCommandScopeDefault())
            owner_chat_id = self._owner_chat_id()
            if owner_chat_id is not None:
                owner_commands = base + [
                    BotCommand("session", "Switch which session your DM routes to"),
                ]
                await app.bot.set_my_commands(
                    owner_commands, scope=BotCommandScopeChat(chat_id=owner_chat_id)
                )
        except Exception:
            logger.exception("Failed to set Telegram command menu")

    async def start(self, stop_event: asyncio.Event) -> None:
        """Start the Telegram bot with sequential inbound update dispatch."""
        try:
            app = Application.builder().token(self._bot_token).build()
            self._app = app

            app.add_handler(CommandHandler("start", self._handle_start))
            app.add_handler(CommandHandler("status", self._handle_status))
            app.add_handler(CommandHandler("ask", self._handle_ask))
            app.add_handler(CommandHandler("session", self._handle_session))
            app.add_handler(CallbackQueryHandler(self._handle_session_callback, pattern=r"^sess:"))
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

            await self._register_bot_commands(app)

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
        # Drain short ingress handlers before taking the owned-task snapshot.
        # Otherwise an in-flight handler could register a run during shutdown.
        if self._app is not None:
            try:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                if self._app.running:
                    await self._app.stop()
            except Exception:
                logger.exception("Error stopping Telegram updates")
        tasks = tuple(self._reply_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._app is not None:
            try:
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
        agent_available = self._agent_runtime_support is not None
        status = "online" if agent_available else "no provider configured"
        group_mode = (
            "all-group-messages"
            if self._can_read_all_group_messages
            else "privacy-mode (commands/mentions/replies only)"
        )
        await update.message.reply_text(f"Sentinel status: {status}\nGroup mode: {group_mode}")

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
        await self._dispatch_message(update, text_override=text)

    async def _handle_session(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List the owner's recent sessions as inline buttons to switch DM routing."""
        if not update.message:
            return
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or chat.type != "private" or not self._is_owner_sender(chat, user):
            await update.message.reply_text(
                "This command is only available to the owner in a private chat."
            )
            return

        async with self._db_factory() as db:
            sessions = await session_bindings.list_recent_owner_sessions(
                db,
                user_id=self._user_id,
                limit=30,
            )
            current = await session_bindings.get_active_binding_session(
                db,
                user_id=self._user_id,
                binding_type=session_bindings.OWNER_ACTIVE_BINDING_TYPE,
                binding_key=session_bindings.OWNER_ACTIVE_BINDING_KEY,
            )
            current_id = current.id if current is not None else None

        if not sessions:
            await update.message.reply_text("No sessions yet.")
            return

        rows: list[list[InlineKeyboardButton]] = []
        for session in sessions:
            prefix = "✅ " if str(session.id) == str(current_id) else ""
            label = f"{prefix}{(session.title or 'Untitled')[:40]}"
            rows.append([InlineKeyboardButton(label, callback_data=f"sess:{session.id}")])
        keyboard = InlineKeyboardMarkup(rows)
        await update.message.reply_text(
            "Pick the session your DM routes to:",
            reply_markup=keyboard,
        )

    async def _handle_session_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Switch the owner's active DM session from a /session inline button tap."""
        query = update.callback_query
        if query is None:
            return
        chat = query.message.chat if query.message else None
        user = query.from_user
        if (
            chat is None
            or getattr(chat, "type", None) != "private"
            or not self._is_owner_sender(chat, user)
        ):
            await query.answer("Not authorized", show_alert=True)
            return

        raw = query.data or ""
        try:
            parsed = UUID(raw.split("sess:", 1)[1])
        except (ValueError, IndexError):
            await query.answer("Invalid selection", show_alert=True)
            return

        async with self._db_factory() as db:
            try:
                session = await session_bindings.set_owner_active_session(
                    db,
                    user_id=self._user_id,
                    session_id=parsed,
                )
                await db.commit()
            except session_bindings.SessionBindingTargetInvalidError as exc:
                await query.answer(str(exc), show_alert=True)
                return

        await query.answer("Switched")
        try:
            await query.edit_message_text(f"Now routing your DM to: {session.title or 'Untitled'}")
        except Exception:
            logger.exception("Failed to confirm Telegram session switch")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._dispatch_message(update)

    def _resolve_incoming_text(self, update: Update, text_override: str | None = None) -> str:
        """Extract text payload from message body/caption with optional override."""
        if text_override is not None:
            return text_override.strip()
        if not update.message:
            return ""
        return (update.message.text or update.message.caption or "").strip()

    async def _dispatch_message(self, update: Update, *, text_override: str | None = None) -> None:
        """Dispatch in Telegram update order; only the agent turn runs in background."""
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
        try:
            await self._process_message(update, metadata)
        except Exception:
            logger.exception("Error accepting Telegram message")
            await update.message.reply_text("An error occurred processing your message.")

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
            metadata["telegram_route_error"] = route_scope
            return None
        self._apply_route_guardrails(metadata, route_scope)
        chat_id = self._to_int(getattr(chat, "id", None))
        chat_type = str(getattr(chat, "type", "unknown"))
        return _RouteContext(
            session_id=session_id,
            session_key=str(session_id),
            route_scope=route_scope,
            inline_reply_mode=(route_scope == "owner_dm"),
            chat_id=chat_id,
            chat_type=chat_type,
        )

    async def _persist_inbound_user_message(
        self,
        db: Any,
        *,
        route: _RouteContext,
        content: str,
        metadata: dict,
    ) -> _PersistedInboundMessage:
        """Persist inbound Telegram user message and detect first-message sessions."""

        count_result = await db.execute(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.session_id == route.session_id)
        )
        is_first_message = count_result.scalar_one() == 0
        session_result = await db.execute(
            select(SessionModel).where(SessionModel.id == route.session_id)
        )
        session = session_result.scalars().first()
        if session is not None:
            apply_conversation_message_delta(session, conversation_delta_for_role("user"))

        message = MessageModel(
            session_id=route.session_id,
            role="user",
            content=content,
            metadata_json=with_generation_metadata(
                metadata,
                generation=build_generation_metadata(
                    requested_tier=TierName.NORMAL,
                    resolved_model=None,
                    provider=None,
                    temperature=0.7,
                    max_iterations=25,
                ),
            ),
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
        streamed_message: Any | None = None,
    ) -> None:
        """Deliver owner DM response directly in Telegram chat."""
        if streamed_message is not None:
            await streamed_message.close()
        await self._reply_rich(update, final_text or "(Agent produced no text response)")
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
            await self._send_rich_to_chat(route.chat_id, final_text.strip())
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

            await CompactionService(
                provider=self._agent_runtime_support.provider
            ).auto_compact_if_needed(db, session_id=session_id)
        except Exception:
            return

    # -- serialized ingress and session-owned turns --------------------------

    async def _process_message(self, update: Update, metadata: dict) -> None:
        """Persist inbound message, run agent runtime support, and deliver Telegram/web outputs."""
        if self._agent_runtime_support is None:
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
                if metadata.get("telegram_route_error") == "owner_dm_missing":
                    await update.message.reply_text(
                        "Choose a session with /session before sending owner DM messages."
                    )
                else:
                    await update.message.reply_text("Could not resolve agent session.")
                return

        # Match the desktop steering contract: persistence and enqueue are
        # serialized against completion. Telegram handlers remain sequential,
        # so a later /session callback cannot redirect this accepted message.
        async with self._run_registry.idle_guard(route.session_key) as idle:
            async with self._db_factory() as db:
                persisted = await self._persist_inbound_user_message(
                    db, route=route, content=content, metadata=metadata
                )
                if not idle:
                    await self._enqueue_steering(db, route, persisted)
        await self._ws_manager.broadcast_message_ack(
            route.session_key,
            str(persisted.message.id),
            content,
            persisted.message.created_at,
            metadata=persisted.message.metadata_json,
        )
        if not idle:
            await self._run_registry.notify_idle_interjections(route.session_key)
            await update.message.reply_text("Direction added to the current run.")
            return

        task = await self._run_registry.start(
            route.session_key,
            self._run_reply(update, metadata, route, persisted, content),
        )
        if task is None:
            # Another transport can start the session between the guard and
            # registration. Preserve this exact message as steering, not a retry.
            async with self._run_registry.idle_guard(route.session_key):
                async with self._db_factory() as db:
                    message = await db.get(MessageModel, persisted.message.id)
                    persisted = _PersistedInboundMessage(
                        message=message, is_first_message=persisted.is_first_message
                    )
                    await self._enqueue_steering(db, route, persisted)
            await self._run_registry.notify_idle_interjections(route.session_key)
            return
        self._reply_tasks.add(task)
        task.add_done_callback(self._reply_finished)

    def _reply_finished(self, task: asyncio.Task) -> None:
        self._reply_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Telegram reply task failed", exc_info=task.exception())

    async def _enqueue_steering(
        self, db: Any, route: _RouteContext, persisted: _PersistedInboundMessage
    ) -> None:
        message = persisted.message
        message.metadata_json = {
            **(message.metadata_json or {}),
            "steering": "pending",
            "steering_id": str(message.id),
        }
        await db.commit()
        self._run_registry.enqueue_interjection(
            route.session_key, db_messages_to_runtime_items([message])[0]
        )

    async def _run_reply(self, update, metadata, route, persisted, content) -> None:
        try:
            await self._run_reply_body(update, metadata, route, persisted, content)
        finally:
            await self._run_registry.clear(route.session_key, asyncio.current_task())

    async def _run_reply_body(self, update, metadata, route, persisted, content) -> None:
        async with self._db_factory() as db:
            naming_service = SessionNamingService(
                provider=getattr(self._agent_runtime_support, "provider", None),
                ws_manager=self._ws_manager,
                db_factory=self._db_factory,
            )
            if persisted.is_first_message and route.inline_reply_mode:
                asyncio.create_task(
                    naming_service.maybe_auto_rename(
                        session_id=route.session_id,
                        force=True,
                        first_message=content,
                    )
                )

            await self._ws_manager.broadcast_agent_thinking(route.session_key)

            delivery_state = _ToolDeliveryState(expected_chat_id=route.chat_id)
            inline_stream_text = ""
            inline_stream_message = (
                RichReplyDraft(
                    self._app.bot,
                    route.chat_id,
                    thread_id=getattr(update.message, "message_thread_id", None),
                )
                if route.inline_reply_mode
                else None
            )

            async def _on_event(event: Any) -> None:
                nonlocal inline_stream_text
                sentinel_event = runtime_event_to_sentinel_event(event)
                await self._ws_manager.broadcast_agent_event(route.session_key, sentinel_event)
                if route.inline_reply_mode:
                    if sentinel_event.type == "text_delta":
                        delta = getattr(sentinel_event, "delta", None)
                        if isinstance(delta, str) and delta:
                            inline_stream_text += delta
                            inline_stream_message.update(inline_stream_text)
                    elif (
                        sentinel_event.type == "toolcall_start"
                        and sentinel_event.tool_call is not None
                    ):
                        inline_stream_message.update(
                            f"{inline_stream_text}\n\n*Working: {sentinel_event.tool_call.name}*"
                        )
                    elif (
                        sentinel_event.type == "tool_result"
                        and sentinel_event.tool_result is not None
                    ):
                        tool_result = sentinel_event.tool_result
                        if tool_result.tool_name != "telegram":
                            status = (
                                "Waiting for approval"
                                if event.approval_request is not None
                                else "Needs attention" if tool_result.is_error else "Completed"
                            )
                            inline_stream_message.update(
                                f"{inline_stream_text}\n\n*{status}: {tool_result.tool_name}*"
                            )
                    return
                if sentinel_event.type != "tool_result" or sentinel_event.tool_result is None:
                    return
                tool_result = sentinel_event.tool_result
                if tool_result.tool_name != "telegram" or tool_result.is_error:
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

            runtime = runtime_adapter_module.SentinelLoopRuntimeAdapter(
                loop=self._agent_runtime_support,
                db=db,
                session_id=route.session_id,
            )

            run = runtime.run_turn(
                RunTurnRequest(
                    conversation_id=route.session_key,
                    new_items=[
                        ConversationItem(
                            id="telegram-user-input",
                            role="user",
                            content=[TextBlock(text=content)],
                            metadata=dict(metadata),
                        )
                    ],
                    config=GenerationConfig(
                        model=TierName.NORMAL.value,
                        max_iterations=25,
                        stream=True,
                        provider_metadata={
                            "persist_user_message": False,
                        },
                    ),
                    interjection_source=lambda: self._run_registry.drain_interjections(
                        route.session_key
                    ),
                ),
                sink=_on_event,
            )

            run_completed_successfully = False
            try:
                result = await run
                final_text = ""
                if result and result.final_item is not None:
                    final_text = "\n".join(
                        block.text
                        for block in result.final_item.content
                        if isinstance(block, TextBlock) and block.text
                    ).strip()

                if route.inline_reply_mode:
                    await self._deliver_inline_owner_reply(
                        update,
                        chat_id=route.chat_id,
                        final_text=final_text,
                        attachments=[],
                        streamed_message=inline_stream_message,
                    )
                else:
                    await self._deliver_non_inline_reply(
                        db,
                        route=route,
                        persisted=persisted,
                        final_text=final_text,
                        delivery_state=delivery_state,
                    )
                run_completed_successfully = True

            except asyncio.CancelledError:
                await update.message.reply_text("Agent run was cancelled.")
                raise
            except Exception as exc:
                logger.exception("Agent run failed for Telegram message")
                await self._ws_manager.broadcast_agent_error(route.session_key, str(exc))
                await self._ws_manager.broadcast_done(route.session_key, "error")
                await update.message.reply_text("An error occurred while processing your request.")
            finally:
                if inline_stream_message is not None:
                    await inline_stream_message.close()
                await self._auto_compact_after_run(db, session_id=route.session_id)
                if run_completed_successfully:
                    await naming_service.maybe_auto_rename(session_id=route.session_id)

    # -- helpers -------------------------------------------------------------

    async def _reply_rich(self, update: Update, text: str) -> None:
        """Reply with the same renderer and transport as module sends."""
        await send_rich_message(
            self._app.bot,
            update.effective_chat.id,
            text,
            reply_to=update.message.message_id,
            thread_id=getattr(update.message, "message_thread_id", None),
        )

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


__all__ = ["TelegramBridge"]
