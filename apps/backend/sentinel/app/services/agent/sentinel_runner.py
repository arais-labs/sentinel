"""Sentinel application wrapper around runtime execution.

This module owns Sentinel-specific concerns around one run:
- context loading from session state
- persistence of created messages
- runtime context snapshotting
- estop enforcement

The reusable execution engine lives outside this wrapper.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.logging_context import reset_log_session, set_log_session
from app.models import Message, Session
from app.services.agent.agent_modes import AgentMode, get_agent_mode_definition
from app.services.sessions.context_usage import (
    build_context_usage_metrics,
    estimate_agent_messages_tokens,
)
from app.services.agent.context_builder import ContextBuilder
from app.services.agent.execution_core import AgentExecutionCore
from app.services.agent.tool_image_reinjection import (
    ToolImageReinjectionPolicy,
)
from app.services.agent.tool_adapter import ToolAdapter
from app.services.estop import EstopLevel, EstopService
from app.services.messages import (
    build_generation_metadata,
    with_generation_metadata,
)
from app.services.sessions.session_naming import (
    apply_conversation_message_delta,
    conversation_delta_for_role,
)
from app.services.llm.generic.base import LLMProvider
from app.services.llm.ids import TierName
from app.services.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
    ToolResultContent,
    ToolResultMessage,
    ToolSchema,
    SystemMessage,
    UserMessage,
)


logger = logging.getLogger(__name__)


def _humanize_error(raw: str) -> str:
    """Return a user-friendly error message for common LLM failures."""
    text = str(raw or "")
    lower = text.lower()
    if "all providers failed" in lower:
        normalized = " ".join(text.split())
        normalized_lower = normalized.lower()
        if normalized_lower.startswith("all providers failed"):
            parts = normalized.split(".", 1)
            if len(parts) == 2 and parts[1].strip():
                normalized = f"All AI providers failed.{parts[1]}"
            else:
                normalized = "All AI providers failed."
        return normalized[:700] if len(normalized) > 700 else normalized
    if any(k in lower for k in ("rate_limit", "rate limit", "http_429", "429")):
        return "API rate limit reached. Please wait a moment and try again."
    if any(k in lower for k in ("authentication", "401", "invalid api key", "invalid_api_key")):
        return "API authentication failed. Please check your API key in Settings."
    if any(k in lower for k in ("insufficient", "billing", "payment", "402")):
        return "API billing issue. Please check your account balance and payment method."
    if any(k in lower for k in ("overloaded", "503", "server_error")):
        return "The AI provider is currently overloaded. Please try again in a few moments."
    if any(k in lower for k in ("timeout", "timed out")):
        return "Request timed out. The server took too long to respond."
    return text[:300] if len(text) > 300 else text


def _make_error_message(error_text: str) -> AssistantMessage:
    """Create an AssistantMessage that will be persisted and shown in the chat."""
    return AssistantMessage(
        content=[TextContent(text=f"⚠️ **Error:** {error_text}")],
        stop_reason="error",
    )


@dataclass(slots=True)
class AgentLoopResult:
    """Final artifact of one agent run, including text, usage, and attachments."""

    final_text: str
    messages_created: int
    usage: TokenUsage
    iterations: int
    error: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class PreparedRuntimeTurnContext:
    """Sentinel-prepared context needed to execute one runtime turn."""

    messages: list[AgentMessage]
    tools: list[ToolSchema]
    effective_system_prompt: str | None
    runtime_context_snapshot: dict[str, Any] | None


class AgentLoop:
    """Sentinel-facing runner that prepares and persists one agent turn."""

    def __init__(
        self,
        provider: LLMProvider,
        context_builder: ContextBuilder,
        tool_adapter: ToolAdapter,
        estop_service: EstopService | None = None,
    ) -> None:
        self.provider = provider
        self.context_builder = context_builder
        self.tool_adapter = tool_adapter
        self._estop = estop_service or EstopService()
        self._execution_core = AgentExecutionCore(
            provider=provider,
            tool_adapter=tool_adapter,
            stream_response=self._stream_response,
            grace_analysis=self._grace_analysis,
            stream_safe_tool_metadata=self._stream_safe_tool_metadata,
            summarize_response_blocks=self._summarize_response_blocks,
            make_error_message=_make_error_message,
            humanize_error=_humanize_error,
        )

    async def estop_level(self, db: AsyncSession) -> EstopLevel:
        """Return the current Sentinel emergency-stop level."""
        return await self._estop.check_level(db)

    async def prepare_runtime_turn_context(
        self,
        db: AsyncSession,
        session_id: UUID,
        *,
        system_prompt: str | None,
        pending_user_message: str,
        agent_mode: AgentMode | str,
        model: str,
        temperature: float,
        max_iterations: int,
        stream: bool,
    ) -> PreparedRuntimeTurnContext:
        """Build the Sentinel-owned history/tool snapshot for one runtime turn."""
        messages = await self.context_builder.build(
            db,
            session_id,
            system_prompt,
            pending_user_message=pending_user_message,
            agent_mode=agent_mode,
        )
        tools = self.tool_adapter.get_tool_schemas()
        return PreparedRuntimeTurnContext(
            messages=messages,
            tools=tools,
            effective_system_prompt=self.extract_runtime_system_prompt(messages),
            runtime_context_snapshot=self.build_runtime_context_snapshot(
                messages,
                tools,
                model=model,
                temperature=temperature,
                max_iterations=max_iterations,
                stream=stream,
                agent_mode=agent_mode,
            ),
        )

    async def persist_created_messages(
        self,
        db: AsyncSession,
        session_id: UUID,
        created: list[AgentMessage],
        assistant_iterations: dict[int, int],
        *,
        requested_tier: TierName | str | None,
        temperature: float,
        max_iterations: int,
        effective_system_prompt: str | None = None,
        runtime_context_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Persist run-created messages using Sentinel's storage model."""
        await self._persist_messages(
            db,
            session_id,
            created,
            assistant_iterations,
            requested_tier=requested_tier,
            temperature=temperature,
            max_iterations=max_iterations,
            effective_system_prompt=effective_system_prompt,
            runtime_context_snapshot=runtime_context_snapshot,
        )

    def extract_runtime_system_prompt(self, messages: list[AgentMessage]) -> str | None:
        """Public wrapper for the persisted runtime system prompt view."""
        return self._extract_runtime_system_prompt(messages)

    def build_runtime_context_snapshot(
        self,
        messages: list[AgentMessage],
        tools: list[ToolSchema],
        *,
        model: str,
        temperature: float,
        max_iterations: int,
        stream: bool,
        agent_mode: AgentMode | str,
    ) -> dict[str, Any]:
        """Public wrapper for Sentinel's runtime snapshot payload."""
        return self._build_runtime_context_snapshot(
            messages,
            tools,
            model=model,
            temperature=temperature,
            max_iterations=max_iterations,
            stream=stream,
            agent_mode=agent_mode,
        )

    def extract_final_text(self, messages: list[AgentMessage]) -> str:
        """Return the final assistant text from a created message batch."""
        return self._extract_final_text(messages)

    def collect_attachments(self, messages: list[AgentMessage]) -> list[dict[str, Any]]:
        """Return tool-produced attachments from a created message batch."""
        return self._collect_attachments(messages)

    async def run(
        self,
        db: AsyncSession,
        session_id: UUID,
        user_message: str | list[TextContent | ImageContent],
        *,
        system_prompt: str | None = None,
        max_iterations: int = 50,
        temperature: float = 0.7,
        model: str = TierName.NORMAL.value,
        agent_mode: AgentMode | str | None = None,
        persist_user_message: bool = True,
        stream: bool = True,
        timeout_seconds: float | None = None,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
        inject_queue: asyncio.Queue[str] | None = None,
        persist_incremental: bool = False,
        user_metadata: dict[str, Any] | None = None,
    ) -> AgentLoopResult:
        """Execute a full agent run for one user turn and persist resulting messages."""
        session_log_token = set_log_session(session_id)
        if timeout_seconds is None:
            timeout_seconds = settings.agent_loop_timeout
        normalized_user_metadata = (
            dict(user_metadata)
            if isinstance(user_metadata, dict)
            else {}
        )
        try:
            mode_definition = get_agent_mode_definition(agent_mode)
            normalized_user_metadata["agent_mode"] = mode_definition.id.value
            user = UserMessage(content=user_message, metadata=normalized_user_metadata)
            created_seed: list[AgentMessage] = [user] if persist_user_message else []

            if await self.estop_level(db) == EstopLevel.KILL_ALL:
                if on_event is not None:
                    await on_event(AgentEvent(type="error", error="Emergency stop KILL_ALL is active"))
                    await on_event(AgentEvent(type="done", stop_reason="aborted"))
                await self.persist_created_messages(
                    db,
                    session_id,
                    created_seed,
                    {},
                    requested_tier=model,
                    temperature=temperature,
                    max_iterations=max_iterations,
                )
                return AgentLoopResult(
                    final_text="",
                    messages_created=len(created_seed),
                    usage=TokenUsage(),
                    iterations=0,
                    attachments=[],
                )

            prepared = await self.prepare_runtime_turn_context(
                db,
                session_id,
                system_prompt=system_prompt,
                pending_user_message=self._user_text(user_message),
                agent_mode=mode_definition.id,
                model=model,
                temperature=temperature,
                max_iterations=max_iterations,
                stream=stream,
            )
            messages = [*prepared.messages]
            tools = prepared.tools
            runtime_system_prompt = prepared.effective_system_prompt
            runtime_context_snapshot = prepared.runtime_context_snapshot
            context_snapshot_pending = True
            messages.append(user)

            logger.info(
                "AgentLoop.run: session_id=%s model=%s stream=%s tools=%s",
                session_id, model, stream, [t.name for t in tools],
            )

            _caller_on_event = on_event
            deferred_done: AgentEvent | None = None
            if on_event is not None and stream:
                async def _intercepted_on_event(event: AgentEvent) -> None:
                    nonlocal deferred_done
                    if event.type == "done" and event.stop_reason != "tool_use":
                        deferred_done = event
                    else:
                        await _caller_on_event(event)  # type: ignore[misc]
                on_event = _intercepted_on_event

            persisted_count = 0

            async def _persist_checkpoint(
                batch: list[AgentMessage],
                assistant_iterations: dict[int, int],
            ) -> None:
                nonlocal persisted_count, context_snapshot_pending
                if not persist_incremental or not batch:
                    return
                snapshot = runtime_context_snapshot if context_snapshot_pending else None
                await self.persist_created_messages(
                    db,
                    session_id,
                    batch,
                    assistant_iterations,
                    requested_tier=model,
                    temperature=temperature,
                    max_iterations=max_iterations,
                    effective_system_prompt=runtime_system_prompt,
                    runtime_context_snapshot=snapshot,
                )
                if snapshot is not None:
                    context_snapshot_pending = False
                persisted_count += len(batch)

            artifacts = await self._execution_core.execute(
                db=db,
                session_id=session_id,
                messages=messages,
                created_seed=created_seed,
                tools=tools,
                model=model,
                temperature=temperature,
                max_iterations=max_iterations,
                stream=stream,
                timeout_seconds=timeout_seconds,
                agent_mode=mode_definition.id,
                reinjection_policy=ToolImageReinjectionPolicy(
                    enabled=settings.tool_image_reinjection_enabled,
                    max_images_per_turn=max(0, int(settings.tool_image_reinjection_max_images)),
                    max_bytes_per_image=max(1, int(settings.tool_image_reinjection_max_bytes_per_image)),
                    max_total_bytes_per_turn=max(1, int(settings.tool_image_reinjection_max_total_bytes)),
                ),
                cooldown_seconds=max(0.0, float(settings.agent_loop_cooldown)),
                on_event=on_event,
                inject_queue=inject_queue,
                on_checkpoint=_persist_checkpoint if persist_incremental else None,
            )

            remaining = artifacts.created[persisted_count:]
            snapshot = runtime_context_snapshot if context_snapshot_pending else None
            await self.persist_created_messages(
                db,
                session_id,
                remaining,
                artifacts.assistant_iterations,
                requested_tier=model,
                temperature=temperature,
                max_iterations=max_iterations,
                effective_system_prompt=runtime_system_prompt,
                runtime_context_snapshot=snapshot,
            )
            if snapshot is not None:
                context_snapshot_pending = False

            if deferred_done is not None and _caller_on_event is not None:
                await _caller_on_event(deferred_done)
            elif not artifacts.done_emitted and _caller_on_event is not None:
                await _caller_on_event(AgentEvent(type="done", stop_reason="stop"))

            return AgentLoopResult(
                final_text=self.extract_final_text(artifacts.created),
                messages_created=len(artifacts.created),
                usage=artifacts.usage,
                iterations=artifacts.iterations,
                error=artifacts.error,
                attachments=self.collect_attachments(artifacts.created),
            )
        finally:
            reset_log_session(session_log_token)

    async def _stream_response(
        self,
        messages: list[AgentMessage],
        *,
        model: str,
        tools: list[ToolSchema],
        temperature: float,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None,
        partial_out: list[AssistantMessage] | None = None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        """Run provider streaming and assemble one assistant message from events.

        We intentionally keep consuming stream chunks after intermediate `done`
        events and use the final done marker observed.
        """
        streamed_events: list[AgentEvent] = []
        done_event: AgentEvent | None = None
        fallback_model = model
        fallback_provider = self.provider.name
        generation_hint = self.provider.resolve_generation_hint(model)
        if generation_hint is not None:
            hinted_provider, hinted_model = generation_hint
            if hinted_provider:
                fallback_provider = hinted_provider
            if hinted_model:
                fallback_model = hinted_model

        try:
            async for event in self.provider.stream(
                messages,
                model=model,
                tools=tools,
                temperature=temperature,
                reasoning_config=None,
                tool_choice=tool_choice,
            ):
                if event.type == "done":
                    # Some providers may emit intermediate done events before all chunks.
                    # Keep draining and use the last done we observed.
                    done_event = event
                    continue
                streamed_events.append(event)
                if on_event is not None:
                    await on_event(event)
        except asyncio.CancelledError:
            # Assemble whatever partial content was streamed before cancellation
            if partial_out is not None and streamed_events:
                partial_out.append(
                    self._assemble_message_from_events(
                        streamed_events,
                        fallback_model=fallback_model,
                        fallback_provider=fallback_provider,
                    )
                )
            raise

        final_done = done_event or AgentEvent(type="done", stop_reason="stop")
        streamed_events.append(final_done)
        if on_event is not None:
            await on_event(final_done)

        return self._assemble_message_from_events(
            streamed_events,
            fallback_model=fallback_model,
            fallback_provider=fallback_provider,
        )

    def _assemble_message_from_events(
        self,
        events: list[AgentEvent],
        *,
        fallback_model: str,
        fallback_provider: str,
    ) -> AssistantMessage:
        """Convert low-level stream events into one AssistantMessage."""
        # Preserve first-seen block order per (kind, content_index). Some providers
        # reuse the same content_index for both text and tool call blocks.
        block_sequence: list[tuple[str, int]] = []
        seen_blocks: set[tuple[str, int]] = set()
        text_blocks: dict[int, list[str]] = {}
        thinking_blocks: dict[int, list[str]] = {}
        thinking_signatures: dict[int, list[str]] = {}
        tool_calls: dict[int, dict[str, Any]] = {}
        usage = TokenUsage()
        model = fallback_model
        provider = fallback_provider
        stop_reason = "stop"

        def _remember(kind: str, index: int) -> None:
            key = (kind, index)
            if key in seen_blocks:
                return
            seen_blocks.add(key)
            block_sequence.append(key)

        for event in events:
            idx = event.content_index if event.content_index is not None else 0
            if event.message is not None:
                if event.message.model:
                    model = event.message.model
                if event.message.provider:
                    provider = event.message.provider
                usage.input_tokens += event.message.usage.input_tokens
                usage.output_tokens += event.message.usage.output_tokens

            if event.type == "text_start":
                _remember("text", idx)
                text_blocks.setdefault(idx, [])
            elif event.type == "text_delta":
                _remember("text", idx)
                text_blocks.setdefault(idx, []).append(event.delta or "")
            elif event.type == "thinking_start":
                _remember("thinking", idx)
                thinking_blocks.setdefault(idx, [])
            elif event.type == "thinking_delta":
                _remember("thinking", idx)
                thinking_blocks.setdefault(idx, []).append(event.delta or "")
                if event.signature:
                    thinking_signatures.setdefault(idx, []).append(event.signature)
            elif event.type == "toolcall_start":
                _remember("tool_call", idx)
                tool_call = event.tool_call or ToolCallContent()
                tool_calls[idx] = {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arg_deltas": [],
                    "base_args": tool_call.arguments,
                    "thought_signature": tool_call.thought_signature,
                }
            elif event.type == "toolcall_delta":
                _remember("tool_call", idx)
                call_state = tool_calls.setdefault(
                    idx,
                    {
                        "id": "",
                        "name": "",
                        "arg_deltas": [],
                        "base_args": {},
                        "thought_signature": None,
                    },
                )
                call_state["arg_deltas"].append(event.delta or "")
            elif event.type == "error":
                stop_reason = "error"
            elif event.type == "done":
                if stop_reason != "error":
                    stop_reason = event.stop_reason or stop_reason

        # --- DEBUG: log what we collected from stream ---
        logger.debug(
            "Assemble: blocks=%s tool_calls_keys=%s stop_reason=%s",
            [f"{kind}@{idx}" for kind, idx in block_sequence],
            list(tool_calls.keys()),
            stop_reason,
        )
        # --- END DEBUG ---

        content: list[TextContent | ThinkingContent | ToolCallContent] = []
        for block_type, idx in block_sequence:
            if block_type == "text":
                text_value = "".join(text_blocks.get(idx, []))
                if text_value:
                    content.append(TextContent(text=text_value))
                continue
            if block_type == "thinking":
                sig_parts = thinking_signatures.get(idx, [])
                sig = "".join(sig_parts) if sig_parts else None
                thinking_value = "".join(thinking_blocks.get(idx, []))
                if thinking_value or sig:
                    content.append(ThinkingContent(
                        thinking=thinking_value,
                        signature=sig,
                    ))
                continue
            if block_type == "tool_call":
                call_state = tool_calls.get(
                    idx,
                    {
                        "id": "",
                        "name": "",
                        "arg_deltas": [],
                        "base_args": {},
                        "thought_signature": None,
                    },
                )
                raw_args = "".join(call_state.get("arg_deltas", []))
                parsed_args: dict[str, Any]
                if raw_args:
                    try:
                        loaded = json.loads(raw_args)
                        parsed_args = loaded if isinstance(loaded, dict) else {"value": loaded}
                    except json.JSONDecodeError:
                        parsed_args = {"raw": raw_args}
                else:
                    base_args = call_state.get("base_args")
                    parsed_args = base_args if isinstance(base_args, dict) else {}

                if isinstance(call_state.get("base_args"), dict) and isinstance(parsed_args, dict):
                    merged = dict(call_state["base_args"])
                    merged.update(parsed_args)
                    parsed_args = merged

                content.append(
                    ToolCallContent(
                        id=str(call_state.get("id") or ""),
                        name=str(call_state.get("name") or ""),
                        arguments=parsed_args,
                        thought_signature=(
                            str(call_state.get("thought_signature")).strip()
                            if isinstance(call_state.get("thought_signature"), str)
                            and str(call_state.get("thought_signature")).strip()
                            else None
                        ),
                    )
                )

        if any(isinstance(block, ToolCallContent) for block in content) and stop_reason not in {"error", "aborted", "timeout"}:
            stop_reason = "tool_use"

        return AssistantMessage(
            content=content,
            model=model,
            provider=provider,
            usage=usage,
            stop_reason=stop_reason,
        )

    async def _persist_messages(
        self,
        db: AsyncSession,
        session_id: UUID,
        created: list[AgentMessage],
        assistant_iterations: dict[int, int],
        *,
        requested_tier: TierName | str | None,
        temperature: float,
        max_iterations: int,
        effective_system_prompt: str | None = None,
        runtime_context_snapshot: dict[str, Any] | None = None,
    ) -> None:
        """Persist run-created messages in chronological order with metadata."""
        base_time = datetime.now(UTC)
        session_record = await db.get(Session, session_id)
        requested_generation = build_generation_metadata(
            requested_tier=requested_tier,
            resolved_model=None,
            provider=None,
            temperature=temperature,
            max_iterations=max_iterations,
        )
        latest_assistant_generation: dict[str, Any] | None = None
        if session_record is not None and effective_system_prompt:
            prompt = effective_system_prompt.strip()
            if prompt:
                session_record.latest_system_prompt = prompt
        conversation_delta = 0
        start_offset = 0
        if runtime_context_snapshot:
            summary = (
                f"[Runtime Context Snapshot] model={runtime_context_snapshot.get('model', '')} "
                f"tools={runtime_context_snapshot.get('tool_count', 0)} "
                f"system_blocks={runtime_context_snapshot.get('system_message_count', 0)}"
            )
            db.add(
                Message(
                    session_id=session_id,
                    role="system",
                    content=summary,
                    metadata_json=with_generation_metadata(
                        {"source": "runtime_context", "run_context": runtime_context_snapshot},
                        generation=requested_generation,
                    ),
                    created_at=base_time,
                )
            )
            start_offset = 1
        for idx, message in enumerate(created):
            created_at = base_time + timedelta(milliseconds=idx + start_offset)
            if isinstance(message, UserMessage):
                metadata = dict(message.metadata or {})
                text_content = self._user_text(message.content)
                if (
                    session_record is not None
                    and not session_record.initial_prompt
                    and text_content.strip()
                ):
                    session_record.initial_prompt = text_content.strip()
                metadata = with_generation_metadata(
                    metadata,
                    generation=requested_generation,
                )
                if isinstance(message.content, list):
                    attachments: list[dict[str, Any]] = []
                    for block in message.content:
                        if isinstance(block, ImageContent) and block.data:
                            attachments.append(
                                {
                                    "mime_type": block.media_type,
                                    "base64": block.data,
                                }
                            )
                    if attachments:
                        existing = metadata.get("attachments")
                        if isinstance(existing, list):
                            metadata["attachments"] = [*existing, *attachments]
                        else:
                            metadata["attachments"] = attachments
                record = Message(
                    session_id=session_id,
                    role="user",
                    content=text_content,
                    metadata_json=metadata,
                    created_at=created_at,
                )
                db.add(record)
                conversation_delta += conversation_delta_for_role("user")
                continue

            if isinstance(message, AssistantMessage):
                text = self._assistant_text(message)
                tool_calls_data: list[dict[str, Any]] = []
                for block in message.content:
                    if not isinstance(block, ToolCallContent):
                        continue
                    persisted_call = {
                        "id": block.id,
                        "name": block.name,
                        "arguments": self._sanitize_tool_call_arguments(block.arguments),
                        "thought_signature": block.thought_signature,
                    }
                    tool_calls_data.append(persisted_call)
                metadata: dict[str, Any] = {
                    "provider": message.provider,
                    "model": message.model,
                    "stop_reason": message.stop_reason,
                    "input_tokens": message.usage.input_tokens,
                    "output_tokens": message.usage.output_tokens,
                    "iteration": int(assistant_iterations.get(id(message), 0)),
                }
                if tool_calls_data:
                    metadata["tool_calls"] = tool_calls_data
                assistant_generation = build_generation_metadata(
                    requested_tier=requested_tier,
                    resolved_model=message.model,
                    provider=message.provider,
                    temperature=temperature,
                    max_iterations=max_iterations,
                )
                metadata = with_generation_metadata(
                    metadata,
                    generation=assistant_generation,
                )
                latest_assistant_generation = assistant_generation
                record = Message(
                    session_id=session_id,
                    role="assistant",
                    content=text,
                    metadata_json=metadata,
                    token_count=message.usage.output_tokens,
                    created_at=created_at,
                )
                db.add(record)
                conversation_delta += conversation_delta_for_role("assistant")
                continue

            if isinstance(message, ToolResultMessage):
                raw_metadata = dict(message.metadata or {})
                persisted_message_id = raw_metadata.pop("__persisted_message_id", None)
                metadata = {"is_error": message.is_error}
                metadata.update({k: v for k, v in raw_metadata.items() if not str(k).startswith("__")})
                metadata = with_generation_metadata(
                    metadata,
                    generation=latest_assistant_generation or requested_generation,
                )
                stored_content, truncation_meta = self._truncate_tool_result_for_storage(
                    message.content or ""
                )
                if truncation_meta:
                    metadata.update(truncation_meta)
                existing_record = None
                if isinstance(persisted_message_id, str) and persisted_message_id.strip():
                    try:
                        existing_record = await db.get(Message, UUID(persisted_message_id.strip()))
                    except ValueError:
                        existing_record = None
                if existing_record is not None:
                    existing_record.content = stored_content
                    existing_record.metadata_json = metadata
                    existing_record.tool_call_id = message.tool_call_id or None
                    existing_record.tool_name = message.tool_name or None
                else:
                    record = Message(
                        session_id=session_id,
                        role="tool_result",
                        content=stored_content,
                        metadata_json=metadata,
                        tool_call_id=message.tool_call_id or None,
                        tool_name=message.tool_name or None,
                        created_at=created_at,
                    )
                    db.add(record)

        if session_record is not None:
            apply_conversation_message_delta(session_record, conversation_delta)

        await db.commit()

    @staticmethod
    def _extract_runtime_system_prompt(messages: list[AgentMessage]) -> str | None:
        blocks: list[str] = []
        for message in messages:
            if not isinstance(message, SystemMessage):
                continue
            prompt = (message.content or "").strip()
            if prompt:
                blocks.append(prompt)
        if not blocks:
            return None
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _truncate_runtime_history_preview(value: str, *, max_chars: int = 320) -> str:
        text = value.strip()
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars].rstrip()}..."

    @staticmethod
    def _runtime_history_entry(message: AgentMessage) -> dict[str, Any] | None:
        if isinstance(message, UserMessage):
            text_parts: list[str] = []
            image_count = 0
            if isinstance(message.content, str):
                text = message.content.strip()
                if text:
                    text_parts.append(text)
            elif isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, TextContent):
                        text = (block.text or "").strip()
                        if text:
                            text_parts.append(text)
                    elif isinstance(block, ImageContent):
                        if (block.data or "").strip():
                            image_count += 1

            preview = "\n\n".join(text_parts).strip()
            if not preview and image_count > 0:
                preview = f"[{image_count} image attachment{'s' if image_count != 1 else ''}]"

            entry: dict[str, Any] = {
                "role": "user",
                "kind": "history_user",
                "preview": AgentLoop._truncate_runtime_history_preview(preview) if preview else None,
                "text_block_count": len(text_parts),
                "image_count": image_count,
            }
            source = message.metadata.get("source") if isinstance(message.metadata, dict) else None
            if isinstance(source, str) and source.strip():
                entry["source"] = source.strip()
            return entry

        if isinstance(message, AssistantMessage):
            text_parts = [
                (block.text or "").strip()
                for block in message.content
                if isinstance(block, TextContent) and (block.text or "").strip()
            ]
            tool_calls = [
                {
                    "id": block.id,
                    "name": block.name,
                }
                for block in message.content
                if isinstance(block, ToolCallContent) and (block.id or block.name)
            ]
            preview = "\n\n".join(text_parts).strip()
            if not preview and tool_calls:
                call_names = ", ".join(
                    call["name"] for call in tool_calls if isinstance(call.get("name"), str) and call["name"].strip()
                )
                if call_names:
                    preview = f"Planned tool call{'s' if len(tool_calls) != 1 else ''}: {call_names}"

            entry = {
                "role": "assistant",
                "kind": "history_assistant",
                "preview": AgentLoop._truncate_runtime_history_preview(preview) if preview else None,
                "text_block_count": len(text_parts),
                "tool_call_count": len(tool_calls),
            }
            if tool_calls:
                entry["tool_calls"] = tool_calls
            return entry

        if isinstance(message, ToolResultMessage):
            preview = AgentLoop._truncate_runtime_history_preview((message.content or "").strip())
            return {
                "role": "tool_result",
                "kind": "history_tool_result",
                "preview": preview or None,
                "tool_name": message.tool_name or None,
                "tool_call_id": message.tool_call_id or None,
                "is_error": bool(message.is_error),
            }

        return None

    @staticmethod
    def _build_runtime_context_snapshot(
        messages: list[AgentMessage],
        tools: list[ToolSchema],
        *,
        model: str,
        temperature: float,
        max_iterations: int,
        stream: bool,
        agent_mode: AgentMode | str,
    ) -> dict[str, Any]:
        system_blocks: list[str] = []
        layered_context: list[dict[str, Any]] = []
        memory_blocks: list[dict[str, Any]] = []
        history_messages: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, SystemMessage):
                history_entry = AgentLoop._runtime_history_entry(message)
                if history_entry is not None:
                    history_messages.append(history_entry)
                continue
            content = (message.content or "").strip()
            if not content:
                continue
            system_blocks.append(content)
            metadata = dict(message.metadata or {})
            layer = str(metadata.get("layer") or "system")
            kind = str(metadata.get("kind") or "system_block")
            title = str(metadata.get("title") or f"System block #{len(system_blocks)}")
            explanation = str(metadata.get("explanation") or "").strip() or None

            layer_entry: dict[str, Any] = {
                "layer": layer,
                "kind": kind,
                "title": title,
                "explanation": explanation,
                "content": content,
            }

            raw_blocks = metadata.get("memory_blocks")
            layer_memory_blocks: list[dict[str, Any]] = []
            if isinstance(raw_blocks, list):
                for item in raw_blocks:
                    if not isinstance(item, dict):
                        continue
                    normalized = {
                        "source": str(item.get("source") or "unknown"),
                        "memory_id": item.get("memory_id"),
                        "root_id": item.get("root_id"),
                        "title": str(item.get("title") or "Untitled"),
                        "summary": item.get("summary"),
                        "content": item.get("content"),
                        "category": item.get("category"),
                        "pinned": bool(item.get("pinned")),
                        "injected_full": bool(item.get("injected_full")),
                        "depth": int(item.get("depth") or 0),
                        "importance": item.get("importance"),
                        "score": item.get("score"),
                    }
                    layer_memory_blocks.append(normalized)
                    memory_blocks.append(normalized)
            if layer_memory_blocks:
                layer_entry["memory_blocks"] = layer_memory_blocks

            layered_context.append(layer_entry)

        if history_messages:
            layered_context.append(
                {
                    "layer": "history",
                    "kind": "conversation_history",
                    "title": "Injected Previous Messages",
                    "explanation": "Recent conversation history injected into this run context.",
                    "history_messages": history_messages,
                }
            )

        pinned_memories = [
            {
                "title": block["title"],
                "content": str(block.get("content") or "").strip(),
            }
            for block in memory_blocks
            if block.get("pinned") and isinstance(block.get("content"), str) and str(block.get("content")).strip()
        ]
        if not pinned_memories:
            for block in layered_context:
                content = str(block.get("content") or "")
                if content.startswith("## Memory (pinned):"):
                    first_line, _, remainder = content.partition("\n")
                    title = first_line.replace("## Memory (pinned):", "").strip()
                    pinned_memories.append({"title": title or "Untitled", "content": remainder.strip()})

        usage_metrics = build_context_usage_metrics(
            estimated_tokens=estimate_agent_messages_tokens(messages),
            context_budget=settings.context_token_budget,
        )

        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "model": model,
            "agent_mode": agent_mode.value if isinstance(agent_mode, AgentMode) else str(agent_mode),
            "temperature": temperature,
            "max_iterations": max_iterations,
            "stream": stream,
            "system_message_count": len(system_blocks),
            "system_messages": system_blocks,
            "pinned_memories": pinned_memories,
            "structured_context": {
                "version": "v2",
                "layers": layered_context,
                "memory_blocks": memory_blocks,
                "layer_count": len(layered_context),
                "memory_block_count": len(memory_blocks),
                "history_message_count": len(history_messages),
            },
            "context_token_budget": usage_metrics.context_token_budget,
            "estimated_context_tokens": usage_metrics.estimated_context_tokens,
            "estimated_context_percent": usage_metrics.estimated_context_percent,
            "tool_count": len(tools),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
                for tool in tools
            ],
        }

    def _extract_final_text(self, messages: list[AgentMessage]) -> str:
        for message in reversed(messages):
            if not isinstance(message, AssistantMessage):
                continue
            text = self._assistant_text(message).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _collect_attachments(messages: list[AgentMessage]) -> list[dict[str, Any]]:
        """Gather image attachments from all ToolResultMessage metadata."""
        attachments: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, ToolResultMessage) and msg.metadata:
                for att in msg.metadata.get("attachments", []):
                    if isinstance(att, dict) and "base64" in att:
                        attachments.append(att)
        return attachments

    def _assistant_text(self, message: AssistantMessage) -> str:
        """Extract concatenated text blocks from an assistant message."""
        parts = [block.text for block in message.content if isinstance(block, TextContent) and block.text]
        return "\n".join(parts)

    def _sanitize_tool_call_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Keep tool-call args compact in persisted assistant metadata."""
        max_chars = max(200, int(settings.stored_tool_call_args_max_chars))
        try:
            serialized = json.dumps(arguments, ensure_ascii=False)
        except Exception:
            serialized = str(arguments)
        if len(serialized) <= max_chars:
            return arguments
        return {
            "_truncated": True,
            "preview": serialized[:max_chars],
            "original_chars": len(serialized),
        }

    def _truncate_tool_result_for_storage(self, content: str) -> tuple[str, dict[str, Any]]:
        """Cap stored tool-result payload size while preserving debug metadata."""
        max_chars = max(200, int(settings.stored_tool_result_max_chars))
        if len(content) <= max_chars:
            return content, {}
        truncated = content[:max_chars] + f"\n...[TRUNCATED_FOR_STORAGE - {len(content)} chars]"
        return truncated, {
            "storage_truncated": True,
            "original_chars": len(content),
            "stored_chars": len(truncated),
        }

    @staticmethod
    def _user_text(content: str | list[TextContent | ImageContent]) -> str:
        """Extract user-facing text from mixed text/image user content."""
        if isinstance(content, str):
            return content
        parts = [block.text for block in content if isinstance(block, TextContent) and block.text]
        return "\n".join(parts)

    @staticmethod
    def _summarize_response_blocks(response: AssistantMessage) -> list[str]:
        """Build compact debug labels for one assembled assistant response."""
        summary: list[str] = []
        for block in response.content:
            if isinstance(block, TextContent):
                summary.append(f"Text({len(block.text)}ch)")
            elif isinstance(block, ThinkingContent):
                summary.append(f"Thinking({len(block.thinking)}ch)")
            elif isinstance(block, ToolCallContent):
                summary.append(f"ToolCall({block.name}, id={block.id[:12]})")
            else:
                summary.append(f"Unknown({type(block).__name__})")
        return summary

    @staticmethod
    def _stream_safe_tool_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        """Strip heavy fields (e.g. base64 blobs) from live WS tool_result events."""
        if not metadata:
            return {}
        safe = {
            key: value
            for key, value in metadata.items()
            if not str(key).startswith("__")
        }
        attachments = safe.get("attachments")
        if isinstance(attachments, list):
            safe["attachment_count"] = len(attachments)
            safe["attachments"] = [
                {"path": str(item.get("path", ""))}
                for item in attachments
                if isinstance(item, dict)
            ]
        return safe

    async def _grace_analysis(
        self,
        messages: list[AgentMessage],
        session_id: UUID,
        effective_max: int,
        grace_extension: int,
    ) -> bool:
        """
        Analyse the tail of the conversation to decide if a grace extension should be granted.
        Returns True only when the LLM responds with {"continue": true}.
        Any exception, timeout, or malformed JSON causes strict rejection (False).
        """
        raw = ""
        block_summary: list[str] = []
        try:
            # Collect last ~20 messages and build a compact summary
            tail = messages[-20:] if len(messages) > 20 else messages[:]
            summary_lines: list[str] = []
            for msg in tail:
                if isinstance(msg, AssistantMessage):
                    text_parts = [b.text for b in msg.content if isinstance(b, TextContent) and b.text]
                    tool_calls = [
                        f"{b.name}({json.dumps(b.arguments)[:200]})"
                        for b in msg.content
                        if isinstance(b, ToolCallContent)
                    ]
                    if text_parts:
                        summary_lines.append(f"assistant: {' '.join(text_parts)[:300]}")
                    for tc in tool_calls:
                        summary_lines.append(f"tool_call: {tc}")
                elif isinstance(msg, ToolResultMessage):
                    snippet = (msg.content or "")[:300]
                    status = "error" if msg.is_error else "ok"
                    summary_lines.append(f"tool_result({status}): {snippet}")

            tail_text = "\n".join(summary_lines[-30:])  # cap at 30 lines
            analysis_messages: list[AgentMessage] = [
                UserMessage(
                    content=(
                        f"An AI agent has reached its iteration limit ({effective_max} steps). "
                        f"You must decide whether to grant a grace extension of {grace_extension} additional iterations "
                        "so it can complete its current task.\n\n"
                        f"Session: {session_id}\n\n"
                        "## Recent conversation tail (last ~10 exchanges)\n"
                        f"{tail_text}\n\n"
                        "## Decision criteria\n"
                        f'{{"continue": true}}  — the agent is clearly making meaningful progress toward a concrete goal '
                        f"and {grace_extension} more iterations would likely yield a useful, complete result.\n"
                        '{{"continue": false}} — the agent is stuck in a loop, repeating the same tool calls, '
                        "hallucinating, or has already produced a reasonable final answer.\n\n"
                        "Respond ONLY with valid JSON (no markdown fences, no explanation). "
                        "Err on the side of false when uncertain."
                    )
                )
            ]

            response = await asyncio.wait_for(
                self.provider.chat(analysis_messages, model=TierName.FAST.value, tools=[], temperature=0.0),
                timeout=15.0,
            )
            block_summary = self._summarize_response_blocks(response)
            raw = self._assistant_text(response).strip()
            # Strip markdown fences just in case
            if raw.startswith("```"):
                raw = raw.split("```")[1] if "```" in raw[3:] else raw
                raw = raw.lstrip("json").strip()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                logger.warning(
                    "Grace analysis: unexpected JSON type: session_id=%s raw=%r blocks=%s",
                    session_id,
                    raw[:1200],
                    block_summary,
                )
                return False
            decision = parsed.get("continue")
            if not isinstance(decision, bool):
                logger.warning(
                    "Grace analysis: missing bool 'continue': session_id=%s raw=%r blocks=%s",
                    session_id,
                    raw[:1200],
                    block_summary,
                )
                return False
            logger.info("Grace analysis decision=%s reason=%r session_id=%s", decision, parsed.get("reason", ""), session_id)
            return decision
        except json.JSONDecodeError:
            logger.warning(
                "Grace analysis invalid JSON (strict reject): session_id=%s raw=%r blocks=%s",
                session_id,
                raw[:1200],
                block_summary,
                exc_info=True,
            )
            return False
        except Exception:  # noqa: BLE001
            logger.warning(
                "Grace analysis failed (strict reject): session_id=%s raw=%r blocks=%s",
                session_id,
                raw[:1200],
                block_summary,
                exc_info=True,
            )
            return False
