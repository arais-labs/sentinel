from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import GitPushApproval, Memory, Message, Session
from app.services.context_usage import (
    build_context_usage_metrics,
    estimate_agent_messages_tokens,
    estimate_db_messages_tokens,
    extract_runtime_context_metrics,
    normalize_context_budget,
)
from app.services import session_bindings
from app.services.agent_run_registry import AgentRunRegistry
from app.services.llm.generic.types import ImageContent, TextContent, UserMessage
from app.services.llm.ids import TierName
from app.services.session_runtime import (
    cleanup_session_runtime,
    get_session_runtime_snapshot,
    stop_all_detached_runtime_jobs,
)
from app.services.sessions.errors import (
    AgentLoopUnavailableError,
    ChatPayloadRequiredError,
    MainSessionDeletionError,
    MainSessionTargetInvalidError,
    MessageNotFoundError,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionPage:
    items: list[Session]
    total: int


@dataclass(slots=True)
class MessagePage:
    items: list[Message]
    has_more: bool


@dataclass(slots=True)
class ChatRunResult:
    final_text: str
    iterations: int
    input_tokens: int
    output_tokens: int
    error: str | None = None


class SessionService:
    def __init__(
        self,
        *,
        run_registry: AgentRunRegistry,
        agent_loop: Any | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._run_registry = run_registry
        self._agent_loop = agent_loop
        self._db_factory = db_factory or AsyncSessionLocal

    async def list_sessions(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        include_sub_agents: bool,
        limit: int,
        offset: int,
    ) -> SessionPage:
        query = select(Session).where(Session.user_id == user_id)
        if not include_sub_agents:
            query = query.where(Session.parent_session_id.is_(None))
        query = query.order_by(
            Session.updated_at.desc(),
            Session.created_at.desc(),
            Session.started_at.desc(),
            Session.id.desc(),
        )
        result = await db.execute(query)
        sessions = result.scalars().all()
        return SessionPage(items=sessions[offset : offset + limit], total=len(sessions))

    async def create_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        agent_id: str | None,
        title: str | None,
    ) -> Session:
        now = datetime.now(UTC)
        session = Session(
            user_id=user_id,
            agent_id=agent_id,
            title=title,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    async def get_default_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        agent_id: str | None,
    ) -> Session:
        session = await session_bindings.resolve_or_create_main_session(
            db,
            user_id=user_id,
            agent_id=agent_id,
        )
        await db.commit()
        await db.refresh(session)
        return session

    async def reset_default_session(
        self,
        db: AsyncSession,
        *,
        user_id: str,
        agent_id: str | None,
    ) -> Session:
        current_main = await session_bindings.resolve_or_create_main_session(
            db,
            user_id=user_id,
            agent_id=agent_id,
        )
        old_session_ids = [current_main.id]

        now = datetime.now(UTC)
        session = Session(
            user_id=user_id,
            agent_id=agent_id,
            title="Main",
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        await db.flush()
        await session_bindings.set_main_session(
            db,
            user_id=user_id,
            session_id=session.id,
        )
        await db.commit()
        await db.refresh(session)

        await self._cleanup_runtime_for_session_ids(old_session_ids)
        return session

    async def set_main_session(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> Session:
        _ = await self.get_session(db, session_id=session_id, user_id=user_id)
        is_telegram_channel = await session_bindings.is_session_bound(
            db,
            user_id=user_id,
            session_id=session_id,
            binding_types={
                session_bindings.TELEGRAM_GROUP_BINDING_TYPE,
                session_bindings.TELEGRAM_DM_BINDING_TYPE,
            },
            active_only=True,
        )
        if is_telegram_channel:
            raise MainSessionTargetInvalidError(
                "Telegram channel sessions cannot be set as main"
            )
        try:
            session = await session_bindings.set_main_session(
                db,
                user_id=user_id,
                session_id=session_id,
            )
        except session_bindings.SessionBindingTargetInvalidError as exc:
            raise MainSessionTargetInvalidError(str(exc)) from exc
        await db.commit()
        await db.refresh(session)
        return session

    async def get_session(self, db: AsyncSession, *, session_id: UUID, user_id: str) -> Session:
        result = await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
        session = result.scalars().first()
        if session is None:
            raise SessionNotFoundError("Session not found")
        return session

    async def get_runtime_snapshot(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        action_limit: int,
    ) -> dict[str, Any]:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        return get_session_runtime_snapshot(session.id, action_limit=action_limit)

    async def get_context_usage(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> dict[str, Any]:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        budget = normalize_context_budget(settings.context_token_budget)
        system_result = await db.execute(
            select(Message)
            .where(
                Message.session_id == session.id,
                Message.role == "system",
            )
            .order_by(Message.created_at.desc())
            .limit(200)
        )
        system_messages = system_result.scalars().all()

        usage_tokens: int | None = None
        usage_percent: int | None = None
        snapshot_created_at: datetime | None = None
        source = "runtime_context"
        for message in system_messages:
            metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
            if str(metadata.get("source") or "").strip().lower() != "runtime_context":
                continue
            metrics = extract_runtime_context_metrics(
                metadata.get("run_context")
                if isinstance(metadata.get("run_context"), dict)
                else None,
                default_budget=budget,
            )
            if metrics is None:
                continue
            budget = metrics.context_token_budget
            usage_tokens = metrics.estimated_context_tokens
            usage_percent = metrics.estimated_context_percent
            snapshot_created_at = message.created_at
            break

        if usage_tokens is None:
            rebuilt = await self._estimate_rebuilt_context_usage(
                db,
                session_id=session.id,
                context_budget=budget,
            )
            if rebuilt is not None:
                budget = rebuilt.context_token_budget
                usage_tokens = rebuilt.estimated_context_tokens
                usage_percent = rebuilt.estimated_context_percent
                source = "rebuilt_context_estimate"
            else:
                message_result = await db.execute(
                    select(Message)
                    .where(Message.session_id == session.id)
                    .order_by(Message.created_at.asc())
                )
                messages = message_result.scalars().all()
                fallback = build_context_usage_metrics(
                    estimated_tokens=estimate_db_messages_tokens(messages),
                    context_budget=budget,
                )
                budget = fallback.context_token_budget
                usage_tokens = fallback.estimated_context_tokens
                usage_percent = fallback.estimated_context_percent
                source = "db_messages_fallback"

        return {
            "session_id": session.id,
            "context_token_budget": budget,
            "estimated_context_tokens": usage_tokens,
            "estimated_context_percent": usage_percent,
            "snapshot_created_at": snapshot_created_at,
            "source": source,
        }

    async def _estimate_rebuilt_context_usage(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        context_budget: int,
    ):
        """Estimate context using the same builder path used before actual runs."""
        context_builder = getattr(self._agent_loop, "context_builder", None)
        if context_builder is None or not hasattr(context_builder, "build"):
            return None
        try:
            built = await context_builder.build(
                db,
                session_id,
                system_prompt=None,
                pending_user_message=None,
            )
        except Exception:
            return None
        return build_context_usage_metrics(
            estimated_tokens=estimate_agent_messages_tokens(built),
            context_budget=context_budget,
        )

    async def cleanup_runtime(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> tuple[UUID, dict[str, bool]]:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        result = await cleanup_session_runtime(session.id)
        return session.id, result

    async def delete_session(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> int:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        main_session_id = await self._get_main_session_id(db, user_id=user_id)
        if main_session_id is not None and session.id == main_session_id:
            raise MainSessionDeletionError("Main session cannot be deleted")
        descendants = await self._get_descendant_sessions(
            db, root_session_id=session.id, user_id=user_id
        )
        for child in descendants:
            await db.delete(child)
        await db.delete(session)
        await db.commit()
        await self._cleanup_runtime_for_session_ids([session.id, *[c.id for c in descendants]])
        return len(descendants)

    async def stop_generation(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> bool:
        _ = await self.get_session(db, session_id=session_id, user_id=user_id)
        cancelled = await self._run_registry.cancel(str(session_id))
        now = datetime.now(UTC)
        has_mutations = False
        pending_result = await db.execute(
            select(GitPushApproval).where(
                GitPushApproval.session_id == session_id,
                GitPushApproval.status == "pending",
            )
        )
        pending_rows = pending_result.scalars().all()
        if pending_rows:
            for row in pending_rows:
                row.status = "cancelled"
                row.decision_note = "Cancelled by user via stop"
                row.resolved_at = now
            has_mutations = True

        unresolved_tool_calls = await self._unresolved_tool_calls(db, session_id=session_id)
        if unresolved_tool_calls:
            content = json.dumps(
                {
                    "status": "cancelled",
                    "message": "Tool call cancelled by user via stop.",
                }
            )
            for call in unresolved_tool_calls:
                db.add(
                    Message(
                        session_id=session_id,
                        role="tool_result",
                        content=content,
                        metadata_json={"pending": False, "cancelled_by_stop": True},
                        tool_call_id=call["id"],
                        tool_name=call["name"],
                    )
                )
            has_mutations = True

        if has_mutations:
            await db.commit()
        await stop_all_detached_runtime_jobs(
            session_id,
            reason="Cancelled by user via stop",
        )
        return cancelled

    async def _unresolved_tool_calls(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
    ) -> list[dict[str, str]]:
        result = await db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()

        resolved_ids: set[str] = set()
        pending_order: list[str] = []
        pending: dict[str, dict[str, str]] = {}

        for item in messages:
            role = str(item.role or "")
            if role == "assistant":
                metadata = item.metadata_json if isinstance(item.metadata_json, dict) else {}
                tool_calls = metadata.get("tool_calls")
                if not isinstance(tool_calls, list):
                    continue
                for raw_call in tool_calls:
                    if not isinstance(raw_call, dict):
                        continue
                    call_id = str(raw_call.get("id") or "").strip()
                    if not call_id or call_id in resolved_ids or call_id in pending:
                        continue
                    call_name = str(raw_call.get("name") or "unknown").strip() or "unknown"
                    pending[call_id] = {"id": call_id, "name": call_name}
                    pending_order.append(call_id)
                continue

            if role not in {"tool", "tool_result"}:
                continue
            call_id = str(item.tool_call_id or "").strip()
            if not call_id:
                continue
            resolved_ids.add(call_id)
            pending.pop(call_id, None)

        return [pending[call_id] for call_id in pending_order if call_id in pending]

    async def create_message(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any],
    ) -> Message:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)

        if role == "user" and not session.initial_prompt and content.strip():
            session.initial_prompt = content.strip()

        message = Message(
            session_id=session.id,
            role=role,
            content=content,
            metadata_json=metadata,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message

    async def list_messages(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        limit: int,
        before: UUID | None,
    ) -> MessagePage:
        _ = await self.get_session(db, session_id=session_id, user_id=user_id)
        result = await db.execute(select(Message).where(Message.session_id == session_id))
        messages = result.scalars().all()
        messages.sort(
            key=lambda m: m.created_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )

        if before:
            before_message = next((msg for msg in messages if msg.id == before), None)
            if before_message is None:
                raise MessageNotFoundError("Message not found")
            before_created_at = before_message.created_at
            messages = [
                msg for msg in messages if msg.created_at and msg.created_at < before_created_at
            ]

        sliced = messages[: limit + 1]
        has_more = len(sliced) > limit
        return MessagePage(items=sliced[:limit], has_more=has_more)

    async def run_chat(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        content: str,
        attachments: list[Any],
        tier: TierName | None,
        system_prompt: str | None,
        temperature: float,
        max_iterations: int,
    ) -> ChatRunResult:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        if self._agent_loop is None:
            raise AgentLoopUnavailableError("No LLM provider configured")

        text = content.strip()
        if not text and not attachments:
            raise ChatPayloadRequiredError("content or attachments required")

        if attachments:
            user_blocks: list[TextContent | ImageContent] = []
            if text:
                user_blocks.append(TextContent(text=text))
            for item in attachments:
                base64_data = item.base64.strip()
                if ";base64," in base64_data:
                    _, _, base64_data = base64_data.partition(";base64,")
                user_blocks.append(
                    ImageContent(
                        media_type=item.mime_type,
                        data=base64_data,
                    )
                )
            user_payload: str | list[TextContent | ImageContent] = user_blocks
        else:
            user_payload = text

        result = await self._agent_loop.run(
            db,
            session.id,
            user_payload,
            system_prompt=system_prompt,
            model=(tier or TierName.NORMAL).value,
            temperature=temperature,
            max_iterations=max_iterations,
            allow_high_risk=True,
            stream=False,
        )
        return ChatRunResult(
            final_text=result.final_text,
            iterations=result.iterations,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            error=getattr(result, "error", None),
        )

    async def is_session_running(self, session_id: UUID) -> bool:
        return await self._run_registry.is_running(str(session_id))

    async def compute_unread_flags(
        self,
        db: AsyncSession,
        sessions: list[Session],
    ) -> dict[UUID, bool]:
        if not sessions:
            return {}
        session_ids = [s.id for s in sessions]
        result = await db.execute(
            select(
                Message.session_id,
                sa_func.max(Message.created_at).label("latest_msg"),
            )
            .where(
                Message.session_id.in_(session_ids),
                Message.role.in_(["assistant", "tool_result"]),
            )
            .group_by(Message.session_id)
        )
        latest_by_session: dict[UUID, datetime] = {
            row.session_id: row.latest_msg for row in result
        }
        flags: dict[UUID, bool] = {}
        for session in sessions:
            latest_msg = latest_by_session.get(session.id)
            if latest_msg is None:
                flags[session.id] = False
            elif session.last_read_at is None:
                flags[session.id] = True
            else:
                flags[session.id] = latest_msg > session.last_read_at
        return flags

    async def mark_as_read(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> Session:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        session.last_read_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(session)
        return session

    async def get_main_session_id(self, db: AsyncSession, *, user_id: str) -> UUID | None:
        return await self._get_main_session_id(db, user_id=user_id)

    async def extract_session_memories(self, session_ids: list[UUID], user_id: str) -> None:
        """Summarize prior sessions into memory nodes (fire-and-forget)."""
        if self._agent_loop is None:
            return
        try:
            async with self._db_factory() as db:
                for session_id in session_ids:
                    messages = await self._session_messages_for_distillation(db, session_id)
                    if not messages:
                        continue

                    transcript = self._build_transcript(messages)
                    prompt = (
                        "You are a memory distillation agent. Given a conversation transcript, "
                        "extract the most important durable facts, decisions, user preferences, "
                        "and outcomes. Write a concise structured summary (max 300 words) that "
                        "would help an assistant in a future session understand what happened "
                        "and what matters. Focus on facts, not chat pleasantries.\n\n"
                        f"TRANSCRIPT:\n{transcript}"
                    )
                    result = await self._agent_loop.provider.chat(
                        [UserMessage(content=prompt)],
                        model=TierName.FAST.value,
                        tools=[],
                        temperature=0.3,
                    )
                    summary_text = "".join(
                        block.text for block in result.content if isinstance(block, TextContent)
                    ).strip()
                    if not summary_text:
                        continue

                    root = await self._get_or_create_previous_sessions_root(db)
                    date_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
                    db.add(
                        Memory(
                            content=summary_text,
                            title=f"Session summary ({date_str})",
                            summary=summary_text[:200],
                            category="core",
                            importance=65,
                            parent_id=root.id,
                            session_id=session_id,
                            metadata_json={"source": "session_reset", "user_id": user_id},
                        )
                    )
                await db.commit()
        except Exception:
            logger.warning("Memory extraction on reset failed", exc_info=True)

    async def _session_messages_for_distillation(
        self, db: AsyncSession, session_id: UUID
    ) -> list[Message]:
        result = await db.execute(
            select(Message).where(
                Message.session_id == session_id,
                Message.role.in_(["user", "assistant"]),
            )
        )
        return result.scalars().all()

    def _build_transcript(self, messages: list[Message]) -> str:
        lines: list[str] = []
        for message in sorted(
            messages, key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC)
        ):
            role = message.role.upper()
            snippet = (message.content or "")[:400].replace("\n", " ")
            lines.append(f"{role}: {snippet}")
        return "\n".join(lines)[:6000]

    async def _get_or_create_previous_sessions_root(self, db: AsyncSession) -> Memory:
        result = await db.execute(
            select(Memory)
            .where(
                Memory.title == "Previous Sessions",
                Memory.parent_id.is_(None),
            )
            .limit(1)
        )
        root = result.scalars().first()
        if root is not None:
            return root

        root = Memory(
            content=(
                "Archive of past session summaries. "
                "Reference these when context from previous conversations may be relevant."
            ),
            title="Previous Sessions",
            summary="Past session summaries — reference if needed.",
            category="core",
            importance=80,
            pinned=True,
            metadata_json={"source": "session_reset_root"},
        )
        db.add(root)
        await db.flush()
        return root

    async def _get_main_session_id(self, db: AsyncSession, *, user_id: str) -> UUID | None:
        existing = await session_bindings.resolve_main_session_id(db, user_id=user_id)
        if existing is not None:
            return existing

        session = await session_bindings.resolve_or_create_main_session(
            db,
            user_id=user_id,
            agent_id=None,
        )
        await db.commit()
        return session.id

    async def _get_descendant_sessions(
        self,
        db: AsyncSession,
        *,
        root_session_id: UUID,
        user_id: str,
    ) -> list[Session]:
        result = await db.execute(select(Session).where(Session.user_id == user_id))
        sessions = result.scalars().all()
        by_parent: dict[UUID, list[Session]] = {}
        for session in sessions:
            parent_id = session.parent_session_id
            if parent_id is None:
                continue
            by_parent.setdefault(parent_id, []).append(session)

        descendants: list[Session] = []
        stack: list[UUID] = [root_session_id]
        seen: set[UUID] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            children = by_parent.get(current, [])
            for child in children:
                descendants.append(child)
                stack.append(child.id)
        return descendants

    async def _cleanup_runtime_for_session_ids(self, session_ids: list[UUID]) -> None:
        unique_ids = list(dict.fromkeys(session_ids))
        if not unique_ids:
            return
        tasks = [cleanup_session_runtime(session_id) for session_id in unique_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for session_id, result in zip(unique_ids, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "Runtime cleanup failed for session %s", session_id, exc_info=result
                )
