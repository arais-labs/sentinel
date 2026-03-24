"""Runtime-native agent execution engine."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from app.sentral.interfaces import Compactor, ConversationStore, Provider, Runtime, ToolRegistry
from app.sentral.types import (
    AgentEvent,
    ApprovalRequest,
    AssistantTurn,
    CompactionConfig,
    CompactionResult,
    ConversationItem,
    EventSink,
    GenerationConfig,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolCallBlock,
    ToolExecutionResult,
    ToolResultBlock,
    ToolSchema,
    TurnResult,
)


class AgentRuntimeEngine(Runtime):
    """Reusable runtime implementation over runtime-native contracts."""

    def __init__(
        self,
        *,
        provider: Provider,
        tool_registry: ToolRegistry,
        conversation_store: ConversationStore | None = None,
        compactor: Compactor | None = None,
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._conversation_store = conversation_store
        self._compactor = compactor

    async def run_turn(
        self,
        request,
        *,
        sink: EventSink | None = None,
    ) -> TurnResult:
        history = await self._load_history(request)
        result, _events = await self._execute(request, history=history, sink=sink)
        await self._persist_history(request, result)
        return result

    async def stream_turn(
        self,
        request,
    ) -> AsyncIterator[AgentEvent]:
        history = await self._load_history(request)
        queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
        terminal_error: Exception | None = None
        result: TurnResult | None = None

        async def _sink(event: AgentEvent) -> None:
            await queue.put(event)

        async def _run() -> None:
            nonlocal terminal_error, result
            try:
                result, _events = await self._execute(request, history=history, sink=_sink)
                await self._persist_history(request, result)
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
            raise NotImplementedError("No compactor is configured for AgentRuntimeEngine.")
        return await self._compactor.compact(history=history, config=config)

    async def _load_history(self, request) -> list[ConversationItem]:
        if request.history is not None:
            return list(request.history)
        if self._conversation_store is not None and request.conversation_id is not None:
            return list(await self._conversation_store.load_history(request.conversation_id))
        return []

    async def _persist_history(self, request, result: TurnResult) -> None:
        if self._conversation_store is None or request.conversation_id is None:
            return
        await self._conversation_store.replace_history(request.conversation_id, result.history)

    async def _execute(
        self,
        request,
        *,
        history: list[ConversationItem],
        sink: EventSink | None = None,
    ) -> tuple[TurnResult, list[AgentEvent]]:
        config = request.config
        if config is None:
            raise ValueError("RunTurnRequest.config is required.")
        if not request.new_items:
            raise ValueError("RunTurnRequest.new_items must contain at least one item.")

        working_history = [*history]
        created_items: list[ConversationItem] = [*request.new_items]
        working_history.extend(request.new_items)
        events: list[AgentEvent] = []
        usage = TokenUsage()
        iterations = 0
        pending_approval: ApprovalRequest | None = None
        last_stop_reason: str | None = None
        timeout_seconds = config.provider_metadata.get("timeout_seconds")

        async def _emit(event: AgentEvent) -> None:
            nonlocal last_stop_reason, pending_approval
            if event.stop_reason is not None:
                last_stop_reason = event.stop_reason
            if event.approval_request is not None:
                pending_approval = event.approval_request
            events.append(event)
            if sink is not None:
                await sink(event)

        async def _run_iterations() -> None:
            nonlocal iterations
            effective_max = max(1, config.max_iterations)
            grace_extension = max(10, effective_max // 4)
            total_limit = effective_max + grace_extension
            grace_check_done = False

            for index in range(total_limit):
                if index == effective_max and not grace_check_done:
                    grace_check_done = True
                    if not await self._grace_analysis(working_history, effective_max, grace_extension):
                        await self._run_finalization_round(
                            working_history=working_history,
                            created_items=created_items,
                            usage=usage,
                            config=config,
                            progress_max=effective_max,
                            emit=_emit,
                        )
                        return

                iterations = index + 1
                progress_max = total_limit if grace_check_done else effective_max
                await _emit(
                    AgentEvent(
                        type="agent_progress",
                        iteration=min(iterations, progress_max),
                        max_iterations=progress_max,
                    )
                )

                turn = await self._provider_turn(
                    working_history,
                    config=config,
                    emit=_emit,
                )
                usage.input_tokens += turn.usage.input_tokens
                usage.output_tokens += turn.usage.output_tokens
                assistant_item = turn.item
                assistant_item.metadata = {
                    **assistant_item.metadata,
                    "iteration": iterations,
                }
                working_history.append(assistant_item)
                created_items.append(assistant_item)

                if turn.stop_reason != "tool_use":
                    return

                tool_calls = [block for block in assistant_item.content if isinstance(block, ToolCallBlock)]
                if not tool_calls:
                    await _emit(AgentEvent(type="done", stop_reason="stop"))
                    return

                tool_results = await self._execute_tool_calls(tool_calls)
                for result in tool_results:
                    tool_item = ConversationItem(
                        id=_new_item_id("tool"),
                        role="tool",
                        content=[result],
                        metadata=dict(result.metadata),
                    )
                    working_history.append(tool_item)
                    created_items.append(tool_item)
                    await _emit(AgentEvent(type="tool_result", tool_result=result))
                    approval_request = self._approval_from_tool_result(result)
                    if approval_request is not None:
                        await _emit(
                            AgentEvent(
                                type="approval_required",
                                approval_request=approval_request,
                            )
                        )
                        await _emit(
                            AgentEvent(
                                type="done",
                                stop_reason="pending_approval",
                            )
                        )
                        return

            await self._run_finalization_round(
                working_history=working_history,
                created_items=created_items,
                usage=usage,
                config=config,
                progress_max=effective_max + grace_extension,
                emit=_emit,
            )

        if isinstance(timeout_seconds, (int, float)) and float(timeout_seconds) > 0:
            try:
                await asyncio.wait_for(_run_iterations(), timeout=float(timeout_seconds))
            except asyncio.TimeoutError:
                await _emit(AgentEvent(type="done", stop_reason="timeout"))
                return self._build_result(
                    history=working_history,
                    created_items=created_items,
                    usage=usage,
                    iterations=iterations,
                    stop_reason="timeout",
                    error=f"Agent loop timed out after {timeout_seconds}s",
                    pending_approval=pending_approval,
                ), events
        else:
            await _run_iterations()

        return self._build_result(
            history=working_history,
            created_items=created_items,
            usage=usage,
            iterations=iterations,
            stop_reason=last_stop_reason,
            error=None,
            pending_approval=pending_approval,
        ), events

    async def _provider_turn(
        self,
        history: list[ConversationItem],
        *,
        config: GenerationConfig,
        emit: EventSink,
    ) -> AssistantTurn:
        tool_schemas = self._tool_schemas()
        if config.stream:
            return await self._stream_response(history, tools=tool_schemas, config=config, emit=emit)
        turn = await self._provider.chat(messages=history, tools=tool_schemas, config=config)
        for block in turn.item.content:
            if isinstance(block, TextBlock) and block.text:
                await emit(AgentEvent(type="text_delta", delta=block.text))
        await emit(AgentEvent(type="done", stop_reason=turn.stop_reason, item=turn.item))
        return turn

    async def _stream_response(
        self,
        history: list[ConversationItem],
        *,
        tools: list[ToolSchema],
        config: GenerationConfig,
        emit: EventSink,
    ) -> AssistantTurn:
        streamed_events: list[AgentEvent] = []
        done_event: AgentEvent | None = None
        async for event in self._provider.stream(
            messages=history,
            tools=tools,
            config=config,
        ):
            if event.type == "done":
                done_event = event
                continue
            streamed_events.append(event)
            await emit(event)
        final_done = done_event or AgentEvent(type="done", stop_reason="stop")
        streamed_events.append(final_done)
        await emit(final_done)
        return self._assemble_turn_from_events(streamed_events, fallback_model=config.model, fallback_provider=self._provider.name)

    def _assemble_turn_from_events(
        self,
        events: list[AgentEvent],
        *,
        fallback_model: str,
        fallback_provider: str,
    ) -> AssistantTurn:
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

        for index, event in enumerate(events):
            idx = int(event.metadata.get("content_index", index)) if isinstance(event.metadata.get("content_index"), int) else index
            if event.item is not None and event.item.role == "assistant":
                model = str(event.item.metadata.get("model") or model)
                provider = str(event.item.metadata.get("provider") or provider)
                usage_payload = event.item.metadata.get("usage") if isinstance(event.item.metadata.get("usage"), dict) else {}
                usage.input_tokens += int(usage_payload.get("input_tokens") or 0)
                usage.output_tokens += int(usage_payload.get("output_tokens") or 0)
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
                if isinstance(event.metadata.get("signature"), str):
                    thinking_signatures.setdefault(idx, []).append(str(event.metadata["signature"]))
            elif event.type == "toolcall_start":
                _remember("tool_call", idx)
                tool_call = event.tool_call or ToolCallBlock()
                tool_calls[idx] = {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arg_deltas": [],
                    "base_args": tool_call.arguments,
                    "thought_signature": tool_call.thought_signature,
                }
            elif event.type == "toolcall_delta":
                _remember("tool_call", idx)
                state = tool_calls.setdefault(
                    idx,
                    {
                        "id": "",
                        "name": "",
                        "arg_deltas": [],
                        "base_args": {},
                        "thought_signature": None,
                    },
                )
                state["arg_deltas"].append(event.delta or "")
            elif event.type == "error":
                stop_reason = "error"
            elif event.type == "done":
                if stop_reason != "error":
                    stop_reason = event.stop_reason or stop_reason

        content: list[Any] = []
        for block_type, idx in block_sequence:
            if block_type == "text":
                text = "".join(text_blocks.get(idx, []))
                if text:
                    content.append(TextBlock(text=text))
            elif block_type == "thinking":
                thinking = "".join(thinking_blocks.get(idx, []))
                signature_parts = thinking_signatures.get(idx, [])
                signature = "".join(signature_parts) if signature_parts else None
                if thinking or signature:
                    content.append(ThinkingBlock(thinking=thinking, signature=signature))
            elif block_type == "tool_call":
                state = tool_calls.get(
                    idx,
                    {"id": "", "name": "", "arg_deltas": [], "base_args": {}, "thought_signature": None},
                )
                call_id = str(state.get("id") or "").strip()
                call_name = str(state.get("name") or "").strip()
                if not call_id or not call_name:
                    continue
                raw_args = "".join(state.get("arg_deltas", []))
                if raw_args:
                    try:
                        loaded = json.loads(raw_args)
                        parsed_args = loaded if isinstance(loaded, dict) else {"value": loaded}
                    except json.JSONDecodeError:
                        parsed_args = {"raw": raw_args}
                else:
                    parsed_args = state.get("base_args") if isinstance(state.get("base_args"), dict) else {}
                if isinstance(state.get("base_args"), dict) and isinstance(parsed_args, dict):
                    merged = dict(state["base_args"])
                    merged.update(parsed_args)
                    parsed_args = merged
                content.append(
                    ToolCallBlock(
                        id=call_id,
                        name=call_name,
                        arguments=parsed_args,
                        thought_signature=(
                            str(state.get("thought_signature")).strip()
                            if isinstance(state.get("thought_signature"), str) and str(state.get("thought_signature")).strip()
                            else None
                        ),
                    )
                )

        has_tool_calls = any(isinstance(block, ToolCallBlock) for block in content)
        if has_tool_calls and stop_reason not in {"error", "aborted", "timeout"}:
            stop_reason = "tool_use"
        elif not has_tool_calls and stop_reason == "tool_use":
            stop_reason = "stop"

        item = ConversationItem(
            id=_new_item_id("assistant"),
            role="assistant",
            content=content,
            metadata={
                "model": model,
                "provider": provider,
                "stop_reason": stop_reason,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                },
            },
        )
        return AssistantTurn(item=item, stop_reason=stop_reason, usage=usage)

    async def _execute_tool_calls(self, tool_calls: list[ToolCallBlock]) -> list[ToolResultBlock]:
        tasks = [self._execute_one_tool(call) for call in tool_calls]
        return await asyncio.gather(*tasks)

    async def _execute_one_tool(self, tool_call: ToolCallBlock) -> ToolResultBlock:
        tool = self._tool_registry.get_tool(tool_call.name)
        if tool is None:
            return ToolResultBlock(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=f"Tool '{tool_call.name}' is not registered",
                is_error=True,
                tool_arguments=dict(tool_call.arguments),
            )
        result = await tool.execute(dict(tool_call.arguments))
        return self._tool_execution_to_result_block(tool_call, result)

    def _tool_execution_to_result_block(
        self,
        tool_call: ToolCallBlock,
        result: ToolExecutionResult,
    ) -> ToolResultBlock:
        metadata = dict(result.metadata)
        if result.approval_request is not None:
            req = result.approval_request
            is_pending = result.status == "pending_approval"
            # Use field names the frontend expects: provider / approval_id (not tool_name / id)
            approval_dict: dict[str, Any] = {
                "provider": req.tool_name,
                "approval_id": req.id,
                "pending": is_pending,
                "status": "pending" if is_pending else "resolved",
                "can_resolve": is_pending,
                "action": req.action,
                "description": req.description,
            }
            # Carry through any extra metadata fields from the original approval payload
            for k, v in req.metadata.items():
                if k not in approval_dict:
                    approval_dict[k] = v
            metadata["approval"] = approval_dict
            metadata["pending"] = is_pending
        if result.status == "pending_approval":
            req = result.approval_request
            content = json.dumps(
                {
                    "status": "pending",
                    "message": req.description if req is not None else "Action requires approval.",
                    "approval": metadata.get("approval"),
                },
                default=str,
            )
            return ToolResultBlock(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=content,
                is_error=False,
                metadata=metadata,
                tool_arguments=dict(tool_call.arguments),
            )
        if result.status == "error":
            return ToolResultBlock(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=str(result.error or "Tool execution failed."),
                is_error=True,
                metadata=metadata,
                tool_arguments=dict(tool_call.arguments),
            )
        content = result.content if isinstance(result.content, str) else json.dumps(result.content, default=str)
        return ToolResultBlock(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            content=content,
            is_error=False,
            metadata=metadata,
            tool_arguments=dict(tool_call.arguments),
        )

    def _approval_from_tool_result(self, result: ToolResultBlock) -> ApprovalRequest | None:
        approval = result.metadata.get("approval")
        if not isinstance(approval, dict):
            return None
        # Only trigger the approval-required flow when the approval is actually still pending.
        # Resolved approvals (approved/rejected/timed_out) must not stop the loop.
        pending = approval.get("pending")
        status = str(approval.get("status") or "").lower()
        if pending is not True and status not in ("pending", ""):
            return None
        return ApprovalRequest(
            id=str(approval.get("id") or approval.get("approval_id") or "").strip(),
            tool_name=str(approval.get("tool_name") or approval.get("provider") or result.tool_name).strip(),
            action=str(approval.get("action") or result.tool_name).strip(),
            description=str(approval.get("description") or "Action requires approval.").strip(),
            payload=dict(result.tool_arguments or {}),
            metadata={
                key: value
                for key, value in approval.items()
                if key not in {"id", "approval_id", "tool_name", "provider", "action", "description"}
            },
        )

    async def _run_finalization_round(
        self,
        *,
        working_history: list[ConversationItem],
        created_items: list[ConversationItem],
        usage: TokenUsage,
        config: GenerationConfig,
        progress_max: int,
        emit: EventSink,
    ) -> None:
        await emit(
            AgentEvent(
                type="agent_progress",
                iteration=progress_max + 1,
                max_iterations=progress_max + 1,
            )
        )
        final_instruction = (
            "You've reached the step limit, and this is the final reply for this run. "
            "Do not call any tools. "
            "Write a natural, user-facing update (not robotic or templated). "
            "If the task is unfinished, briefly cover: what was completed, what is blocked/uncertain, "
            "and the single best next step to continue."
        )
        final_history = [
            *working_history,
            ConversationItem(
                id=_new_item_id("user"),
                role="user",
                content=[TextBlock(text=final_instruction)],
            ),
        ]
        final_turn = await self._provider.chat(
            messages=final_history,
            tools=[],
            config=GenerationConfig(
                model=config.model,
                temperature=config.temperature,
                max_iterations=config.max_iterations,
                stream=False,
                system_prompt=config.system_prompt,
                max_output_tokens=config.max_output_tokens,
                tool_choice="none",
                provider_metadata=dict(config.provider_metadata),
            ),
        )
        usage.input_tokens += final_turn.usage.input_tokens
        usage.output_tokens += final_turn.usage.output_tokens
        created_items.append(final_turn.item)
        working_history.append(final_turn.item)
        for block in final_turn.item.content:
            if isinstance(block, TextBlock) and block.text:
                await emit(AgentEvent(type="text_delta", delta=block.text))
        await emit(AgentEvent(type="done", stop_reason=final_turn.stop_reason, item=final_turn.item))

    async def _grace_analysis(
        self,
        history: list[ConversationItem],
        effective_max: int,
        grace_extension: int,
    ) -> bool:
        tail = history[-20:] if len(history) > 20 else history[:]
        summary_lines: list[str] = []
        for item in tail:
            if item.role == "assistant":
                text_parts = [block.text for block in item.content if isinstance(block, TextBlock) and block.text]
                tool_calls = [
                    f"{block.name}({json.dumps(block.arguments)[:200]})"
                    for block in item.content
                    if isinstance(block, ToolCallBlock)
                ]
                if text_parts:
                    summary_lines.append(f"assistant: {' '.join(text_parts)[:300]}")
                for tool_call in tool_calls:
                    summary_lines.append(f"tool_call: {tool_call}")
            elif item.role == "tool":
                for block in item.content:
                    if isinstance(block, ToolResultBlock):
                        status = "error" if block.is_error else "ok"
                        summary_lines.append(f"tool_result({status}): {(block.content or '')[:300]}")
        tail_text = "\n".join(summary_lines[-30:])
        analysis_request = [
            ConversationItem(
                id=_new_item_id("user"),
                role="user",
                content=[
                    TextBlock(
                        text=(
                            f"An AI agent has reached its iteration limit ({effective_max} steps). "
                            f"You must decide whether to grant a grace extension of {grace_extension} additional iterations "
                            "so it can complete its current task.\n\n"
                            "## Recent conversation tail (last ~10 exchanges)\n"
                            f"{tail_text}\n\n"
                            "Respond ONLY with valid JSON: {\"continue\": true} or {\"continue\": false}."
                        )
                    )
                ],
            )
        ]
        try:
            response = await asyncio.wait_for(
                self._provider.chat(
                    messages=analysis_request,
                    tools=[],
                    config=GenerationConfig(model="fast", temperature=0.0, stream=False),
                ),
                timeout=15.0,
            )
            text = "\n".join(
                block.text for block in response.item.content if isinstance(block, TextBlock) and block.text
            ).strip()
            if text.startswith("```"):
                text = text.split("```")[1] if "```" in text[3:] else text
                text = text.lstrip("json").strip()
            parsed = json.loads(text)
            return bool(parsed.get("continue")) if isinstance(parsed, dict) else False
        except Exception:  # noqa: BLE001
            return False

    def _tool_schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=tool.name,
                description=tool.description,
                parameters=dict(tool.parameters_schema),
            )
            for tool in self._tool_registry.list_tools()
            if tool.enabled
        ]

    def _build_result(
        self,
        *,
        history: list[ConversationItem],
        created_items: list[ConversationItem],
        usage: TokenUsage,
        iterations: int,
        stop_reason: str | None,
        error: str | None,
        pending_approval: ApprovalRequest | None,
    ) -> TurnResult:
        final_item = next((item for item in reversed(history) if item.role == "assistant"), None)
        if pending_approval is not None:
            status = "pending_approval"
        elif stop_reason == "timeout":
            status = "timeout"
        elif stop_reason == "aborted":
            status = "aborted"
        elif error:
            status = "error"
        else:
            status = "completed"
        return TurnResult(
            status=status,
            history=history,
            usage=usage,
            iterations=iterations,
            final_item=final_item,
            stop_reason=stop_reason,
            pending_approval=pending_approval,
            error=error,
            metadata={"created_items": created_items},
        )


def _new_item_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


import contextlib
