from __future__ import annotations

import asyncio
import base64
import logging
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.models import Message as MessageModel, Session as SessionModel

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MSG_LEN = 4096


class TelegramBridge:
    """Bridges Telegram chats to the Sentinel agent's default session."""

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

    def update_agent_loop(self, agent_loop: object) -> None:
        self._agent_loop = agent_loop

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
            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
            )

            # initialize() calls get_me() internally and validates the token
            await app.initialize()

            # Guard: stop() may have been called concurrently during initialize()
            if self._app is None:
                logger.info("Telegram bridge stopped during initialization")
                return

            self._bot_username = app.bot.username
            self._running = True

            logger.info("Telegram bridge started as @%s", self._bot_username)

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

        logger.info("Telegram bridge stopped")

    # -- handlers ------------------------------------------------------------

    async def _handle_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat = update.effective_chat
        if chat is None:
            return

        self._connected_chats[chat.id] = {
            "chat_id": chat.id,
            "chat_type": chat.type,
            "title": chat.title or chat.full_name or str(chat.id),
            "connected_at": datetime.now(UTC).isoformat(),
        }

        await update.message.reply_text(
            f"Connected to Sentinel agent.\n"
            f"Chat registered as {chat.type}.\n"
            f"Send any message to interact with the agent."
        )

    async def _handle_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        agent_available = self._agent_loop is not None
        status = "online" if agent_available else "no provider configured"
        await update.message.reply_text(f"Sentinel status: {status}")

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not update.message or not update.message.text:
            return

        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            return

        # Register chat if not already registered
        if chat.id not in self._connected_chats:
            self._connected_chats[chat.id] = {
                "chat_id": chat.id,
                "chat_type": chat.type,
                "title": chat.title or (chat.full_name if hasattr(chat, 'full_name') else None) or str(chat.id),
                "connected_at": datetime.now(UTC).isoformat(),
            }

        metadata = {
            "source": "telegram",
            "telegram_chat_id": chat.id,
            "telegram_chat_type": chat.type,
        }
        if chat.title:
            metadata["telegram_chat_title"] = chat.title
        if user:
            metadata["telegram_user_name"] = user.full_name or user.first_name or "Unknown"
            if user.username:
                metadata["telegram_username"] = user.username

        await self._queue.put((update, metadata))

    # -- sequential message worker -------------------------------------------

    async def _message_worker(self) -> None:
        """Process queued Telegram messages one at a time."""
        while self._running:
            try:
                update, metadata = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return

            try:
                await self._process_message(update, metadata)
            except Exception:
                logger.exception("Error processing Telegram message")
                try:
                    await update.message.reply_text(
                        "An error occurred processing your message."
                    )
                except Exception:
                    pass

    async def _process_message(self, update: Update, metadata: dict) -> None:
        if self._agent_loop is None:
            await update.message.reply_text(
                "No AI provider configured. Please set up a provider in Sentinel settings."
            )
            return

        text = update.message.text.strip()
        chat = update.effective_chat
        user = update.effective_user

        # Build agent-visible content with source context
        user_name = (user.full_name or user.first_name or "Unknown") if user else "Unknown"
        if chat.type in ("group", "supergroup"):
            chat_title = chat.title or "Group"
            content = (
                f"(via Telegram group \"{chat_title}\", from {user_name}) "
                f"{text}"
            )
        else:
            content = (
                f"(via Telegram DM from {user_name}) "
                f"{text}"
            )

        # Resolve default session
        session_id = await self._resolve_default_session()
        if session_id is None:
            await update.message.reply_text("Could not resolve agent session.")
            return

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

        # Persist user message
        from sqlalchemy import select, func

        async with self._db_factory() as db:
            count_result = await db.execute(
                select(func.count()).select_from(MessageModel).where(
                    MessageModel.session_id == session_id
                )
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
            if is_first_message and self._agent_loop is not None:
                asyncio.create_task(
                    self._name_session(session_id, text, self._ws_manager, self._agent_loop)
                )

            # Broadcast thinking state to web
            await self._ws_manager.broadcast_agent_thinking(session_key)

            # Run agent
            from app.services.llm.types import AgentEvent

            async def _on_event(event: AgentEvent) -> None:
                await self._ws_manager.broadcast_agent_event(session_key, event)

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
                await update.message.reply_text(
                    "Agent is already processing this session."
                )
                return

            try:
                result = await run_task
                final_text = result.final_text if result else ""

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

            except asyncio.CancelledError:
                await update.message.reply_text("Agent run was cancelled.")
            except Exception as exc:
                logger.exception("Agent run failed for Telegram message")
                await self._ws_manager.broadcast_agent_error(session_key, str(exc))
                await self._ws_manager.broadcast_done(session_key, "error")
                await update.message.reply_text(
                    "An error occurred while processing your request."
                )
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
        """Find the oldest active root session for the configured user."""
        from sqlalchemy import select

        async with self._db_factory() as db:
            result = await db.execute(
                select(SessionModel).where(
                    SessionModel.user_id == self._user_id,
                    SessionModel.status == "active",
                    SessionModel.parent_session_id.is_(None),
                )
            )
            sessions = result.scalars().all()
            if not sessions:
                # Create one
                session = SessionModel(
                    user_id=self._user_id,
                    agent_id="dev-agent",
                    title="Main",
                    status="active",
                )
                db.add(session)
                await db.commit()
                await db.refresh(session)
                return session.id

            sessions.sort(
                key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC)
            )
            return sessions[0].id

    async def _send_chunked(self, update: Update, text: str) -> None:
        """Send a message, splitting at Telegram's 4096-char limit."""
        while text:
            chunk = text[:TELEGRAM_MAX_MSG_LEN]
            text = text[TELEGRAM_MAX_MSG_LEN:]
            try:
                await update.message.reply_text(chunk)
            except Exception:
                logger.exception("Failed to send Telegram chunk")

    async def _send_photo(self, chat_id: int, image_base64: str, caption: str | None = None) -> None:
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
            logger.warning(
                "Auto-naming failed for session %s", session_id, exc_info=True
            )


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

        if not isinstance(message, str) or not message.strip():
            return {"success": False, "error": "message must be a non-empty string"}

        # If no chat_id, try to find a connected chat
        connected = bridge.connected_chats
        if chat_id is None:
            if len(connected) == 1:
                chat_id = next(iter(connected.keys()))
            elif len(connected) == 0:
                return {"success": False, "error": "No Telegram chats connected. A user must send /start to the bot first."}
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

        ok = await bridge.send_message(chat_id, message.strip())
        if ok:
            return {"success": True, "chat_id": chat_id, "message_sent": message.strip()[:200]}
        return {"success": False, "error": "Failed to send message"}

    return ToolDefinition(
        name="send_telegram_message",
        description=(
            "Send a message to a connected Telegram chat (group or DM). "
            "If only one chat is connected, chat_id can be omitted. "
            "Use this when asked to message someone on Telegram."
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
            },
        },
        execute=_execute,
    )
