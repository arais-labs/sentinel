"""Sentinel adapter around the runtime-native engine."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.sentral import (
    AgentRuntimeEngine,
    AgentEvent as RuntimeAgentEvent,
    CompactionConfig,
    CompactionResult,
    Compactor,
    ConversationItem,
    GenerationConfig,
    ImageBlock,
    RunTurnRequest,
    Runtime,
    TextBlock,
    TokenUsage,
    ToolResultBlock,
    TurnResult,
)
from app.config import settings
from app.models import Message
from app.services.agent.agent_modes import get_agent_mode_definition
from app.services.agent.runtime_support import SentinelRuntimeSupport
from app.services.estop import EstopLevel
from app.services.agent_runtime_adapters.conversions import (
    db_messages_to_runtime_items,
    runtime_item_to_sentinel_message,
    sentinel_message_to_runtime_item,
)
from app.services.agent_runtime_adapters.provider import SentinelProviderAdapter
from app.services.agent_runtime_adapters.tools import SentinelToolRegistryAdapter
from app.services.llm.generic.types import ImageContent, TextContent, UserMessage


HistoryLoader = Callable[[AsyncSession, UUID], Awaitable[list[ConversationItem]]]


class SentinelLoopRuntimeAdapter(Runtime):
    """Expose Sentinel execution through the standalone runtime contracts.

    This adapter is intentionally Sentinel-specific:
    - conversation history is backed by Sentinel messages
    - persistence remains in Sentinel
    - transports are outside
    - execution runs through the sentral AgentRuntimeEngine
    """

    def __init__(
        self,
        *,
        loop: SentinelRuntimeSupport,
        db: AsyncSession,
        session_id: UUID,
        compactor: Compactor | None = None,
        history_loader: HistoryLoader | None = None,
        persist_incremental: bool = False,
    ) -> None:
        self._loop = loop
        self._db = db
        self._session_id = session_id
        self._compactor = compactor
        self._history_loader = history_loader or self._load_runtime_history
        self._persist_incremental = persist_incremental

    async def run_turn(
        self,
        request: RunTurnRequest,
        *,
        sink: Callable[[RuntimeAgentEvent], Awaitable[None]] | None = None,
    ) -> TurnResult:
        result, _events = await self._execute(request, sink=sink)
        return result

    async def stream_turn(
        self,
        request: RunTurnRequest,
    ) -> AsyncIterator[RuntimeAgentEvent]:
        queue: asyncio.Queue[RuntimeAgentEvent | None] = asyncio.Queue()
        terminal_error: Exception | None = None

        async def _sink(event: RuntimeAgentEvent) -> None:
            await queue.put(event)

        async def _run() -> None:
            nonlocal terminal_error
            try:
                await self._execute(request, sink=_sink)
            except Exception as exc:  # noqa: BLE001
                terminal_error = exc
            finally:
                await queue.put(None)

        task = asyncio.create_task(_run())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            if terminal_error is not None:
                raise terminal_error
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def compact(
        self,
        *,
        history: list[ConversationItem],
        config: CompactionConfig,
    ) -> CompactionResult:
        if self._compactor is None:
            raise NotImplementedError("No compactor is configured for SentinelLoopRuntimeAdapter.")
        return await self._compactor.compact(history=history, config=config)

    async def _execute(
        self,
        request: RunTurnRequest,
        *,
        sink: Callable[[RuntimeAgentEvent], Awaitable[None]] | None = None,
    ) -> tuple[TurnResult, list[RuntimeAgentEvent]]:
        self._validate_request(request)
        config = request.config
        assert config is not None

        events: list[RuntimeAgentEvent] = []
        last_stop_reason: str | None = None
        pending_approval = None
        deferred_done: RuntimeAgentEvent | None = None
        # Track most-recent tool_call_id per tool name so the pending callback
        # can emit a pending tool_result event with the correct call ID.
        tool_call_ids_by_name: dict[str, str] = {}

        async def _emit_runtime_event(runtime_event: RuntimeAgentEvent) -> None:
            nonlocal last_stop_reason, pending_approval, deferred_done
            if runtime_event.stop_reason:
                last_stop_reason = runtime_event.stop_reason
            if runtime_event.approval_request is not None:
                pending_approval = runtime_event.approval_request
            if runtime_event.type == "toolcall_start" and runtime_event.tool_call:
                tool_call_ids_by_name[runtime_event.tool_call.name] = runtime_event.tool_call.id
            if config.stream and runtime_event.type == "done" and runtime_event.stop_reason != "tool_use":
                deferred_done = runtime_event
                return
            events.append(runtime_event)
            if sink is not None:
                await sink(runtime_event)

        async def _on_pending_tool_result(
            tool_name: str,
            tool_args: dict,
            approval_payload: dict,
        ) -> None:
            """Emit a pending tool_result WS event immediately when approval is requested.

            This makes the streaming card turn rose (pending state) in real-time
            instead of waiting until the approval is resolved.
            """
            import json as _json
            tool_call_id = tool_call_ids_by_name.get(tool_name, "")
            pending_metadata: dict = {"approval": approval_payload, "pending": True}
            content = _json.dumps({
                "status": "pending",
                "message": approval_payload.get("description") or "Waiting for approval.",
                "approval": approval_payload,
            })
            pending_block = ToolResultBlock(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=content,
                is_error=False,
                metadata=pending_metadata,
                tool_arguments=tool_args,
            )
            await _emit_runtime_event(RuntimeAgentEvent(type="tool_result", tool_result=pending_block))

        mode_definition = get_agent_mode_definition(config.provider_metadata.get("agent_mode"))
        user_metadata = (
            dict(config.provider_metadata.get("user_metadata"))
            if isinstance(config.provider_metadata.get("user_metadata"), dict)
            else {}
        )
        user_metadata["agent_mode"] = mode_definition.id.value
        user_payload = self._request_user_message(request)
        persist_user_message = bool(config.provider_metadata.get("persist_user_message", True))
        user_message = UserMessage(content=user_payload, metadata=user_metadata)
        created_seed = [user_message] if persist_user_message else []

        if await self._loop.estop_level(self._db) == EstopLevel.KILL_ALL:
            await self._loop.persist_created_messages(
                self._db,
                self._session_id,
                created_seed,
                {},
                requested_tier=config.model,
                temperature=config.temperature,
                max_iterations=config.max_iterations,
            )
            await _emit_runtime_event(
                RuntimeAgentEvent(type="error", error="Emergency stop KILL_ALL is active")
            )
            await _emit_runtime_event(
                RuntimeAgentEvent(type="done", stop_reason="aborted")
            )
            history = await self._history_loader(self._db, self._session_id)
            final_item = self._last_assistant_item(history)
            return (
                TurnResult(
                    status="aborted",
                    history=history,
                    usage=TokenUsage(),
                    iterations=0,
                    final_item=final_item,
                    stop_reason="aborted",
                    error=None,
                    metadata={"messages_created": len(created_seed)},
                ),
                events,
            )

        prepared = await self._loop.prepare_runtime_turn_context(
            self._db,
            self._session_id,
            system_prompt=config.system_prompt,
            pending_user_message=SentinelRuntimeSupport.user_text(user_payload),
            agent_mode=mode_definition.id,
            model=config.model,
            temperature=config.temperature,
            max_iterations=config.max_iterations,
            stream=config.stream,
        )
        messages = prepared.messages
        runtime_system_prompt = prepared.effective_system_prompt
        runtime_context_snapshot = prepared.runtime_context_snapshot
        context_snapshot_pending = True
        timeout = request.timeout_seconds or settings.agent_loop_timeout
        persisted_count = 0
        history = [
            sentinel_message_to_runtime_item(message, item_id=f"history-{index}")
            for index, message in enumerate(messages)
        ]

        async def _persist_runtime_batch(batch: list[ConversationItem]) -> None:
            nonlocal context_snapshot_pending, persisted_count
            if not batch:
                return
            sentinel_batch = [runtime_item_to_sentinel_message(item) for item in batch]
            assistant_iterations_batch = {
                id(message): int(item.metadata.get("iteration") or 0)
                for item, message in zip(batch, sentinel_batch, strict=False)
                if getattr(item, "role", None) == "assistant"
            }
            snapshot = runtime_context_snapshot if context_snapshot_pending else None
            await self._loop.persist_created_messages(
                self._db,
                self._session_id,
                sentinel_batch,
                assistant_iterations_batch,
                requested_tier=config.model,
                temperature=config.temperature,
                max_iterations=config.max_iterations,
                effective_system_prompt=runtime_system_prompt,
                runtime_context_snapshot=snapshot,
            )
            if snapshot is not None:
                context_snapshot_pending = False
            persisted_count += len(batch)

        runtime = AgentRuntimeEngine(
            provider=SentinelProviderAdapter(self._loop.provider),
            tool_registry=SentinelToolRegistryAdapter(
                self._loop.tool_adapter.registry,
                self._loop.tool_adapter.executor,
                agent_mode=mode_definition.id,
                session_id=self._session_id,
                on_pending_tool_result=_on_pending_tool_result,
            ),
            compactor=self._compactor,
        )
        runtime_result = await runtime.run_turn(
            RunTurnRequest(
                conversation_id=request.conversation_id,
                history=history,
                new_items=request.new_items,
                config=GenerationConfig(
                    model=config.model,
                    temperature=config.temperature,
                    max_iterations=config.max_iterations,
                    stream=config.stream,
                    system_prompt=config.system_prompt,
                    max_output_tokens=config.max_output_tokens,
                    tool_choice=config.tool_choice,
                    provider_metadata=dict(config.provider_metadata),
                ),
                timeout_seconds=timeout,
                interjection_source=request.interjection_source,
            ),
            sink=_emit_runtime_event,
            checkpoint=_persist_runtime_batch if self._persist_incremental else None,
        )
        created_runtime_items = runtime_result.metadata.get("created_items")
        created_items = created_runtime_items if isinstance(created_runtime_items, list) else []
        all_sentinel_created = [runtime_item_to_sentinel_message(item) for item in created_items]
        remaining_created = created_items[persisted_count:]
        sentinel_created = [runtime_item_to_sentinel_message(item) for item in remaining_created]
        assistant_iterations = {
            id(message): int(item.metadata.get("iteration") or 0)
            for item, message in zip(remaining_created, sentinel_created, strict=False)
            if getattr(item, "role", None) == "assistant"
        }
        if sentinel_created:
            snapshot = runtime_context_snapshot if context_snapshot_pending else None
            await self._loop.persist_created_messages(
                self._db,
                self._session_id,
                sentinel_created,
                assistant_iterations,
                requested_tier=config.model,
                temperature=config.temperature,
                max_iterations=config.max_iterations,
                effective_system_prompt=runtime_system_prompt,
                runtime_context_snapshot=snapshot,
            )
            if snapshot is not None:
                context_snapshot_pending = False

        if deferred_done is not None:
            events.append(deferred_done)
            if sink is not None:
                await sink(deferred_done)
        elif runtime_result.stop_reason != "pending_approval":
            done = RuntimeAgentEvent(type="done", stop_reason="stop")
            events.append(done)
            if sink is not None:
                await sink(done)

        history = await self._history_loader(self._db, self._session_id)
        final_item = self._last_assistant_item(history)
        status = self._map_status(runtime_result.error, last_stop_reason, pending_approval)
        return (
            TurnResult(
                status=status,
                history=history,
                usage=runtime_result.usage,
                iterations=runtime_result.iterations,
                final_item=final_item,
                stop_reason=last_stop_reason,
                pending_approval=pending_approval,
                error=runtime_result.error,
                metadata={
                    "messages_created": len(created_items),
                    "final_text": self._loop.extract_final_text(all_sentinel_created),
                    "attachments": self._loop.collect_attachments(all_sentinel_created),
                },
            ),
            events,
        )

    def _validate_request(self, request: RunTurnRequest) -> None:
        if request.config is None:
            raise ValueError("RunTurnRequest.config is required.")
        if request.history is not None:
            raise NotImplementedError(
                "SentinelLoopRuntimeAdapter currently requires DB-backed Sentinel history; direct history injection is not supported yet."
            )
        if request.conversation_id is not None and request.conversation_id != str(self._session_id):
            raise ValueError("RunTurnRequest.conversation_id must match the bound Sentinel session.")
        if not request.new_items:
            raise ValueError("RunTurnRequest.new_items must contain at least one user item.")
        for item in request.new_items:
            if item.role != "user":
                raise ValueError("SentinelLoopRuntimeAdapter only accepts user items as new input.")
            for block in item.content:
                if not isinstance(block, (TextBlock, ImageBlock)):
                    raise ValueError("User input may only contain text and image blocks.")

    def _request_user_message(
        self,
        request: RunTurnRequest,
    ) -> str | list[TextContent | ImageContent]:
        blocks: list[TextContent | ImageContent] = []
        for item in request.new_items:
            for block in item.content:
                if isinstance(block, TextBlock):
                    blocks.append(TextContent(text=block.text))
                elif isinstance(block, ImageBlock):
                    blocks.append(ImageContent(media_type=block.media_type, data=block.data))
        if len(blocks) == 1 and isinstance(blocks[0], TextContent):
            return blocks[0].text
        return blocks

    async def _load_runtime_history(
        self,
        db: AsyncSession,
        session_id: UUID,
    ) -> list[ConversationItem]:
        result = await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
        )
        messages = list(result.scalars().all())
        return db_messages_to_runtime_items(messages)

    @staticmethod
    def _last_assistant_item(
        history: list[ConversationItem],
    ) -> ConversationItem | None:
        for item in reversed(history):
            if item.role == "assistant":
                return item
        return None

    @staticmethod
    def _map_status(
        error: str | None,
        stop_reason: str | None,
        pending_approval: object,
    ) -> str:
        if pending_approval is not None:
            return "pending_approval"
        if stop_reason == "timeout":
            return "timeout"
        if stop_reason == "aborted":
            return "aborted"
        if error:
            return "error"
        return "completed"
