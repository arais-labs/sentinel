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
from app.models import Message
from app.services.agent.context_builder import ContextBuilder
from app.services.agent.tool_adapter import ToolAdapter
from app.services.estop import EstopLevel, EstopService
from app.services.llm.base import LLMProvider
from app.services.llm.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)


logger = logging.getLogger(__name__)


def _humanize_error(raw: str) -> str:
    """Return a user-friendly error message for common LLM failures."""
    lower = raw.lower()
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
    if "all providers failed" in lower:
        return "All AI providers failed. Please check your API keys in Settings."
    return raw[:300] if len(raw) > 300 else raw


def _make_error_message(error_text: str) -> AssistantMessage:
    """Create an AssistantMessage that will be persisted and shown in the chat."""
    return AssistantMessage(
        content=[TextContent(text=f"⚠️ **Error:** {error_text}")],
        stop_reason="error",
    )


@dataclass(slots=True)
class AgentLoopResult:
    final_text: str
    messages_created: int
    usage: TokenUsage
    iterations: int
    error: str | None = None
    attachments: list[dict[str, str]] = field(default_factory=list)


class AgentLoop:
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

    async def run(
        self,
        db: AsyncSession,
        session_id: UUID,
        user_message: str,
        *,
        system_prompt: str | None = None,
        max_iterations: int = 50,
        temperature: float = 0.7,
        model: str = "hint:reasoning",
        allow_high_risk: bool = False,
        persist_user_message: bool = True,
        stream: bool = True,
        timeout_seconds: float | None = None,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
        inject_queue: asyncio.Queue[str] | None = None,
    ) -> AgentLoopResult:
        if timeout_seconds is None:
            timeout_seconds = settings.agent_loop_timeout
        user = UserMessage(content=user_message)
        created: list[AgentMessage] = []
        assistant_iterations: dict[int, int] = {}
        if persist_user_message:
            created.append(user)

        if await self._estop.check_level(db) == EstopLevel.KILL_ALL:
            if on_event is not None:
                await on_event(AgentEvent(type="error", error="Emergency stop KILL_ALL is active"))
                await on_event(AgentEvent(type="done", stop_reason="aborted"))
            await self._persist_messages(db, session_id, created, assistant_iterations)
            return AgentLoopResult(
                final_text="",
                messages_created=len(created),
                usage=TokenUsage(),
                iterations=0,
                attachments=[],
            )

        messages = await self.context_builder.build(
            db,
            session_id,
            system_prompt,
            pending_user_message=user_message,
        )
        messages.append(user)

        tools = self.tool_adapter.get_tool_schemas()
        logger.info(
            "AgentLoop.run: session_id=%s model=%s stream=%s tools=%s",
            session_id, model, stream, [t.name for t in tools],
        )
        total_usage = TokenUsage()
        iterations = 0
        done_emitted = False
        last_error: str | None = None

        # Defer the final "done" event until AFTER _persist_messages commits, so the
        # frontend's loadMessages() HTTP call sees the persisted messages in the DB.
        # Intermediate "done" events (stop_reason="tool_use") pass through immediately.
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

        async def _run_iterations() -> None:
            nonlocal iterations, done_emitted, last_error
            grace_check_done = False
            effective_max = max(1, max_iterations)
            # Grace extension: 25% of original budget, min 10 extra iterations
            grace_extension = max(10, effective_max // 4)
            total_limit = effective_max + grace_extension

            for index in range(total_limit):
                # At the hard boundary: run grace analysis once
                if index == effective_max and not grace_check_done:
                    grace_check_done = True
                    grace_granted = await self._grace_analysis(messages, session_id, effective_max, grace_extension)
                    if not grace_granted:
                        if on_event is not None:
                            await on_event(AgentEvent(type="done", stop_reason="length"))
                        done_emitted = True
                        break
                    logger.info(
                        "Grace extension granted (+%d iterations): session_id=%s",
                        grace_extension,
                        session_id,
                    )
                    # Fall through — continue running up to total_limit

                iterations = index + 1
                if on_event is not None:
                    # During grace period, report against total_limit so the bar shows continued progress
                    progress_max = total_limit if grace_check_done else effective_max
                    await on_event(AgentEvent(type="agent_progress", iteration=min(iterations, progress_max), max_iterations=progress_max))
                # Drain injected messages from external callers
                if inject_queue is not None:
                    while not inject_queue.empty():
                        try:
                            injected_text = inject_queue.get_nowait()
                            injected = UserMessage(content=f"[Operator interjection]: {injected_text}")
                            messages.append(injected)
                            created.append(injected)
                        except asyncio.QueueEmpty:
                            break
                try:
                    if stream:
                        partial_out: list[AssistantMessage] = []
                        try:
                            response = await self._stream_response(
                                messages,
                                model=model,
                                tools=tools,
                                temperature=temperature,
                                on_event=on_event,
                                partial_out=partial_out,
                            )
                        except asyncio.CancelledError:
                            # Save partial content before propagating
                            if partial_out:
                                created.append(partial_out[0])
                                assistant_iterations[id(partial_out[0])] = iterations
                            raise
                    else:
                        response = await self.provider.chat(messages, model=model, tools=tools, temperature=temperature)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logger.error("LLM call failed: session_id=%s iteration=%d error=%s", session_id, iterations, last_error)
                    # Persist a visible error message in the chat
                    error_msg = _make_error_message(_humanize_error(last_error))
                    created.append(error_msg)
                    assistant_iterations[id(error_msg)] = iterations
                    if on_event is not None:
                        await on_event(AgentEvent(type="error", error=last_error))
                        await on_event(AgentEvent(type="done", stop_reason="error"))
                    done_emitted = True
                    break

                total_usage.input_tokens += response.usage.input_tokens
                total_usage.output_tokens += response.usage.output_tokens

                messages.append(response)
                created.append(response)
                assistant_iterations[id(response)] = iterations

                # --- DEBUG: log assembled response ---
                block_summary = []
                for block in response.content:
                    if isinstance(block, TextContent):
                        block_summary.append(f"Text({len(block.text)}ch)")
                    elif isinstance(block, ThinkingContent):
                        block_summary.append(f"Thinking({len(block.thinking)}ch)")
                    elif isinstance(block, ToolCallContent):
                        block_summary.append(f"ToolCall({block.name}, id={block.id[:12]})")
                    else:
                        block_summary.append(f"Unknown({type(block).__name__})")
                logger.info(
                    "Loop iter=%d stop_reason=%r blocks=[%s] session_id=%s",
                    iterations, response.stop_reason, ", ".join(block_summary), session_id,
                )
                # --- END DEBUG ---

                if not stream:
                    for block in response.content:
                        if isinstance(block, TextContent) and block.text and on_event is not None:
                            await on_event(AgentEvent(type="text_delta", delta=block.text))

                if response.stop_reason != "tool_use":
                    logger.info(
                        "Loop ending: stop_reason=%r != 'tool_use', session_id=%s",
                        response.stop_reason, session_id,
                    )
                    if on_event is not None and not stream:
                        await on_event(AgentEvent(type="done", stop_reason=response.stop_reason, message=response))
                    done_emitted = True
                    break

                tool_calls = [item for item in response.content if isinstance(item, ToolCallContent)]
                if not tool_calls:
                    logger.warning(
                        "Loop: stop_reason='tool_use' but NO ToolCallContent found! session_id=%s",
                        session_id,
                    )
                    if on_event is not None and not stream:
                        await on_event(AgentEvent(type="done", stop_reason="stop", message=response))
                    done_emitted = True
                    break

                logger.info(
                    "Loop executing %d tool(s): %s session_id=%s",
                    len(tool_calls),
                    [tc.name for tc in tool_calls],
                    session_id,
                )

                for tool_call in tool_calls:
                    if on_event is not None and not stream:
                        await on_event(AgentEvent(type="toolcall_start", tool_call=tool_call))

                tool_results = await self.tool_adapter.execute_tool_calls(
                    tool_calls,
                    db,
                    session_id=session_id,
                    allow_high_risk=allow_high_risk,
                )
                messages.extend(tool_results)
                created.extend(tool_results)

                for tool_result in tool_results:
                    if on_event is not None and not stream:
                        await on_event(
                            AgentEvent(
                                type="toolcall_end",
                                tool_call=ToolCallContent(
                                    id=tool_result.tool_call_id,
                                    name=tool_result.tool_name,
                                    arguments={},
                                ),
                            )
                        )
                # Cooldown between iterations to avoid hammering the LLM provider
                if settings.agent_loop_cooldown > 0:
                    await asyncio.sleep(settings.agent_loop_cooldown)
            else:
                # Exhausted all iterations (including grace extension) — hard stop
                if on_event is not None:
                    await on_event(AgentEvent(type="done", stop_reason="length"))
                done_emitted = True

        persisted = False
        try:
            await asyncio.wait_for(_run_iterations(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            last_error = f"Agent loop timed out after {timeout_seconds}s"
            logger.error("Agent loop timeout: session_id=%s timeout=%s", session_id, timeout_seconds)
            error_msg = _make_error_message(f"Agent timed out after {int(timeout_seconds)}s. You can retry your request.")
            created.append(error_msg)
            assistant_iterations[id(error_msg)] = iterations
            await self._persist_messages(db, session_id, created, assistant_iterations)
            persisted = True
            if on_event is not None:
                await on_event(AgentEvent(type="done", stop_reason="timeout"))
            done_emitted = True
        except asyncio.CancelledError:
            last_error = "Generation stopped by user"
            logger.info("Agent loop cancelled: session_id=%s", session_id)
            error_msg = _make_error_message("Generation stopped by user.")
            created.append(error_msg)
            assistant_iterations[id(error_msg)] = iterations
            await self._persist_messages(db, session_id, created, assistant_iterations)
            persisted = True
            if on_event is not None:
                await on_event(AgentEvent(type="error", error=last_error))
                await on_event(AgentEvent(type="done", stop_reason="aborted"))
            done_emitted = True

        # Persist before emitting the final done so loadMessages() sees committed data.
        if not persisted:
            await self._persist_messages(db, session_id, created, assistant_iterations)

        if deferred_done is not None and _caller_on_event is not None:
            await _caller_on_event(deferred_done)
        elif not done_emitted and _caller_on_event is not None:
            await _caller_on_event(AgentEvent(type="done", stop_reason="stop"))

        return AgentLoopResult(
            final_text=self._extract_final_text(created),
            messages_created=len(created),
            usage=total_usage,
            iterations=iterations,
            error=last_error,
            attachments=self._collect_attachments(created),
        )

    async def _stream_response(
        self,
        messages: list[AgentMessage],
        *,
        model: str,
        tools: list[ToolSchema],
        temperature: float,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None,
        partial_out: list[AssistantMessage] | None = None,
    ) -> AssistantMessage:
        streamed_events: list[AgentEvent] = []
        seen_done = False

        try:
            async for event in self.provider.stream(messages, model=model, tools=tools, temperature=temperature):
                streamed_events.append(event)
                if on_event is not None:
                    await on_event(event)
                if event.type == "done":
                    seen_done = True
                    break
        except asyncio.CancelledError:
            # Assemble whatever partial content was streamed before cancellation
            if partial_out is not None and streamed_events:
                partial_out.append(
                    self._assemble_message_from_events(
                        streamed_events,
                        fallback_model=model,
                        fallback_provider=self.provider.name,
                    )
                )
            raise

        if not seen_done:
            streamed_events.append(AgentEvent(type="done", stop_reason="stop"))
            if on_event is not None:
                await on_event(streamed_events[-1])

        return self._assemble_message_from_events(
            streamed_events,
            fallback_model=model,
            fallback_provider=self.provider.name,
        )

    def _assemble_message_from_events(
        self,
        events: list[AgentEvent],
        *,
        fallback_model: str,
        fallback_provider: str,
    ) -> AssistantMessage:
        block_types: dict[int, str] = {}
        text_blocks: dict[int, list[str]] = {}
        thinking_blocks: dict[int, list[str]] = {}
        thinking_signatures: dict[int, list[str]] = {}
        tool_calls: dict[int, dict[str, Any]] = {}
        usage = TokenUsage()
        model = fallback_model
        provider = fallback_provider
        stop_reason = "stop"

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
                block_types[idx] = "text"
                text_blocks.setdefault(idx, [])
            elif event.type == "text_delta":
                block_types.setdefault(idx, "text")
                text_blocks.setdefault(idx, []).append(event.delta or "")
            elif event.type == "thinking_start":
                block_types[idx] = "thinking"
                thinking_blocks.setdefault(idx, [])
            elif event.type == "thinking_delta":
                block_types.setdefault(idx, "thinking")
                thinking_blocks.setdefault(idx, []).append(event.delta or "")
                if event.signature:
                    thinking_signatures.setdefault(idx, []).append(event.signature)
            elif event.type == "toolcall_start":
                block_types[idx] = "tool_call"
                tool_call = event.tool_call or ToolCallContent()
                tool_calls[idx] = {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arg_deltas": [],
                    "base_args": tool_call.arguments,
                }
            elif event.type == "toolcall_delta":
                block_types.setdefault(idx, "tool_call")
                call_state = tool_calls.setdefault(
                    idx,
                    {"id": "", "name": "", "arg_deltas": [], "base_args": {}},
                )
                call_state["arg_deltas"].append(event.delta or "")
            elif event.type == "error":
                stop_reason = "error"
            elif event.type == "done":
                stop_reason = event.stop_reason or stop_reason

        # --- DEBUG: log what we collected from stream ---
        logger.debug(
            "Assemble: block_types=%s tool_calls_keys=%s stop_reason=%s",
            {idx: bt for idx, bt in block_types.items()},
            list(tool_calls.keys()),
            stop_reason,
        )
        # --- END DEBUG ---

        content: list[TextContent | ThinkingContent | ToolCallContent] = []
        for idx in sorted(block_types.keys()):
            block_type = block_types[idx]
            if block_type == "text":
                content.append(TextContent(text="".join(text_blocks.get(idx, []))))
                continue
            if block_type == "thinking":
                sig_parts = thinking_signatures.get(idx, [])
                sig = "".join(sig_parts) if sig_parts else None
                content.append(ThinkingContent(
                    thinking="".join(thinking_blocks.get(idx, [])),
                    signature=sig,
                ))
                continue
            if block_type == "tool_call":
                call_state = tool_calls.get(idx, {"id": "", "name": "", "arg_deltas": [], "base_args": {}})
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
                    )
                )

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
    ) -> None:
        base_time = datetime.now(UTC)
        for idx, message in enumerate(created):
            created_at = base_time + timedelta(milliseconds=idx)
            if isinstance(message, UserMessage):
                record = Message(
                    session_id=session_id,
                    role="user",
                    content=message.content if isinstance(message.content, str) else "",
                    metadata_json={},
                    created_at=created_at,
                )
                db.add(record)
                continue

            if isinstance(message, AssistantMessage):
                text = self._assistant_text(message)
                tool_calls_data = [
                    {"id": block.id, "name": block.name, "arguments": block.arguments}
                    for block in message.content
                    if isinstance(block, ToolCallContent)
                ]
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
                record = Message(
                    session_id=session_id,
                    role="assistant",
                    content=text,
                    metadata_json=metadata,
                    token_count=message.usage.output_tokens,
                    created_at=created_at,
                )
                db.add(record)
                continue

            if isinstance(message, ToolResultMessage):
                metadata = {"is_error": message.is_error}
                if message.metadata:
                    metadata.update(message.metadata)
                record = Message(
                    session_id=session_id,
                    role="tool_result",
                    content=message.content,
                    metadata_json=metadata,
                    tool_call_id=message.tool_call_id or None,
                    tool_name=message.tool_name or None,
                    created_at=created_at,
                )
                db.add(record)

        await db.commit()

    def _extract_final_text(self, messages: list[AgentMessage]) -> str:
        for message in reversed(messages):
            if not isinstance(message, AssistantMessage):
                continue
            text = self._assistant_text(message).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _collect_attachments(messages: list[AgentMessage]) -> list[dict[str, str]]:
        """Gather image attachments from all ToolResultMessage metadata."""
        attachments: list[dict[str, str]] = []
        for msg in messages:
            if isinstance(msg, ToolResultMessage) and msg.metadata:
                for att in msg.metadata.get("attachments", []):
                    if isinstance(att, dict) and "base64" in att:
                        attachments.append(att)
        return attachments

    def _assistant_text(self, message: AssistantMessage) -> str:
        parts = [block.text for block in message.content if isinstance(block, TextContent) and block.text]
        return "\n".join(parts)

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
                self.provider.chat(analysis_messages, model="hint:fast", tools=[], temperature=0.0),
                timeout=15.0,
            )
            raw = self._assistant_text(response).strip()
            # Strip markdown fences just in case
            if raw.startswith("```"):
                raw = raw.split("```")[1] if "```" in raw[3:] else raw
                raw = raw.lstrip("json").strip()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                logger.warning("Grace analysis: unexpected JSON type: session_id=%s raw=%r", session_id, raw)
                return False
            decision = parsed.get("continue")
            if not isinstance(decision, bool):
                logger.warning("Grace analysis: missing bool 'continue': session_id=%s raw=%r", session_id, raw)
                return False
            logger.info("Grace analysis decision=%s reason=%r session_id=%s", decision, parsed.get("reason", ""), session_id)
            return decision
        except Exception:  # noqa: BLE001
            logger.warning("Grace analysis failed (strict reject): session_id=%s", session_id, exc_info=True)
            return False
