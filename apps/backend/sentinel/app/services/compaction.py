from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Message, Session, SessionSummary
from app.services.llm.base import LLMProvider
from app.services.llm.types import TextContent


@dataclass
class CompactionResult:
    session_id: UUID
    raw_token_count: int
    compressed_token_count: int
    summary_preview: str


class CompactionService:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    async def compact_session(
        self, db: AsyncSession, *, session_id: UUID, user_id: str
    ) -> CompactionResult:
        session = await self._get_owned_session(db, session_id=session_id, user_id=user_id)
        return await self._compact(db, session)

    async def auto_compact_if_needed(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        threshold_tokens: int | None = None,
    ) -> CompactionResult | None:
        messages = await self._session_messages(db, session_id=session_id)
        token_limit = (
            max(1, int(threshold_tokens))
            if threshold_tokens is not None
            else max(1, int(settings.compaction_auto_trigger_tokens))
        )
        estimated_tokens = self._estimate_messages_tokens(messages)
        if estimated_tokens <= token_limit:
            return None
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            return None
        return await self._compact(db, session)

    async def should_auto_compact(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        threshold_tokens: int | None = None,
    ) -> bool:
        messages = await self._session_messages(db, session_id=session_id)
        token_limit = (
            max(1, int(threshold_tokens))
            if threshold_tokens is not None
            else max(1, int(settings.compaction_auto_trigger_tokens))
        )
        estimated_tokens = self._estimate_messages_tokens(messages)
        return estimated_tokens > token_limit

    async def _compact(self, db: AsyncSession, session: Session) -> CompactionResult:
        messages = await self._session_messages(db, session_id=session.id)

        if len(messages) <= 10:
            return CompactionResult(
                session_id=session.id,
                raw_token_count=0,
                compressed_token_count=0,
                summary_preview="No compaction needed yet.",
            )

        older = messages[:-10]
        context_start = older[0].created_at or datetime.now(UTC)
        context_end = older[-1].created_at or datetime.now(UTC)

        if self._provider is not None:
            summary_payload = await self._llm_summary_payload(older)
            summary_text = str(
                summary_payload.get("context_summary") or summary_payload.get("summary_text") or ""
            ).strip()
            if not summary_text:
                summary_text = self._fallback_summary_text(older)
                summary_payload["context_summary"] = summary_text
        else:
            summary_text = self._fallback_summary_text(older)
            summary_payload = {"summary_text": summary_text}

        raw_token_count = self._estimate_messages_tokens(older)
        compressed_token_count = self._estimate_text_tokens(summary_text)

        result = await db.execute(
            select(SessionSummary).where(SessionSummary.session_id == session.id)
        )
        summary = result.scalars().first()
        payload = dict(summary_payload)
        payload["summary_text"] = summary_text
        payload["context_window_start"] = context_start.isoformat()
        payload["context_window_end"] = context_end.isoformat()
        payload["active_message_count"] = 10
        payload["compacted_message_count"] = len(older)
        if summary is None:
            summary = SessionSummary(
                session_id=session.id,
                summary=payload,
                raw_token_count=raw_token_count,
                compressed_token_count=compressed_token_count,
            )
            db.add(summary)
        else:
            summary.summary = payload
            summary.raw_token_count = raw_token_count
            summary.compressed_token_count = compressed_token_count

        # Delete the old messages — they are now represented by the summary.
        # The last 10 messages are kept as active context.
        for message in older:
            await db.delete(message)

        await db.commit()
        await db.refresh(summary)
        preview = summary_text[:200]
        return CompactionResult(
            session_id=session.id,
            raw_token_count=raw_token_count,
            compressed_token_count=compressed_token_count,
            summary_preview=preview,
        )

    async def _get_owned_session(
        self, db: AsyncSession, *, session_id: UUID, user_id: str
    ) -> Session:
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        session = result.scalars().first()
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return session

    async def _session_messages(self, db: AsyncSession, *, session_id: UUID) -> list[Message]:
        result = await db.execute(select(Message).where(Message.session_id == session_id))
        items = result.scalars().all()
        items.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))
        return items

    def _bullet_line(self, role: str, content: str) -> str:
        trimmed = content.replace("\n", " ").strip()
        snippet = trimmed[:80] + ("..." if len(trimmed) > 80 else "")
        return f"- [{role}] {snippet}"

    def _word_count(self, text: str) -> int:
        return len([part for part in text.split() if part])

    def _estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _estimate_message_tokens(self, message: Message) -> int:
        if isinstance(message.token_count, int) and message.token_count > 0:
            return message.token_count
        return self._estimate_text_tokens(message.content or "")

    def _estimate_messages_tokens(self, messages: list[Message]) -> int:
        return sum(self._estimate_message_tokens(message) for message in messages)

    def _fallback_summary_text(self, messages: list[Message]) -> str:
        bullet_lines = [self._bullet_line(message.role, message.content) for message in messages]
        return "\n".join(bullet_lines)

    async def _llm_summary_payload(self, messages: list[Message]) -> dict:
        prompt_lines = [self._bullet_line(message.role, message.content) for message in messages]
        prompt = (
            "Summarize the following conversation into strict JSON with keys: "
            "key_decisions (array of strings), tool_results (array of strings), "
            "open_tasks (array of strings), context_summary (string).\n\n"
            "Conversation:\n" + "\n".join(prompt_lines)
        )

        response = await self._provider.chat(
            [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            model="hint:fast",
            temperature=0.3,
        )

        text_parts = [
            block.text
            for block in response.content
            if isinstance(block, TextContent) and block.text
        ]
        raw_text = "\n".join(text_parts).strip()
        parsed = self._parse_summary_json(raw_text)
        return {
            "key_decisions": (
                parsed.get("key_decisions") if isinstance(parsed.get("key_decisions"), list) else []
            ),
            "tool_results": (
                parsed.get("tool_results") if isinstance(parsed.get("tool_results"), list) else []
            ),
            "open_tasks": (
                parsed.get("open_tasks") if isinstance(parsed.get("open_tasks"), list) else []
            ),
            "context_summary": str(parsed.get("context_summary") or ""),
        }

    def _parse_summary_json(self, raw_text: str) -> dict:
        if not raw_text:
            return {}
        try:
            parsed = json.loads(raw_text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    sliced = json.loads(raw_text[start : end + 1])
                    return sliced if isinstance(sliced, dict) else {}
                except json.JSONDecodeError:
                    return {}
        return {}
