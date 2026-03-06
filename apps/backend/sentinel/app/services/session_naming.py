from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Message, Session
from app.services import session_bindings
from app.services.llm.generic.types import TextContent, UserMessage
from app.services.llm.ids import TierName
from app.services.ws_manager import ConnectionManager

logger = logging.getLogger(__name__)

CONVERSATION_ROLES = {"user", "assistant"}
_SESSION_RENAME_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def conversation_delta_for_role(role: str | None) -> int:
    normalized = (role or "").strip().lower()
    return 1 if normalized in CONVERSATION_ROLES else 0


def apply_conversation_message_delta(session: Session, delta: int) -> int:
    current = max(0, int(session.conversation_message_count or 0))
    if delta > 0:
        current += int(delta)
        session.conversation_message_count = current
    return current


class SessionNamingService:
    def __init__(
        self,
        *,
        provider: Any | None,
        ws_manager: ConnectionManager | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._provider = provider
        self._ws_manager = ws_manager
        self._db_factory = db_factory or AsyncSessionLocal

    async def maybe_auto_rename(
        self,
        *,
        session_id: UUID,
        force: bool = False,
        first_message: str | None = None,
    ) -> str | None:
        if self._provider is None:
            return None
        if not force and not settings.session_auto_rename_enabled:
            return None

        lock = _SESSION_RENAME_LOCKS[str(session_id)]
        async with lock:
            async with self._db_factory() as db:
                title = await self._maybe_auto_rename_in_db(
                    db,
                    session_id=session_id,
                    force=force,
                    first_message=first_message,
                )

        if title and self._ws_manager is not None:
            await self._ws_manager.broadcast(
                str(session_id),
                {"type": "session_named", "session_id": str(session_id), "title": title},
            )
        return title

    async def _maybe_auto_rename_in_db(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        force: bool,
        first_message: str | None,
    ) -> str | None:
        session = await db.get(Session, session_id)
        if session is None:
            return None
        if session.parent_session_id is not None:
            return None
        if await self._is_telegram_bound(db, session):
            return None

        interval = max(1, int(settings.session_auto_rename_every_messages))
        current_count = await self._effective_conversation_count(db, session)
        last_checkpoint = max(0, int(session.last_auto_rename_count or 0))
        checkpoint = ((current_count // interval) * interval) if current_count >= interval else 0

        should_rename = force
        if not should_rename and checkpoint > last_checkpoint:
            should_rename = True
        if not should_rename:
            if session.conversation_message_count != current_count:
                session.conversation_message_count = current_count
                await db.commit()
            return None

        transcript = await self._recent_transcript(
            db, session_id=session.id, max_messages=max(6, int(settings.session_auto_rename_context_messages))
        )
        seed = (first_message or "").strip()
        if not transcript and seed:
            transcript = f"user: {self._clip(seed, 280)}"
        if not transcript:
            return None

        title = await self._generate_title(transcript)
        if title is None:
            if session.conversation_message_count != current_count:
                session.conversation_message_count = current_count
                await db.commit()
            return None

        checkpoint_updated = False
        if checkpoint > last_checkpoint:
            session.last_auto_rename_count = checkpoint
            checkpoint_updated = True
        if session.conversation_message_count != current_count:
            session.conversation_message_count = current_count

        current_title = (session.title or "").strip()
        if current_title == title:
            if checkpoint_updated:
                await db.commit()
            return None

        session.title = title
        await db.commit()
        return title

    async def _effective_conversation_count(
        self, db: AsyncSession, session: Session
    ) -> int:
        current = max(0, int(session.conversation_message_count or 0))
        if current > 0:
            return current
        result = await db.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.session_id == session.id,
                Message.role.in_(list(CONVERSATION_ROLES)),
            )
        )
        counted = int(result.scalar_one() or 0)
        return max(current, counted)

    async def _recent_transcript(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        max_messages: int,
    ) -> str:
        result = await db.execute(
            select(Message)
            .where(
                Message.session_id == session_id,
                Message.role.in_(list(CONVERSATION_ROLES)),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(max_messages)
        )
        rows = list(reversed(result.scalars().all()))
        lines: list[str] = []
        for message in rows:
            role = (message.role or "").strip().lower()
            if role not in CONVERSATION_ROLES:
                continue
            content = self._clip((message.content or "").strip(), 280)
            if not content:
                continue
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    async def _generate_title(self, transcript: str) -> str | None:
        model = self._auto_rename_model()
        prompt = (
            "Generate a concise chat session title that reflects the current work.\n"
            "Rules:\n"
            "- 3 to 8 words\n"
            "- Plain text only\n"
            "- No quotes and no ending punctuation\n"
            "- Maximum 80 characters\n\n"
            "Conversation excerpt:\n"
            f"{transcript[:5000]}"
        )
        try:
            result = await self._provider.chat(
                [UserMessage(content=prompt)],
                model=model,
                tools=[],
                temperature=0.2,
            )
            raw = ""
            for block in result.content:
                if isinstance(block, TextContent):
                    raw += block.text
        except Exception:
            logger.warning("Session auto-rename model call failed", exc_info=True)
            return None
        return self._sanitize_title(raw)

    async def _is_telegram_bound(self, db: AsyncSession, session: Session) -> bool:
        return await session_bindings.is_session_bound(
            db,
            user_id=session.user_id,
            session_id=session.id,
            binding_types={
                session_bindings.TELEGRAM_GROUP_BINDING_TYPE,
                session_bindings.TELEGRAM_DM_BINDING_TYPE,
            },
            active_only=True,
        )

    @staticmethod
    def _auto_rename_model() -> str:
        configured = (settings.session_auto_rename_model_tier or "").strip().lower()
        try:
            tier = TierName(configured)
        except Exception:
            tier = TierName.FAST
        return tier.value

    @staticmethod
    def _clip(value: str, max_chars: int) -> str:
        compact = re.sub(r"\s+", " ", value).strip()
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 1].rstrip() + "…"

    @staticmethod
    def _sanitize_title(raw: str) -> str | None:
        cleaned = (raw or "").strip()
        if not cleaned:
            return None
        cleaned = cleaned.replace("\n", " ").replace("\r", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = cleaned.strip("`\"' ")
        cleaned = re.sub(r"[.!?;,:]+$", "", cleaned).strip()
        if not cleaned:
            return None
        return cleaned[:80]
