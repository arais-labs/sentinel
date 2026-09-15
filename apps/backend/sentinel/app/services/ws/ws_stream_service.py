from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import JSON, func, literal, select, type_coerce
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_expression

from app.models import Message, Session
from sentral import (
    ConversationItem,
    GenerationConfig,
    ImageBlock,
    RunTurnRequest,
    TextBlock,
)
from app.services.agent.agent_modes import AgentMode, normalize_agent_mode_value
import app.services.agent_runtime_adapters.runtime as runtime_adapter_module
from sentral.llm.runtime_conversions import runtime_event_to_sentinel_event
from sentral.llm.generic.types import AgentEvent, ImageContent, TextContent
from sentral.llm.ids import TierName
from app.services.llm.session_selection import selection_model
from app.services.messages import (
    build_generation_metadata,
    normalize_generation_metadata,
    web_ingress_metadata,
    with_generation_metadata,
)
from app.services.modules.builtins.form.contract import validate_response
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.compaction import CompactionService
from app.services.sessions.session_naming import (
    apply_conversation_message_delta,
    conversation_delta_for_role,
)
from app.services.ws.ws_manager import ConnectionManager
from app.services.ws.ws_stream_parser import ParsedWsMessage

logger = logging.getLogger(__name__)


def _phase_from_sentinel_event(event: Any) -> str | None:
    event_type = getattr(event, "type", None)
    if event_type in {"agent_thinking", "thinking_start", "thinking_delta"}:
        return "thinking"
    if event_type == "text_delta":
        return "streaming_text"
    if event_type in {"toolcall_start", "toolcall_delta"}:
        return "tool_running"
    if event_type == "approval_required":
        return "pending_approval"
    if event_type == "tool_result":
        tool_result = getattr(event, "tool_result", None)
        metadata = getattr(tool_result, "metadata", None)
        if isinstance(metadata, dict):
            approval = metadata.get("approval")
            if metadata.get("pending") is True:
                return "pending_approval"
            if isinstance(approval, dict) and approval.get("pending") is True:
                return "pending_approval"
            if metadata.get("cancelled_by_stop") is True:
                return None
        return "thinking"
    if event_type in {"thinking_end", "toolcall_end"}:
        return None
    if event_type in {"done", "error", "agent_error"}:
        return None
    return None


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    failed: bool
    cancelled: bool
    run_error: str | None


class RuntimeSupportProtocol(Protocol):
    provider: Any
    context_builder: Any
    tool_adapter: Any

    async def prepare_runtime_turn_context(
        self, db: AsyncSession, session_id: UUID, **kwargs
    ) -> Any: ...

    async def persist_created_messages(
        self,
        db: AsyncSession,
        session_id: UUID,
        created: list[Any],
        assistant_iterations: dict[int, int],
        **kwargs,
    ) -> None: ...

    def extract_final_text(self, messages: list[Any]) -> str: ...

    def collect_attachments(self, messages: list[Any]) -> list[dict[str, Any]]: ...


async def get_session_record(db: AsyncSession, session_id: UUID) -> Session | None:
    result = await db.execute(select(Session).where(Session.id == session_id))
    return result.scalars().first()


async def load_history(
    db: AsyncSession, session_id: UUID, *, tool_state_only: bool = False
) -> list[dict[str, Any]]:
    query = (
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
    )
    if tool_state_only:
        # Reconnecting needs pending tool IDs and arguments, not screenshots or
        # saved model prompts. Full history remains available to the agent.
        query = query.options(
            with_expression(Message.content, literal("")),
            with_expression(
                Message.metadata_json,
                type_coerce(
                    func.json_object(
                        "tool_calls",
                        func.json_extract(Message.metadata_json, "$.tool_calls"),
                        "generation",
                        func.json_extract(Message.metadata_json, "$.generation"),
                    ),
                    JSON,
                ),
            ),
        )
    result = await db.execute(query)
    messages = result.scalars().all()
    return [
        {
            "id": str(message.id),
            "role": message.role,
            "content": message.content,
            "tool_call_id": message.tool_call_id,
            "tool_name": message.tool_name,
            "metadata": message.metadata_json or {},
            "created_at": _iso(message.created_at),
        }
        for message in messages
    ]


