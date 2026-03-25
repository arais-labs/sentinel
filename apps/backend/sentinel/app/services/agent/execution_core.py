"""Pure iterative agent execution over prepared messages.

This module intentionally excludes Sentinel-specific context loading and
message persistence. Those concerns stay in ``AgentLoop``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agent.agent_modes import AgentMode
from app.services.agent.tool_adapter import ToolAdapter
from app.services.agent.tool_adapter import ToolExecutionCancelled
from app.services.agent.tool_image_reinjection import (
    ToolImageReinjectionPolicy,
    build_tool_image_reinjection_messages,
)
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    TextContent,
    TokenUsage,
    ToolCallContent,
    ToolResultContent,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)


logger = logging.getLogger(__name__)


CheckpointCallback = Callable[
    [list[AgentMessage], dict[int, int]],
    Awaitable[None],
]
StreamResponseCallback = Callable[..., Awaitable[AssistantMessage]]
GraceAnalysisCallback = Callable[[list[AgentMessage], UUID, int, int], Awaitable[bool]]
ToolMetadataCallback = Callable[[dict[str, Any] | None], dict[str, Any]]
ResponseSummaryCallback = Callable[[AssistantMessage], list[str]]
ErrorMessageFactory = Callable[[str], AssistantMessage]
ErrorHumanizer = Callable[[str], str]


@dataclass(slots=True)
class AgentExecutionArtifacts:
    """Artifacts produced by one execution run over prepared messages."""

    created: list[AgentMessage]
    assistant_iterations: dict[int, int]
    usage: TokenUsage
    iterations: int
    done_emitted: bool
    error: str | None = None


class AgentExecutionCore:
    """Run iterative think/act cycles without loading or persisting history."""

    def __init__(
        self,
        *,
        provider: LLMProvider,
        tool_adapter: ToolAdapter,
        stream_response: StreamResponseCallback,
        grace_analysis: GraceAnalysisCallback,
        stream_safe_tool_metadata: ToolMetadataCallback,
        summarize_response_blocks: ResponseSummaryCallback,
        make_error_message: ErrorMessageFactory,
        humanize_error: ErrorHumanizer,
    ) -> None:
        self._provider = provider
        self._tool_adapter = tool_adapter
        self._stream_response = stream_response
        self._grace_analysis = grace_analysis
        self._stream_safe_tool_metadata = stream_safe_tool_metadata
        self._summarize_response_blocks = summarize_response_blocks
        self._make_error_message = make_error_message
        self._humanize_error = humanize_error

    async def execute(
        self,
        *,
        db: AsyncSession,
        session_id: UUID,
        messages: list[AgentMessage],
        created_seed: list[AgentMessage],
        tools: list[ToolSchema],
        model: str,
        temperature: float,
        max_iterations: int,
        stream: bool,
        timeout_seconds: float,
        agent_mode: AgentMode | str,
        reinjection_policy: ToolImageReinjectionPolicy,
        cooldown_seconds: float,
        on_event: Callable[[AgentEvent], Awaitable[None]] | None = None,
        inject_queue: asyncio.Queue[str] | None = None,
        on_checkpoint: CheckpointCallback | None = None,
    ) -> AgentExecutionArtifacts:
        working_messages = list(messages)
        created = list(created_seed)
        assistant_iterations: dict[int, int] = {}
        total_usage = TokenUsage()
        iterations = 0
        done_emitted = False
        last_error: str | None = None
        reinjected_hashes: set[str] = set()
        checkpointed_count = 0

        async def _checkpoint_new_messages() -> None:
            nonlocal checkpointed_count
            if on_checkpoint is None or checkpointed_count >= len(created):
                return
            batch = created[checkpointed_count:]
            await on_checkpoint(batch, assistant_iterations)
            checkpointed_count = len(created)

        async def _run_finalization_round(progress_max: int) -> bool:
            nonlocal iterations, done_emitted, last_error
            iterations += 1
            if on_event is not None:
                await on_event(
                    AgentEvent(
                        type="agent_progress",
                        iteration=iterations,
                        max_iterations=max(progress_max + 1, iterations),
                    )
                )

            final_instruction = (
                "You've reached the step limit, and this is the final reply for this run. "
                "Do not call any tools. "
                "Write a natural, user-facing update (not robotic or templated). "
                "If the task is unfinished, briefly cover: what was completed, what is blocked/uncertain, "
                "and the single best next step to continue."
            )
            final_messages = [*working_messages, UserMessage(content=final_instruction)]

            try:
                if stream:
                    final_response = await self._stream_response(
                        final_messages,
                        model=model,
                        tools=[],
                        temperature=temperature,
                        on_event=on_event,
                    )
                else:
                    final_response = await self._provider.chat(
                        final_messages,
                        model=model,
                        tools=[],
                        temperature=temperature,
                    )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                logger.error(
                    "Finalization round failed: session_id=%s iteration=%d error=%s",
                    session_id,
                    iterations,
                    last_error,
                )
                if on_event is not None:
                    await on_event(AgentEvent(type="error", error=last_error))
                    await on_event(AgentEvent(type="done", stop_reason="length"))
                done_emitted = True
                return False

            if final_response.stop_reason == "tool_use":
                text_blocks = [
                    block
                    for block in final_response.content
                    if isinstance(block, TextContent)
                ]
                if not text_blocks:
                    text_blocks = [
                        TextContent(
                            text=(
                                "Reached the iteration limit. I could not complete all steps in time. "
                                "I can continue from the current state on your next message."
                            )
                        )
                    ]
                final_response = AssistantMessage(
                    content=text_blocks,
                    model=final_response.model,
                    provider=final_response.provider,
                    usage=final_response.usage,
                    stop_reason="stop",
                )

            if not stream and on_event is not None:
                for block in final_response.content:
                    if isinstance(block, TextContent) and block.text:
                        await on_event(AgentEvent(type="text_delta", delta=block.text))
                await on_event(
                    AgentEvent(
                        type="done",
                        stop_reason=final_response.stop_reason,
                        message=final_response,
                    )
                )

            total_usage.input_tokens += final_response.usage.input_tokens
            total_usage.output_tokens += final_response.usage.output_tokens
            working_messages.append(final_response)
            created.append(final_response)
            assistant_iterations[id(final_response)] = iterations
            await _checkpoint_new_messages()
            done_emitted = True
            return True

        async def _run_iterations() -> None:
            nonlocal iterations, done_emitted, last_error
            grace_check_done = False
            effective_max = max(1, max_iterations)
            grace_extension = max(10, effective_max // 4)
            total_limit = effective_max + grace_extension

            for index in range(total_limit):
                if index == effective_max and not grace_check_done:
                    grace_check_done = True
                    grace_granted = await self._grace_analysis(
                        working_messages,
                        session_id,
                        effective_max,
                        grace_extension,
                    )
                    if not grace_granted:
                        await _run_finalization_round(effective_max)
                        break
                    logger.info(
                        "Grace extension granted (+%d iterations): session_id=%s",
                        grace_extension,
                        session_id,
                    )

                iterations = index + 1
                if on_event is not None:
                    progress_max = total_limit if grace_check_done else effective_max
                    await on_event(
                        AgentEvent(
                            type="agent_progress",
                            iteration=min(iterations, progress_max),
                            max_iterations=progress_max,
                        )
                    )

                if inject_queue is not None:
                    while not inject_queue.empty():
                        try:
                            injected_text = inject_queue.get_nowait()
                            injected = UserMessage(content=f"[Operator interjection]: {injected_text}")
                            working_messages.append(injected)
                            created.append(injected)
                        except asyncio.QueueEmpty:
                            break

                try:
                    streamed_tool_call_ids: set[str] = set()
                    if stream:
                        partial_out: list[AssistantMessage] = []
                        event_sink = on_event
                        if on_event is not None:
                            async def _stream_event_sink(event: AgentEvent) -> None:
                                tool_call = event.tool_call
                                if (
                                    event.type == "toolcall_start"
                                    and tool_call is not None
                                    and isinstance(tool_call.id, str)
                                    and tool_call.id
                                ):
                                    streamed_tool_call_ids.add(tool_call.id)
                                await on_event(event)

                            event_sink = _stream_event_sink
                        try:
                            response = await self._stream_response(
                                working_messages,
                                model=model,
                                tools=tools,
                                temperature=temperature,
                                on_event=event_sink,
                                partial_out=partial_out,
                            )
                        except asyncio.CancelledError:
                            if partial_out:
                                created.append(partial_out[0])
                                assistant_iterations[id(partial_out[0])] = iterations
                            raise
                    else:
                        response = await self._provider.chat(
                            working_messages,
                            model=model,
                            tools=tools,
                            temperature=temperature,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logger.error(
                        "LLM call failed: session_id=%s iteration=%d error=%s",
                        session_id,
                        iterations,
                        last_error,
                    )
                    error_msg = self._make_error_message(self._humanize_error(last_error))
                    created.append(error_msg)
                    assistant_iterations[id(error_msg)] = iterations
                    if on_event is not None:
                        await on_event(AgentEvent(type="error", error=last_error))
                        await on_event(AgentEvent(type="done", stop_reason="error"))
                    done_emitted = True
                    break

                total_usage.input_tokens += response.usage.input_tokens
                total_usage.output_tokens += response.usage.output_tokens

                working_messages.append(response)
                created.append(response)
                assistant_iterations[id(response)] = iterations

                block_summary = self._summarize_response_blocks(response)
                logger.info(
                    "Loop iter=%d stop_reason=%r blocks=[%s] session_id=%s",
                    iterations,
                    response.stop_reason,
                    ", ".join(block_summary),
                    session_id,
                )

                if not stream:
                    for block in response.content:
                        if isinstance(block, TextContent) and block.text and on_event is not None:
                            await on_event(AgentEvent(type="text_delta", delta=block.text))

                if response.stop_reason != "tool_use":
                    logger.info(
                        "Loop ending: stop_reason=%r != 'tool_use', session_id=%s",
                        response.stop_reason,
                        session_id,
                    )
                    await _checkpoint_new_messages()
                    if on_event is not None and not stream:
                        await on_event(
                            AgentEvent(
                                type="done",
                                stop_reason=response.stop_reason,
                                message=response,
                            )
                        )
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
                tool_arguments_by_call_id = {
                    call.id: call.arguments
                    for call in tool_calls
                    if isinstance(call.id, str) and call.id
                }

                await _checkpoint_new_messages()

                for tool_call in tool_calls:
                    should_emit_start = (
                        not stream
                        or not tool_call.id
                        or tool_call.id not in streamed_tool_call_ids
                    )
                    if on_event is not None and should_emit_start:
                        await on_event(AgentEvent(type="toolcall_start", tool_call=tool_call))

                async def _emit_pending_tool_result(tool_result: ToolResultMessage) -> None:
                    if on_event is None:
                        return
                    await on_event(
                        AgentEvent(
                            type="tool_result",
                            tool_result=ToolResultContent(
                                tool_call_id=tool_result.tool_call_id,
                                tool_name=tool_result.tool_name,
                                content=tool_result.content,
                                is_error=tool_result.is_error,
                                metadata=self._stream_safe_tool_metadata(tool_result.metadata),
                                tool_arguments=(
                                    tool_arguments_by_call_id.get(tool_result.tool_call_id)
                                    if isinstance(
                                        tool_arguments_by_call_id.get(tool_result.tool_call_id),
                                        dict,
                                    )
                                    else None
                                ),
                            ),
                        )
                    )

                tool_execution_cancelled = False
                try:
                    tool_results = await self._tool_adapter.execute_tool_calls(
                        tool_calls,
                        db,
                        session_id=session_id,
                        agent_mode=agent_mode,
                        on_pending_tool_result=_emit_pending_tool_result,
                    )
                except ToolExecutionCancelled as exc:
                    tool_results = exc.results
                    tool_execution_cancelled = True
                working_messages.extend(tool_results)
                created.extend(tool_results)

                reinjection = build_tool_image_reinjection_messages(
                    tool_results,
                    policy=reinjection_policy,
                    seen_hashes=reinjected_hashes,
                )
                if reinjection.messages:
                    working_messages.extend(reinjection.messages)
                    logger.debug(
                        "Reinjected %d tool image(s) (skipped=%d) session_id=%s",
                        reinjection.selected_count,
                        reinjection.skipped_count,
                        session_id,
                    )

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
                    if on_event is not None:
                        await on_event(
                            AgentEvent(
                                type="tool_result",
                                tool_result=ToolResultContent(
                                    tool_call_id=tool_result.tool_call_id,
                                    tool_name=tool_result.tool_name,
                                    content=tool_result.content,
                                    is_error=tool_result.is_error,
                                    metadata=self._stream_safe_tool_metadata(tool_result.metadata),
                                    tool_arguments=(
                                        tool_arguments_by_call_id.get(tool_result.tool_call_id)
                                        if isinstance(
                                            tool_arguments_by_call_id.get(tool_result.tool_call_id),
                                            dict,
                                        )
                                        else None
                                    ),
                                ),
                            )
                        )

                await _checkpoint_new_messages()
                if tool_execution_cancelled:
                    raise asyncio.CancelledError
                if cooldown_seconds > 0:
                    await asyncio.sleep(cooldown_seconds)
            else:
                await _run_finalization_round(total_limit)

        try:
            await asyncio.wait_for(_run_iterations(), timeout=max(0.1, float(timeout_seconds)))
        except asyncio.TimeoutError:
            last_error = f"Agent loop timed out after {timeout_seconds}s"
            logger.error("Agent loop timeout: session_id=%s timeout=%s", session_id, timeout_seconds)
            error_msg = self._make_error_message(
                f"Agent timed out after {int(timeout_seconds)}s. You can retry your request."
            )
            created.append(error_msg)
            assistant_iterations[id(error_msg)] = iterations
            if on_event is not None:
                await on_event(AgentEvent(type="done", stop_reason="timeout"))
            done_emitted = True
        except asyncio.CancelledError:
            last_error = "Generation stopped by user"
            logger.info("Agent loop cancelled: session_id=%s", session_id)
            error_msg = self._make_error_message("Generation stopped by user.")
            created.append(error_msg)
            assistant_iterations[id(error_msg)] = iterations
            if on_event is not None:
                await on_event(AgentEvent(type="error", error=last_error))
                await on_event(AgentEvent(type="done", stop_reason="aborted"))
            done_emitted = True

        return AgentExecutionArtifacts(
            created=created,
            assistant_iterations=assistant_iterations,
            usage=total_usage,
            iterations=iterations,
            done_emitted=done_emitted,
            error=last_error,
        )
