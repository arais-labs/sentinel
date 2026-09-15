from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    and_,
    delete,
    literal_column,
    or_,
    select,
    type_coerce,
    update,
)
from sqlalchemy import (
    func as sa_func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased, with_expression

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Message, Session, SessionSummary, ToolApproval
from app.models.session_bindings import SessionBinding
from app.models.triggers import Trigger
from sentral import (
    ConversationItem,
    GenerationConfig,
    ImageBlock,
    RunTurnRequest,
    TextBlock,
)
from app.services.agent.agent_modes import AgentMode, get_default_agent_mode
import app.services.agent_runtime_adapters.runtime as runtime_adapter_module
from sentral.llm.ids import TierName
from app.services.llm.session_selection import selection_model
from app.services.messages import (
    normalize_generation_metadata,
    with_generation_metadata,
)
from app.services.runtime.session_cleanup import cleanup_records
from app.services.sessions import session_bindings
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.context_usage import normalize_context_budget
from app.services.sessions.errors import (
    AgentRuntimeUnavailableError,
    ChatPayloadRequiredError,
    MessageNotFoundError,
    SessionNotFoundError,
    SessionRenameNotAllowedError,
    SessionWorkspaceCleanupError,
)
from app.services.sessions.session_naming import (
    SessionNamingService,
    apply_conversation_message_delta,
    conversation_delta_for_role,
)
from app.services.sessions.usage import conversation_usage, merge_usage

logger = logging.getLogger(__name__)

SessionDeleteCleanup = Callable[[list[UUID]], Awaitable[None]]


def _final_response_condition():
    """Use the same final-response boundary for previews, unread state and alerts."""
    output = sa_func.json_each(Message.metadata_json["responses_output"]).table_valued("value")
    phase = sa_func.json_extract(output.c.value, "$.phase")
    message_output = sa_func.json_extract(output.c.value, "$.type") == "message"
    commentary = (
        select(1)
        .select_from(output)
        .where(message_output, phase == "commentary")
        .correlate(Message)
        .exists()
    )
    final = (
        select(1)
        .select_from(output)
        .where(message_output, phase == "final_answer")
        .correlate(Message)
        .exists()
    )
    return and_(
        Message.role == "assistant",
        Message.metadata_json["stop_reason"].as_string().in_(["stop", "end_turn"]),
        sa_func.coalesce(sa_func.json_array_length(Message.metadata_json["tool_calls"]), 0) == 0,
        ~commentary | final,
    )


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
        agent_runtime_support: Any | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._run_registry = run_registry
        self._agent_runtime_support = agent_runtime_support
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
        query = select(Session)
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

    async def fork_session(self, db: AsyncSession, *, session_id: UUID, user_id: str) -> Session:
        """Snapshot persisted history only. Never register a run or copy live bindings."""
        source = await self.get_session(db, session_id=session_id, user_id=user_id)
        summaries = (
            (
                await db.execute(
                    select(SessionSummary).where(SessionSummary.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        messages = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at, Message.id)
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        fork = Session(
            id=uuid4(),
            user_id=user_id,
            agent_id=source.agent_id,
            workspace_id=source.workspace_id,
            title=f"{(source.title or 'Session')[:248]} (fork)",
            status="active",
            initial_prompt=source.initial_prompt,
            started_at=now,
            created_at=now,
            updated_at=now,
            last_read_at=now,
            conversation_message_count=sum(conversation_delta_for_role(m.role) for m in messages),
        )
        fork.last_auto_rename_count = fork.conversation_message_count
        db.add(fork)
        # Preserve ordering even when several original messages have identical timestamps.
        identifiers = dict(
            zip(
                (str(m.id) for m in messages),
                sorted((uuid4() for _ in messages), key=str),
                strict=True,
            )
        )
        for message in messages:
            metadata = deepcopy(message.metadata_json or {})
            metadata["forked_from_message_id"] = str(message.id)
            anchor = metadata.get("steering_after_message_id")
            if anchor in identifiers:
                metadata["steering_after_message_id"] = str(identifiers[anchor])
            # Historical approvals/retries must not become actionable in the new session.
            for key in ("approval", "pending", "retry_settings", "retryable_error"):
                metadata.pop(key, None)
            if metadata.get("steering") == "pending":
                metadata["steering"] = "cancelled"
            db.add(
                Message(
                    id=identifiers[str(message.id)],
                    session_id=fork.id,
                    role=message.role,
                    content=message.content,
                    metadata_json=metadata,
                    token_count=message.token_count,
                    tool_call_id=message.tool_call_id,
                    tool_name=message.tool_name,
                    created_at=message.created_at,
                )
            )
        for summary in summaries:
            payload = deepcopy(summary.summary or {})
            boundary = payload.get("through_message_id")
            if boundary in identifiers:
                payload["through_message_id"] = str(identifiers[boundary])
            db.add(
                SessionSummary(session_id=fork.id, summary=payload, created_at=summary.created_at)
            )
        db.add(
            Message(
                session_id=fork.id,
                role="system",
                created_at=now,
                content=(
                    f"This session is a fork of {source.title or 'Session'} ({source.id}). "
                    "The conversation above is copied history. The workspace is shared with the "
                    "original session. No running tasks, approvals, or automations were copied. "
                    "Wait for the user's follow-up; do not resume prior tasks or repeat tool actions "
                    "merely because they appear in the copied history."
                ),
                metadata_json={
                    "source": "session_fork",
                    "source_session_id": str(source.id),
                    "notice": {"title": "Session fork"},
                },
            )
        )
        await db.commit()
        await db.refresh(fork)
        return fork

    async def rename_session(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        title: str | None,
    ) -> Session:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
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
            raise SessionRenameNotAllowedError("Telegram channel sessions cannot be renamed")
        session.title = title
        await db.commit()
        await db.refresh(session)
        return session

    async def get_session(self, db: AsyncSession, *, session_id: UUID, user_id: str) -> Session:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise SessionNotFoundError("Session not found")
        return session

    async def get_usage(self, db: AsyncSession, *, session_id: UUID, user_id: str) -> dict:
        await self.get_session(db, session_id=session_id, user_id=user_id)

        main = await conversation_usage(db, session_id)
        children = await self._get_descendant_sessions(
            db, root_session_id=session_id, user_id=user_id
        )
        agents = []
        for child in children:
            agents.append(
                {
                    "session_id": str(child.id),
                    "name": child.title,
                    **await conversation_usage(db, child.id),
                }
            )
        delegated = merge_usage(agents)
        return {
            "session_id": str(session_id),
            **merge_usage([main, delegated]),
            "main": main,
            "sub_agents": agents,
            "delegated": delegated,
        }

    async def get_context_usage(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> dict[str, Any]:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        budget = normalize_context_budget(settings.context_token_budget)
        latest_result = await db.execute(
            select(Message)
            .where(Message.session_id == session.id, Message.role == "assistant")
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()
        measured = (latest.metadata_json or {}).get("provider_usage") if latest else None
        return {
            "session_id": session.id,
            "context_token_budget": budget,
            "last_request_usage": measured,
            "snapshot_created_at": latest.created_at if latest else None,
            "source": "provider_response" if measured else "unavailable",
        }

    async def delete_session(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        before_delete: SessionDeleteCleanup | None = None,
    ) -> int:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        descendants = await self._get_descendant_sessions(
            db, root_session_id=session.id, user_id=user_id
        )
        if before_delete is not None:
            affected_session_ids = [session.id, *(child.id for child in descendants)]
            try:
                await before_delete(affected_session_ids)
            except SessionWorkspaceCleanupError:
                raise
            except Exception as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                raise SessionWorkspaceCleanupError(
                    "Machine workspace cleanup failed; session was not deleted.",
                    detail=detail,
                ) from exc

        for record in await cleanup_records(db, [session, *descendants]):
            db.add(record)
        for child in descendants:
            await db.delete(child)
        await db.delete(session)
        await db.commit()
        return len(descendants)

    async def discard_empty_session(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        before_delete: SessionDeleteCleanup | None = None,
    ) -> bool:

        async with self._run_registry.idle_guard(str(session_id)) as idle:
            if not idle:
                return False
            child = aliased(Session)
            # Repeat these predicates in DELETE so messages arriving concurrently
            # cannot be removed based on an earlier empty-history snapshot.
            eligible = (
                Session.id == session_id,
                Session.user_id == user_id,
                Session.parent_session_id.is_(None),
                (Session.initial_prompt.is_(None) | (Session.initial_prompt == "")),
                Session.conversation_message_count == 0,
                ~select(Message.id).where(Message.session_id == session_id).exists(),
                ~select(child.id).where(child.parent_session_id == session_id).exists(),
                ~select(SessionBinding.id).where(SessionBinding.session_id == session_id).exists(),
                ~select(Trigger.id)
                .where(Trigger.action_config["target_session_id"].as_string() == str(session_id))
                .exists(),
            )
            if not await db.scalar(select(Session.id).where(*eligible)):
                return False
            if before_delete:
                await before_delete([session_id])

            session = await db.get(Session, session_id)
            cleanup = await cleanup_records(db, [session]) if session else []
            deleted = await db.scalar(delete(Session).where(*eligible).returning(Session.id))
            if deleted is not None:
                for record in cleanup:
                    db.add(record)
            await db.commit()
            return deleted is not None

    async def stop_generation(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
    ) -> bool:
        _ = await self.get_session(db, session_id=session_id, user_id=user_id)
        cancelled = await self._run_registry.cancel_and_wait(str(session_id))
        now = datetime.now(UTC)
        has_mutations = False
        tool_pending_result = await db.execute(
            select(ToolApproval).where(
                ToolApproval.session_id == session_id,
                ToolApproval.status == "pending",
            )
        )
        tool_pending_rows = tool_pending_result.scalars().all()
        if tool_pending_rows:
            for row in tool_pending_rows:
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
            call_ids = [
                str(call.get("id") or "").strip()
                for call in unresolved_tool_calls
                if str(call.get("id") or "").strip()
            ]
            existing_result = await db.execute(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.role == "tool_result",
                    Message.tool_call_id.in_(call_ids),
                )
            )
            existing_messages = {
                str(row.tool_call_id or "").strip(): row
                for row in existing_result.scalars().all()
                if str(row.tool_call_id or "").strip()
            }
            for call in unresolved_tool_calls:
                call_id = str(call.get("id") or "").strip()
                if not call_id:
                    continue
                generation = normalize_generation_metadata(
                    call.get("generation") if isinstance(call.get("generation"), dict) else None
                )
                metadata = with_generation_metadata(
                    {"pending": False, "cancelled_by_stop": True},
                    generation=generation,
                )
                existing_message = existing_messages.get(call_id)
                if existing_message is not None:
                    self._apply_terminal_tool_result_update(
                        existing_message,
                        content=content,
                        metadata=metadata,
                        approval_status="cancelled",
                        decision_note="Cancelled by user via stop",
                    )
                    continue
                db.add(
                    Message(
                        session_id=session_id,
                        role="tool_result",
                        content=content,
                        metadata_json=metadata,
                        tool_call_id=call_id,
                        tool_name=call["name"],
                    )
                )
            has_mutations = True

        if has_mutations:
            await db.commit()
        return cancelled

    @staticmethod
    def _apply_terminal_tool_result_update(
        message: Message,
        *,
        content: str,
        metadata: dict[str, Any],
        approval_status: str,
        decision_note: str,
    ) -> None:
        existing_metadata = (
            dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
        )
        approval = existing_metadata.get("approval")
        if isinstance(approval, dict):
            next_approval = dict(approval)
            next_approval["status"] = approval_status
            next_approval["pending"] = False
            next_approval["can_resolve"] = False
            next_approval["decision_note"] = decision_note
            metadata["approval"] = next_approval
        message.content = content
        message.metadata_json = metadata

    async def _unresolved_tool_calls(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
    ) -> list[dict[str, Any]]:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
        )
        messages = result.scalars().all()

        resolved_ids: set[str] = set()
        pending_order: list[str] = []
        pending: dict[str, dict[str, Any]] = {}

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
                    generation = normalize_generation_metadata(
                        metadata.get("generation")
                        if isinstance(metadata.get("generation"), dict)
                        else None
                    )
                    pending[call_id] = {"id": call_id, "name": call_name}
                    pending[call_id]["generation"] = generation
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
        apply_conversation_message_delta(session, conversation_delta_for_role(role))

        message = Message(
            session_id=session.id,
            role=role,
            content=content,
            metadata_json=metadata,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)
        if role == "user":
            naming = SessionNamingService(
                provider=getattr(self._agent_runtime_support, "provider", None),
                db_factory=self._db_factory,
            )
            await naming.maybe_auto_rename(session_id=session.id)
        return message

    async def list_messages(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        limit: int,
        before: UUID | None,
        final_only: bool = False,
        chat_view: bool = False,
    ) -> MessagePage:
        _ = await self.get_session(db, session_id=session_id, user_id=user_id)
        query = select(Message).where(Message.session_id == session_id)
        if final_only:
            query = query.where(_final_response_condition())
        if before:
            cursor = (
                await db.execute(
                    select(Message.created_at, literal_column("messages.rowid")).where(
                        Message.session_id == session_id, Message.id == before
                    )
                )
            ).first()
            if cursor is None:
                raise MessageNotFoundError("Message not found")
            query = query.where(
                or_(
                    Message.created_at < cursor[0],
                    and_(
                        Message.created_at == cursor[0],
                        literal_column("messages.rowid") < cursor[1],
                    ),
                )
            )
        order = (Message.created_at.desc(), literal_column("messages.rowid").desc())
        # Select the page before reading/processing large message bodies. SQLite
        # otherwise evaluates projected JSON for rows later discarded by LIMIT.
        page_ids = query.with_only_columns(Message.id).order_by(*order).limit(limit + 1)
        query = select(Message).where(Message.session_id == session_id, Message.id.in_(page_ids))
        if chat_view:
            # Logs retain the full record. Chat does not render model attribution,
            # prompt snapshots, or raw provider output; omit them before JSON decoding.
            query = query.options(
                with_expression(
                    Message.metadata_json,
                    type_coerce(
                        sa_func.json_remove(
                            Message.metadata_json,
                            "$.provider_usage",
                            "$.run_context",
                            "$.responses_output",
                        ),
                        JSON,
                    ),
                )
            )
        query = query.order_by(*order)
        messages = (await db.execute(query)).scalars().all()
        return MessagePage(items=messages[:limit], has_more=len(messages) > limit)

    async def run_chat(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        user_id: str,
        content: str,
        attachments: list[Any],
        tier: TierName | None,
        agent_mode: AgentMode | None,
        system_prompt: str | None,
        temperature: float,
        max_iterations: int,
        provider_id: str | None = None,
        reasoning_level: str | None = None,
        fast_mode: bool = False,
    ) -> ChatRunResult:
        session = await self.get_session(db, session_id=session_id, user_id=user_id)
        if self._agent_runtime_support is None:
            raise AgentRuntimeUnavailableError("No LLM provider configured")

        text = content.strip()
        if not text and not attachments:
            raise ChatPayloadRequiredError("content or attachments required")

        if attachments:
            user_blocks: list[TextBlock | ImageBlock] = []
            if text:
                user_blocks.append(TextBlock(text=text))
            for item in attachments:
                base64_data = item.base64.strip()
                if ";base64," in base64_data:
                    _, _, base64_data = base64_data.partition(";base64,")
                user_blocks.append(
                    ImageBlock(
                        media_type=item.mime_type,
                        data=base64_data,
                    )
                )
            user_blocks_payload = user_blocks
        else:
            user_blocks_payload = [TextBlock(text=text)]

        mode = agent_mode or get_default_agent_mode()

        runtime = runtime_adapter_module.SentinelLoopRuntimeAdapter(
            loop=self._agent_runtime_support,
            db=db,
            session_id=session.id,
        )
        result = await runtime.run_turn(
            RunTurnRequest(
                conversation_id=str(session.id),
                new_items=[
                    ConversationItem(
                        id="chat-user-input",
                        role="user",
                        content=user_blocks_payload,
                    )
                ],
                config=GenerationConfig(
                    model=selection_model(tier, provider_id, reasoning_level, fast_mode),
                    temperature=temperature,
                    max_iterations=max_iterations,
                    stream=False,
                    system_prompt=system_prompt,
                    provider_metadata={"agent_mode": mode},
                ),
                interjection_source=lambda: self._run_registry.drain_interjections(str(session.id)),
            )
        )
        naming = SessionNamingService(
            provider=getattr(self._agent_runtime_support, "provider", None),
            db_factory=self._db_factory,
        )
        await naming.maybe_auto_rename(session_id=session.id)
        return ChatRunResult(
            final_text=str(result.metadata.get("final_text") or ""),
            iterations=result.iterations,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            error=result.error,
        )

    async def is_session_running(self, session_id: UUID) -> bool:
        return await self._run_registry.is_running(str(session_id))

    async def completion_ids(self, db: AsyncSession, sessions: list[Session]) -> dict[UUID, str]:
        completed = await self.completion_details(db, sessions)
        return {session_id: str(value[0]) for session_id, value in completed.items()}

    async def completion_details(self, db: AsyncSession, sessions: list[Session]) -> dict:
        if not sessions:
            return {}
        result = await db.execute(
            select(Message.session_id, Message.id, Message.created_at)
            .where(
                Message.session_id.in_([s.id for s in sessions]),
                _final_response_condition(),
            )
            .order_by(Message.created_at, literal_column("messages.rowid"))
        )
        return {row.session_id: (row.id, row.created_at) for row in result}

    async def compute_unread_flags(
        self,
        db: AsyncSession,
        sessions: list[Session],
        *,
        latest_by_session: dict[UUID, datetime] | None = None,
    ) -> dict[UUID, bool]:
        if not sessions:
            return {}
        if latest_by_session is None:
            session_ids = [s.id for s in sessions]
            result = await db.execute(
                select(
                    Message.session_id,
                    sa_func.max(Message.created_at).label("latest_msg"),
                )
                .where(
                    Message.session_id.in_(session_ids),
                    _final_response_condition(),
                )
                .group_by(Message.session_id)
            )
            latest_by_session: dict[UUID, datetime] = {
                row.session_id: row.latest_msg for row in result
            }
        flags: dict[UUID, bool] = {}
        for session in sessions:
            latest_msg = latest_by_session.get(session.id)
            if latest_msg is None or await self.is_session_running(session.id):
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
        await db.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(last_read_at=datetime.now(UTC), updated_at=Session.updated_at)
        )
        await db.commit()
        await db.refresh(session)
        return session

    async def _get_descendant_sessions(
        self,
        db: AsyncSession,
        *,
        root_session_id: UUID,
        user_id: str,
    ) -> list[Session]:
        result = await db.execute(select(Session))
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
