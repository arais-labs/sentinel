from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID
from weakref import WeakValueDictionary

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, Session, SessionSummary
from app.services.sessions.history import context_history
from app.services.sessions.handoff import render_summary
from app.services.sessions.compaction_generation import (
    current_selection,
    generate_handoff,
    source_record,
)
from app.services.sessions.context_usage import (
    latest_request_input_tokens,
    normalize_context_budget,
)
from sentral.llm.generic.base import LLMProvider

ACTIVE_CONTEXT_MESSAGE_COUNT = 10
_COMPACTION_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


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
        key = str(session.id)
        lock = _COMPACTION_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._compact_locked(db, session)

    async def _compact_locked(self, db: AsyncSession, session: Session) -> CompactionResult:
        """Snapshot, generate without a write lock, then compare and atomically publish."""
        session_id = session.id
        summary, messages = await context_history(db, session.id)
        previous = deepcopy(summary.summary) if summary else None
        # Upgrade legacy lossy summaries from their original source, not the paragraph.
        if summary and (summary.summary or {}).get("schema_version") != 2:
            _, messages = await context_history(db, session.id, include_compacted=True)
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

        selection = current_selection(messages)
        # Detached snapshots prevent expiry/later ORM changes from altering the request.
        frozen = [
            Message(
                id=m.id,
                role=m.role,
                content=m.content,
                metadata_json=deepcopy(m.metadata_json or {}),
                created_at=m.created_at,
                tool_name=m.tool_name,
                tool_call_id=m.tool_call_id,
            )
            for m in older
        ]
        boundary = str(older[-1].id)
        active_count, older_count = len(active_context), len(older)
        await db.commit()  # Release the read transaction before slow provider calls.
        trace = []
        try:
            payload = await generate_handoff(
                self._provider,
                frozen,
                previous if previous and previous.get("schema_version") == 2 else None,
                selection,
                trace=trace,
            )
        finally:
            # Every returned paid response counts, including rejected drafts and retries.
            # Persist separately so a failed/stale handoff cannot erase its usage.
            for call in trace:
                if call["provider_usage"]:
                    db.add(
                        Message(
                            session_id=session_id,
                            role="system",
                            content="Compaction model usage",
                            metadata_json={"source": "usage", "purpose": "compaction", **call},
                        )
                    )
            if any(call["provider_usage"] for call in trace):
                await db.commit()
        summary_text = render_summary(payload)
        payload["compacted_at"] = datetime.now(UTC).isoformat()
        payload["through_message_id"] = boundary
        payload["summary_text"] = summary_text
        payload["context_window_start"] = context_start.isoformat()
        payload["context_window_end"] = context_end.isoformat()
        payload["active_message_count"] = active_count
        payload["compacted_message_count"] = older_count
        try:
            # SQLite write serialization also protects first-summary creation across
            # processes; no network calls occur while this lock is held.
            await db.execute(
                update(Session)
                .where(Session.id == session_id)
                .values(updated_at=Session.updated_at)
            )
            result = await db.execute(
                select(SessionSummary)
                .where(SessionSummary.session_id == session_id)
                .execution_options(populate_existing=True)
            )
            summary = result.scalars().first()
            if (summary.summary if summary else None) != previous:
                raise ValueError(
                    "Another compaction changed this session; generated handoff was not applied."
                )
            # Recheck source membership/delivery order before advancing the boundary.
            _, latest = await context_history(db, session_id, include_compacted=True)
            ids = [
                str(m.id) for m in latest if (m.metadata_json or {}).get("steering") != "pending"
            ]
            if boundary not in ids:
                raise ValueError("Compaction source boundary disappeared; history was not changed.")
            prefix = ids[: ids.index(boundary) + 1]
            expected = [str(m.id) for m in frozen]
            if previous and previous.get("schema_version") == 2:
                old_boundary = previous.get("through_message_id")
                if old_boundary not in prefix:
                    raise ValueError("Previous compaction boundary disappeared.")
                prefix = prefix[prefix.index(old_boundary) + 1 :]
            if prefix != expected:
                raise ValueError(
                    "Historical delivery order changed during compaction; retry safely."
                )
            originals = {m.id: source_record(m) for m in frozen}
            if any(source_record(m) != originals[m.id] for m in latest if m.id in originals):
                raise ValueError(
                    "Historical message content changed during compaction; retry safely."
                )
            if summary is None:
                summary = SessionSummary(session_id=session_id, summary=payload)
                db.add(summary)
            else:
                summary.summary = payload
            db.add(
                Message(
                    session_id=session_id,
                    role="system",
                    content="Context compacted",
                    metadata_json={
                        "source": "runtime_context",
                        "purpose": "compaction",
                        "compaction_selection": payload["compaction_selection"],
                        "generation_trace": payload["generation_trace"],
                        "previous_summary": previous,
                    },
                )
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        preview = summary_text[:200]
        return CompactionResult(
            session_id=session_id,
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
