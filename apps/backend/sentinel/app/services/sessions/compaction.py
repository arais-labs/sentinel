from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, Session, SessionSummary
from app.services.sessions.history import context_history
from app.services.sessions.context_usage import (
    latest_request_input_tokens,
    normalize_context_budget,
)
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import TextContent
from sentral.llm.ids import TierName

ACTIVE_CONTEXT_MESSAGE_COUNT = 10


@dataclass
class CompactionResult:
    """Outcome metadata for a compaction run."""

    session_id: UUID
    compacted: bool
    summary_preview: str


class CompactionService:
    """Condense older session history into a persisted summary payload."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    async def compact_session(
        self, db: AsyncSession, *, session_id: UUID, user_id: str
    ) -> CompactionResult:
        """Compact one session immediately."""
        session = await self._get_session_record(db, session_id=session_id, user_id=user_id)
        return await self._compact(db, session)

    async def auto_compact_if_needed(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        threshold_tokens: int | None = None,
    ) -> CompactionResult | None:
        """Compact only when reported request usage exceeds the threshold."""
        summary, messages = await context_history(db, session_id)
        token_limit = normalize_context_budget(
            int(threshold_tokens) if threshold_tokens is not None else self._model_budget(messages)
        )
        tokens = self._reported_context_tokens(summary, messages)
        if tokens is None or tokens <= token_limit:
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
        """Return whether the session currently exceeds compaction token threshold."""
        summary, messages = await context_history(db, session_id)
        token_limit = normalize_context_budget(
            int(threshold_tokens) if threshold_tokens is not None else self._model_budget(messages)
        )
        tokens = self._reported_context_tokens(summary, messages)
        return tokens is not None and tokens > token_limit

    def _reported_context_tokens(self, summary, messages):
        if summary is not None:
            # A pre-compaction request describes the old context, not the new one.
            raw_boundary = (summary.summary or {}).get("compacted_at")
            boundary = datetime.fromisoformat(raw_boundary) if raw_boundary else summary.created_at
            if boundary is not None:
                messages = [m for m in messages if m.created_at and m.created_at > boundary]
        return latest_request_input_tokens(messages)

    def _model_budget(self, messages):
        if self._provider is None:
            return None
        for message in reversed(messages):
            snapshot = (message.metadata_json or {}).get("provider_usage") or {}
            model = snapshot.get("model")
            if model:
                return self._provider.model_context(model)["context_token_budget"]
        return self._provider.model_context("normal")["context_token_budget"]

    async def _compact(self, db: AsyncSession, session: Session) -> CompactionResult:
        """Atomically advance the model context boundary while retaining every message."""
        summary, messages = await context_history(db, session.id)
        messages = [m for m in messages if (m.metadata_json or {}).get("steering") != "pending"]
        active_context = self._select_active_context_messages(messages)
        if len(messages) <= len(active_context):
            return CompactionResult(
                session_id=session.id,
                compacted=False,
                summary_preview="No compaction needed yet.",
            )

        active_ids = {item.id for item in active_context}
        older = [item for item in messages if item.id not in active_ids]
        if not older:
            return CompactionResult(
                session_id=session.id,
                compacted=False,
                summary_preview="No compaction needed yet.",
            )
        context_start = older[0].created_at or datetime.now(UTC)
        context_end = older[-1].created_at or datetime.now(UTC)

        previous = str((summary.summary or {}).get("summary_text") or "") if summary else ""
        to_summarize = list(older)
        if previous:
            to_summarize.insert(
                0,
                Message(role="system", content="Previous summary:\n" + previous, metadata_json={}),
            )
        if self._provider is not None:
            summary_payload = await self._llm_summary_payload(to_summarize)
            summary_text = str(
                summary_payload.get("context_summary") or summary_payload.get("summary_text") or ""
            ).strip()
            if not summary_text:
                raise ValueError(
                    "The model returned an empty summary; history and context were not changed."
                )
        else:
            summary_text = self._fallback_summary_text(to_summarize)
            summary_payload = {"summary_text": summary_text}

        payload = dict(summary_payload)
        measured = payload.pop("provider_usage", None)
        if self._provider is not None:
            db.add(
                Message(
                    session_id=session.id,
                    role="system",
                    content="Context compacted",
                    metadata_json={
                        "source": "usage",
                        "purpose": "compaction",
                        "provider_usage": measured,
                    },
                )
            )
        payload["compacted_at"] = datetime.now(UTC).isoformat()
        payload["through_message_id"] = str(older[-1].id)
        payload["summary_text"] = summary_text
        payload["context_window_start"] = context_start.isoformat()
        payload["context_window_end"] = context_end.isoformat()
        payload["active_message_count"] = len(active_context)
        payload["compacted_message_count"] = len(older)
        if summary is None:
            summary = SessionSummary(
                session_id=session.id,
                summary=payload,
            )
            db.add(summary)
        else:
            summary.summary = payload

        await db.commit()
        await db.refresh(summary)
        preview = summary_text[:200]
        return CompactionResult(
            session_id=session.id,
            compacted=True,
            summary_preview=preview,
        )

    def _select_active_context_messages(self, messages: list[Message]) -> list[Message]:
        """Keep a coherent recent tail (turn-aware), not arbitrary trailing rows."""
        if not messages:
            return []
        if len(messages) <= ACTIVE_CONTEXT_MESSAGE_COUNT:
            return list(messages)

        turn_buckets: list[tuple[bool, list[Message]]] = []
        current: list[Message] = []
        current_has_user = False
        for message in messages:
            if message.role == "user":
                if current:
                    turn_buckets.append((current_has_user, current))
                current = [message]
                current_has_user = True
                continue
            if not current:
                current = [message]
                current_has_user = False
                continue
            current.append(message)
        if current:
            turn_buckets.append((current_has_user, current))

        has_user_turn = any(has_user for has_user, _ in turn_buckets)
        selected_reversed: list[list[Message]] = []
        selected_count = 0
        selected_user_turns = 0
        for has_user, bucket in reversed(turn_buckets):
            selected_reversed.append(bucket)
            selected_count += len(bucket)
            if has_user:
                selected_user_turns += 1
            enough_rows = selected_count >= ACTIVE_CONTEXT_MESSAGE_COUNT
            enough_users = selected_user_turns >= 1 if has_user_turn else True
            if enough_rows and enough_users:
                break

        selected_reversed.reverse()
        retained = [item for bucket in selected_reversed for item in bucket]
        if not retained:
            return messages[-ACTIVE_CONTEXT_MESSAGE_COUNT:]
        return retained

    async def _get_session_record(
        self, db: AsyncSession, *, session_id: UUID, user_id: str
    ) -> Session:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return session

    def _bullet_line(self, role: str, content: str) -> str:
        trimmed = content.replace("\n", " ").strip()
        snippet = trimmed[:80] + ("..." if len(trimmed) > 80 else "")
        return f"- [{role}] {snippet}"

    def _word_count(self, text: str) -> int:
        return len([part for part in text.split() if part])

    def _fallback_summary_text(self, messages: list[Message]) -> str:
        bullet_lines = [self._bullet_line(message.role, message.content) for message in messages]
        return "\n".join(bullet_lines)

    async def _llm_summary_payload(self, messages: list[Message]) -> dict:
        """Generate structured compaction payload via provider JSON response."""
        prompt_lines = []
        for message in messages:
            line = f"[{message.role}]\n{message.content}"
            calls = (message.metadata_json or {}).get("tool_calls")
            if calls:
                line += "\nTool calls: " + json.dumps(calls)
            prompt_lines.append(line)
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
            model=TierName.FAST.value,
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
            "provider_usage": getattr(response, "provider_usage", None),
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