def unresolved_tool_calls_from_history(
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resolved_ids: set[str] = set()
    pending_order: list[str] = []
    pending: dict[str, dict[str, Any]] = {}

    for item in history:
        role = str(item.get("role") or "")
        if role == "assistant":
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            tool_calls = metadata.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for raw_call in tool_calls:
                if not isinstance(raw_call, dict):
                    continue
                call_id = str(raw_call.get("id") or "").strip()
                if not call_id or call_id in resolved_ids:
                    continue
                if call_id in pending:
                    continue
                name = str(raw_call.get("name") or "unknown")
                arguments = raw_call.get("arguments")
                generation = normalize_generation_metadata(
                    metadata.get("generation")
                    if isinstance(metadata.get("generation"), dict)
                    else None
                )
                pending[call_id] = {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments if isinstance(arguments, dict) else {},
                    "generation": generation,
                }
                pending_order.append(call_id)
            continue

        if role not in {"tool", "tool_result"}:
            continue
        call_id = str(item.get("tool_call_id") or "").strip()
        if not call_id:
            continue
        resolved_ids.add(call_id)
        pending.pop(call_id, None)

    return [pending[call_id] for call_id in pending_order if call_id in pending]


async def persist_user_message(
    db: AsyncSession,
    *,
    session_id: UUID,
    session: Session,
    content: str,
    attachments: list[dict[str, Any]],
    requested_tier: TierName | None,
    temperature: float,
    max_iterations: int,
    agent_mode: AgentMode,
    form_response: dict | None = None,
    provider_id: str | None = None,
    reasoning_level: str | None = None,
    fast_mode: bool = False,
    message_id: UUID | None = None,
    steering: bool = False,
) -> Message:
    metadata: dict[str, Any] = web_ingress_metadata()
    if steering:
        metadata.update(steering="pending", steering_id=str(message_id))
    if form_response is not None:

        content, metadata["form_response"] = await validate_response(db, session_id, form_response)
    metadata["model_selection"] = {
        "provider_id": provider_id,
        "reasoning_level": reasoning_level,
        "fast_mode": fast_mode,
    }
    metadata["agent_mode"] = normalize_agent_mode_value(agent_mode)
    if attachments:
        metadata["attachments"] = attachments
    generation = build_generation_metadata(
        requested_tier=requested_tier or TierName.NORMAL,
        resolved_model=None,
        provider=None,
        temperature=temperature,
        max_iterations=max_iterations,
    )
    metadata = with_generation_metadata(metadata, generation=generation)
    if content and not session.initial_prompt:
        session.initial_prompt = content
    apply_conversation_message_delta(session, conversation_delta_for_role("user"))

    message = Message(
        **({"id": message_id} if message_id is not None else {}),
        session_id=session_id,
        role="user",
        content=content,
        metadata_json=metadata,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


def build_user_payload(
    parsed: ParsedWsMessage,
) -> str | list[TextContent | ImageContent]:
    if not parsed.attachments:
        return parsed.content

    user_blocks: list[TextContent | ImageContent] = []
    if parsed.content:
        user_blocks.append(TextContent(text=parsed.content))
    for item in parsed.attachments:
        user_blocks.append(
            ImageContent(
                media_type=str(item.get("mime_type", "image/png")),
                data=str(item.get("base64", "")),
            )
        )
    return user_blocks


async def run_agent_once(
    *,
    db: AsyncSession,
    session_id: UUID,
    session_key: str,
    manager: ConnectionManager,
    run_registry: AgentRunRegistry,
    agent_runtime_support: RuntimeSupportProtocol,
    payload: str | list[TextContent | ImageContent],
    tier: TierName | None,
    max_iterations: int,
    agent_mode: AgentMode,
    persist_user_message: bool,
    provider_id: str | None = None,
    reasoning_level: str | None = None,
    fast_mode: bool = False,
    user_message: Message | None = None,
) -> AgentRunOutcome:
    runtime = runtime_adapter_module.SentinelLoopRuntimeAdapter(
        loop=agent_runtime_support,
        db=db,
        session_id=session_id,
        persist_incremental=True,
    )

    terminal_events = []

    async def _broadcast_event(event: Any) -> None:
        sentinel_event = runtime_event_to_sentinel_event(event)
        # Final UI events trigger history reloads. Commit the outcome first so
        # those reloads cannot replace a failure with an apparently sent message.
        if sentinel_event.type in {"error", "agent_error"} or (
            sentinel_event.type == "done" and sentinel_event.stop_reason != "tool_use"
        ):
            terminal_events.append(sentinel_event)
            return
        phase = _phase_from_sentinel_event(sentinel_event)
        if phase is not None:
            await run_registry.set_phase(session_key, phase)
        elif getattr(sentinel_event, "type", None) in {
            "thinking_end",
            "toolcall_end",
            "done",
            "error",
            "agent_error",
        }:
            await run_registry.set_phase(session_key, None)
        await manager.broadcast_agent_event(session_key, sentinel_event)

    run_task = await run_registry.start(
        session_key,
        runtime.run_turn(
            RunTurnRequest(
                conversation_id=session_key,
                new_items=[
                    ConversationItem(
                        id="user-input",
                        role="user",
                        content=_payload_to_runtime_blocks(payload),
                    )
                ],
                config=GenerationConfig(
                    model=selection_model(tier, provider_id, reasoning_level, fast_mode),
                    max_iterations=max_iterations,
                    stream=True,
                    provider_metadata={
                        "agent_mode": agent_mode,
                        "persist_user_message": persist_user_message,
                    },
                ),
                interjection_source=lambda: run_registry.drain_interjections(session_key),
            ),
            sink=_broadcast_event,
        ),
    )
    if run_task is None:
        await manager.broadcast_agent_error(
            session_key, "Agent is already processing this session."
        )
        await manager.broadcast_done(session_key, "error")
        return AgentRunOutcome(
            failed=True,
            cancelled=False,
            run_error="Agent is already processing this session.",
        )

    cancelled = False
    run_error: str | None = None
    failed = False
    completed = False
    try:
        await manager.broadcast(session_key, {"type": "run_state", "run_active": True})
        await run_registry.set_phase(session_key, "thinking")
        run_result = await run_task
        run_error = getattr(run_result, "error", None)
        failed = getattr(run_result, "status", None) in {"error", "timeout"}
        completed = getattr(run_result, "status", None) == "completed"
        cancelled = (
            getattr(run_result, "status", None) == "aborted"
            or run_error == "Generation stopped by user"
        )
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:  # noqa: BLE001
        failed = True
        run_error = str(exc)
        terminal_events.extend(
            [
                AgentEvent(type="error", error=str(exc)),
                AgentEvent(type="done", stop_reason="error"),
            ]
        )
    finally:
        # Covers both a returned runtime failure and a raised transport error.
        event_error = next(
            (
                event.error
                for event in terminal_events
                if event.type in {"error", "agent_error"} and event.error
            ),
            None,
        )
        run_error = run_error or event_error
        failed = failed or bool(run_error and not cancelled)
        try:
            if user_message is not None:
                metadata = dict(user_message.metadata_json or {})
                if failed:
                    metadata["retryable_error"] = run_error or "Agent failed"
                elif completed and not cancelled:
                    metadata.pop("retryable_error", None)
                if metadata != (user_message.metadata_json or {}):
                    user_message.metadata_json = metadata
                    await db.commit()
            for event in terminal_events:
                if user_message is not None and event.type in {"error", "agent_error"}:
                    await manager.broadcast(
                        session_key,
                        {
                            "type": event.type,
                            "session_id": session_key,
                            "message_id": str(user_message.id),
                            "error": event.error,
                        },
                    )
                else:
                    await manager.broadcast_agent_event(session_key, event)
        finally:
            await run_registry.clear(session_key, run_task)
        await manager.broadcast(
            session_key,
            {
                "type": "run_state",
                "run_active": await run_registry.is_running(session_key),
            },
        )

    return AgentRunOutcome(failed=failed, cancelled=cancelled, run_error=run_error)


# TODO: Add a mid-run compaction hook. This wrapper only runs *between* WS-driven
# agent runs. For long autonomous tasks, context can fill mid-iteration; the
# desired behavior is to pause the loop, compact, reload context, and continue
# the same run (no separate "resume" prompt). Trigger on TokenUsage.input_tokens
# from the last turn crossing ~85% of settings.context_token_budget. The hook
# belongs in packages/sentral/src/sentral/engine.py at the iteration boundary, not here.
async def maybe_auto_compact_after_run(
    *,
    db: AsyncSession,
    session_id: UUID,
    session_key: str,
    manager: ConnectionManager,
    agent_runtime_support: RuntimeSupportProtocol,
    compaction_service_cls: type[CompactionService] = CompactionService,
) -> None:
    try:
        compaction_svc = compaction_service_cls(
            provider=getattr(agent_runtime_support, "provider", None)
        )
        should_compact = await compaction_svc.should_auto_compact(db, session_id=session_id)
        if not should_compact:
            return

        await manager.broadcast(
            session_key,
            {"type": "compaction_started", "session_id": session_key},
        )
        result = await compaction_svc.auto_compact_if_needed(db, session_id=session_id)
        if result is None:
            return

        await manager.broadcast(
            session_key,
            {
                "type": "compaction_completed",
                "session_id": session_key,
                "compacted": result.compacted,
                "summary_preview": result.summary_preview,
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("Auto-compaction failed for session %s", session_id, exc_info=True)
        await manager.broadcast(
            session_key,
            {
                "type": "compaction_failed",
                "session_id": session_key,
                "error": "Auto-compaction failed",
            },
        )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _payload_to_runtime_blocks(
    payload: str | list[TextContent | ImageContent],
) -> list[TextBlock | ImageBlock]:
    if isinstance(payload, str):
        return [TextBlock(text=payload)]
    blocks: list[TextBlock | ImageBlock] = []
    for item in payload:
        if isinstance(item, TextContent):
            blocks.append(TextBlock(text=item.text))
        elif isinstance(item, ImageContent):
            blocks.append(ImageBlock(media_type=item.media_type, data=item.data))
    return blocks
