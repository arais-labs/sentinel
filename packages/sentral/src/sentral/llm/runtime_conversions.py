"""Conversion helpers between Sentinel runtime types and standalone contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sentral import (
    AgentEvent as RuntimeAgentEvent,
    ApprovalRequest,
    AssistantTurn,
    ConversationItem,
    GenerationConfig,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    TokenUsage as RuntimeTokenUsage,
    ToolCallBlock,
    ToolResultBlock,
    ToolSchema as RuntimeToolSchema,
)
from sentral.llm.generic.types import (
    AgentEvent as SentinelAgentEvent,
    AgentMessage as SentinelAgentMessage,
    AssistantMessage,
    ImageContent,
    SystemMessage,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
    ToolResultContent,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)
from sentral.approval_payload import extract_approval_metadata_from_tool_result


def runtime_tool_schema_to_sentinel(schema: RuntimeToolSchema) -> ToolSchema:
    return ToolSchema(
        name=schema.name,
        description=schema.description,
        parameters=dict(schema.parameters),
    )


def sentinel_tool_schema_to_runtime(schema: ToolSchema) -> RuntimeToolSchema:
    return RuntimeToolSchema(
        name=schema.name,
        description=schema.description,
        parameters=dict(schema.parameters),
    )


def runtime_item_to_sentinel_message(item: ConversationItem) -> SentinelAgentMessage:
    if item.role == "system":
        return SystemMessage(
            content=_flatten_text_content(item.content),
            metadata=dict(item.metadata),
            timestamp=item.timestamp,
        )
    if item.role == "user":
        user_blocks = _runtime_blocks_to_user_content(item.content)
        user_content: str | list[TextContent | ImageContent]
        if len(user_blocks) == 1 and isinstance(user_blocks[0], TextContent):
            user_content = user_blocks[0].text
        else:
            user_content = user_blocks
        return UserMessage(
            content=user_content,
            metadata=dict(item.metadata),
            timestamp=item.timestamp,
        )
    if item.role == "assistant":
        metadata = dict(item.metadata)
        usage_payload = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
        return AssistantMessage(
            presentation=metadata.get("presentation"),
            content=_runtime_blocks_to_assistant_content(item.content),
            model=str(metadata.get("model") or ""),
            provider=str(metadata.get("provider") or ""),
            usage=TokenUsage(
                input_tokens=int(usage_payload.get("input_tokens") or 0),
                output_tokens=int(usage_payload.get("output_tokens") or 0),
            ),
            stop_reason=str(metadata.get("stop_reason") or "stop"),
            responses_output=deepcopy(metadata.get("responses_output") or []),
            provider_usage=deepcopy(metadata.get("provider_usage")),
            responses_context_reset=bool(metadata.get("responses_context_reset")),
        )
    tool_block = _first_tool_result_block(item.content)
    return ToolResultMessage(
        tool_call_id=tool_block.tool_call_id if tool_block is not None else "",
        tool_name=tool_block.tool_name if tool_block is not None else "",
        content=tool_block.content if tool_block is not None else "",
        is_error=tool_block.is_error if tool_block is not None else False,
        metadata=dict(item.metadata),
    )


def runtime_items_to_sentinel_messages(
    items: list[ConversationItem],
) -> list[SentinelAgentMessage]:
    return [runtime_item_to_sentinel_message(item) for item in items]


def sentinel_message_to_runtime_item(
    message: SentinelAgentMessage,
    *,
    item_id: str,
) -> ConversationItem:
    if isinstance(message, SystemMessage):
        return ConversationItem(
            id=item_id,
            role="system",
            content=[TextBlock(text=message.content)],
            metadata=dict(message.metadata),
            timestamp=message.timestamp,
        )
    if isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, str):
            blocks = [TextBlock(text=content)]
        else:
            blocks = [
                (
                    TextBlock(text=block.text)
                    if isinstance(block, TextContent)
                    else ImageBlock(media_type=block.media_type, data=block.data)
                )
                for block in content
            ]
        return ConversationItem(
            id=item_id,
            role="user",
            content=blocks,
            metadata=dict(message.metadata),
            timestamp=message.timestamp,
        )
    if isinstance(message, AssistantMessage):
        metadata = {
            "model": message.model,
            "provider": message.provider,
            "stop_reason": message.stop_reason,
            "responses_output": deepcopy(message.responses_output),
            "responses_context_reset": message.responses_context_reset,
            "provider_usage": deepcopy(message.provider_usage),
            "usage": {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        }
        return ConversationItem(
            id=item_id,
            role="assistant",
            content=[
                (
                    TextBlock(text=block.text)
                    if isinstance(block, TextContent)
                    else (
                        ThinkingBlock(
                            thinking=block.thinking,
                            signature=block.signature,
                        )
                        if isinstance(block, ThinkingContent)
                        else ToolCallBlock(
                            id=block.id,
                            name=block.name,
                            arguments=dict(block.arguments),
                            thought_signature=block.thought_signature,
                        )
                    )
                )
                for block in message.content
            ],
            metadata=metadata,
        )
    return ConversationItem(
        id=item_id,
        role="tool",
        content=[
            ToolResultBlock(
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                content=message.content,
                is_error=message.is_error,
                metadata=dict(message.metadata),
            )
        ],
        metadata=dict(message.metadata),
    )


def sentinel_assistant_turn_to_runtime(
    message: AssistantMessage,
    *,
    item_id: str,
) -> AssistantTurn:
    item = sentinel_message_to_runtime_item(message, item_id=item_id)
    return AssistantTurn(
        item=item,
        stop_reason=message.stop_reason,
        usage=RuntimeTokenUsage(
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        ),
    )


def sentinel_event_to_runtime_event(
    event: SentinelAgentEvent,
) -> RuntimeAgentEvent:
    metadata: dict[str, Any] = {}
    if event.signature is not None:
        metadata["signature"] = event.signature
    if event.content_index is not None:
        metadata["content_index"] = event.content_index
    runtime_event = RuntimeAgentEvent(
        type=event.type,
        delta=event.delta,
        stop_reason=event.stop_reason,
        error=event.error,
        iteration=event.iteration,
        max_iterations=event.max_iterations,
        metadata=metadata,
    )
    if event.tool_call is not None:
        runtime_event.tool_call = ToolCallBlock(
            id=event.tool_call.id,
            name=event.tool_call.name,
            arguments=dict(event.tool_call.arguments),
            thought_signature=event.tool_call.thought_signature,
        )
    if event.tool_result is not None:
        runtime_event.tool_result = ToolResultBlock(
            tool_call_id=event.tool_result.tool_call_id,
            tool_name=event.tool_result.tool_name,
            content=event.tool_result.content,
            is_error=event.tool_result.is_error,
            metadata=dict(event.tool_result.metadata),
            tool_arguments=(
                dict(event.tool_result.tool_arguments)
                if isinstance(event.tool_result.tool_arguments, dict)
                else None
            ),
        )
        approval_payload = extract_approval_metadata_from_tool_result(
            tool_name=event.tool_result.tool_name,
            result=(
                {
                    "approval": event.tool_result.metadata.get("approval"),
                }
                if isinstance(event.tool_result.metadata.get("approval"), dict)
                else {}
            ),
        )
        if approval_payload is not None:
            runtime_event.approval_request = approval_payload_to_request(
                approval_payload,
                payload=event.tool_result.tool_arguments or {},
            )
    if event.message is not None:
        runtime_event.item = sentinel_message_to_runtime_item(
            event.message,
            item_id="assistant",
        )
    return runtime_event


def approval_payload_to_request(
    approval_payload: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
) -> ApprovalRequest:
    metadata = {
        key: value
        for key, value in approval_payload.items()
        if key
        not in {
            "approval_id",
            "action",
            "description",
            "provider",
            "pending",
            "status",
            "can_resolve",
            "label",
        }
    }
    provider = str(approval_payload.get("provider") or "").strip()
    return ApprovalRequest(
        id=str(approval_payload.get("approval_id") or "").strip(),
        tool_name=provider,
        action=str(approval_payload.get("action") or provider).strip(),
        description=str(approval_payload.get("description") or "Action requires approval.").strip(),
        payload=dict(payload or {}),
        metadata=metadata,
    )


def generation_config_to_reasoning_kwargs(
    config: GenerationConfig,
) -> dict[str, Any]:
    return dict(config.provider_metadata)


def _flatten_text_content(content: list[Any]) -> str:
    text_parts = [block.text for block in content if isinstance(block, TextBlock)]
    return "\n".join(part for part in text_parts if part)


def _runtime_blocks_to_user_content(
    content: list[Any],
) -> list[TextContent | ImageContent]:
    blocks: list[TextContent | ImageContent] = []
    for block in content:
        if isinstance(block, TextBlock):
            blocks.append(TextContent(text=block.text))
        elif isinstance(block, ImageBlock):
            blocks.append(ImageContent(media_type=block.media_type, data=block.data))
    return blocks


def _runtime_blocks_to_assistant_content(
    content: list[Any],
) -> list[TextContent | ThinkingContent | ToolCallContent]:
    blocks: list[TextContent | ThinkingContent | ToolCallContent] = []
    for block in content:
        if isinstance(block, TextBlock):
            blocks.append(TextContent(text=block.text))
        elif isinstance(block, ThinkingBlock):
            blocks.append(
                ThinkingContent(
                    thinking=block.thinking,
                    signature=block.signature,
                )
            )
        elif isinstance(block, ToolCallBlock):
            call_id = str(block.id or "").strip()
            call_name = str(block.name or "").strip()
            if not call_id or not call_name:
                continue
            blocks.append(
                ToolCallContent(
                    id=call_id,
                    name=call_name,
                    arguments=dict(block.arguments),
                    thought_signature=block.thought_signature,
                )
            )
    return blocks


def _first_tool_result_block(content: list[Any]) -> ToolResultBlock | None:
    for block in content:
        if isinstance(block, ToolResultBlock):
            return block
    return None


def runtime_event_to_sentinel_event(
    event: RuntimeAgentEvent,
) -> SentinelAgentEvent:
    sentinel_event = SentinelAgentEvent(
        type=event.type,
        conversation_message=event.metadata.get("conversation_message"),
        presentation=event.metadata.get("presentation"),
        content_index=(
            int(event.metadata.get("content_index"))
            if isinstance(event.metadata.get("content_index"), int)
            else None
        ),
        delta=event.delta,
        stop_reason=event.stop_reason,
        error=event.error,
        iteration=event.iteration,
        max_iterations=event.max_iterations,
        signature=(
            str(event.metadata.get("signature"))
            if isinstance(event.metadata.get("signature"), str)
            else None
        ),
    )
    if event.tool_call is not None:
        sentinel_event.tool_call = ToolCallContent(
            id=event.tool_call.id,
            name=event.tool_call.name,
            arguments=dict(event.tool_call.arguments),
            thought_signature=event.tool_call.thought_signature,
        )
    if event.tool_result is not None:
        sentinel_event.tool_result = ToolResultContent(
            tool_call_id=event.tool_result.tool_call_id,
            tool_name=event.tool_result.tool_name,
            content=event.tool_result.content,
            is_error=event.tool_result.is_error,
            metadata=dict(event.tool_result.metadata),
            tool_arguments=(
                dict(event.tool_result.tool_arguments)
                if isinstance(event.tool_result.tool_arguments, dict)
                else None
            ),
        )
    if event.item is not None and event.item.role == "assistant":
        sentinel_item = runtime_item_to_sentinel_message(event.item)
        if isinstance(sentinel_item, AssistantMessage):
            sentinel_event.message = sentinel_item
    return sentinel_event
