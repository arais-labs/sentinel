"""Conversion helpers between Sentinel runtime types and standalone contracts."""

from __future__ import annotations

from typing import Any

from app.sentral import (
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
from app.models import Message
from app.services.llm.generic.types import (
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
from app.services.tools.approval.extractors import extract_approval_metadata_from_tool_result


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
            content=_runtime_blocks_to_assistant_content(item.content),
            model=str(metadata.get("model") or ""),
            provider=str(metadata.get("provider") or ""),
            usage=TokenUsage(
                input_tokens=int(usage_payload.get("input_tokens") or 0),
                output_tokens=int(usage_payload.get("output_tokens") or 0),
            ),
            stop_reason=str(metadata.get("stop_reason") or "stop"),
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
                TextBlock(text=block.text)
                if isinstance(block, TextContent)
                else ImageBlock(media_type=block.media_type, data=block.data)
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
            "usage": {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        }
        return ConversationItem(
            id=item_id,
            role="assistant",
            content=[
                TextBlock(text=block.text)
                if isinstance(block, TextContent)
                else ThinkingBlock(
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
            result={
                "approval": event.tool_result.metadata.get("approval"),
            }
            if isinstance(event.tool_result.metadata.get("approval"), dict)
            else {},
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


def db_messages_to_runtime_items(messages: list[Message]) -> list[ConversationItem]:
    items: list[ConversationItem] = []
    index = 0
    total = len(messages)
    while index < total:
        current = messages[index]
        if current.role == "assistant":
            tool_calls = _db_tool_call_blocks(current)
            if not tool_calls:
                items.append(_db_message_to_runtime_item(current, include_tool_calls=False))
                index += 1
                continue

            cursor = index + 1
            trailing_results: list[Message] = []
            while cursor < total and messages[cursor].role in {"tool", "tool_result"}:
                trailing_results.append(messages[cursor])
                cursor += 1

            required_ids = {
                block.id for block in tool_calls if isinstance(block.id, str) and block.id
            }
            result_ids = {
                item.tool_call_id
                for item in trailing_results
                if isinstance(item.tool_call_id, str) and item.tool_call_id
            }
            if required_ids and required_ids.issubset(result_ids):
                items.append(_db_message_to_runtime_item(current, include_tool_calls=True))
                for tool_message in trailing_results:
                    if tool_message.tool_call_id in required_ids:
                        items.append(_db_message_to_runtime_item(tool_message))
                index = cursor
                continue

            items.append(_db_message_to_runtime_item(current, include_tool_calls=False))
            index = cursor if trailing_results else index + 1
            continue

        if current.role in {"tool", "tool_result"}:
            index += 1
            continue

        items.append(_db_message_to_runtime_item(current))
        index += 1
    return items


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


def _db_message_to_runtime_item(
    message: Message,
    *,
    include_tool_calls: bool = True,
) -> ConversationItem:
    timestamp = (
        message.created_at.isoformat()
        if message.created_at is not None
        else ConversationItem(id=str(message.id), role="user").timestamp
    )
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    if message.role == "assistant":
        content: list[Any] = []
        text = (message.content or "").strip()
        if text:
            content.append(TextBlock(text=text))
        if include_tool_calls:
            content.extend(_db_tool_call_blocks(message))
        return ConversationItem(
            id=str(message.id),
            role="assistant",
            content=content,
            metadata=metadata,
            timestamp=timestamp,
        )
    if message.role in {"tool", "tool_result"}:
        return ConversationItem(
            id=str(message.id),
            role="tool",
            content=[
                ToolResultBlock(
                    tool_call_id=message.tool_call_id or "",
                    tool_name=message.tool_name or "",
                    content=message.content or "",
                    is_error=bool(metadata.get("is_error")),
                    metadata=metadata,
                )
            ],
            metadata=metadata,
            timestamp=timestamp,
        )
    if message.role == "system":
        return ConversationItem(
            id=str(message.id),
            role="system",
            content=[TextBlock(text=message.content or "")],
            metadata=metadata,
            timestamp=timestamp,
        )
    content = []
    text = (message.content or "").strip()
    if text:
        content.append(TextBlock(text=text))
    attachments = metadata.get("attachments")
    if isinstance(attachments, list):
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            mime_type = attachment.get("mime_type")
            data = attachment.get("base64")
            if isinstance(mime_type, str) and isinstance(data, str) and data.strip():
                content.append(ImageBlock(media_type=mime_type.strip() or "image/png", data=data.strip()))
    return ConversationItem(
        id=str(message.id),
        role="user",
        content=content or [TextBlock(text=message.content or "")],
        metadata=metadata,
        timestamp=timestamp,
    )


def _db_tool_call_blocks(message: Message) -> list[ToolCallBlock]:
    metadata = dict(message.metadata_json or {}) if isinstance(message.metadata_json, dict) else {}
    blocks: list[ToolCallBlock] = []
    for item in metadata.get("tool_calls") or []:
        if not isinstance(item, dict):
            continue
        call_id = item.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            continue
        thought_signature = item.get("thought_signature")
        if not isinstance(thought_signature, str) or not thought_signature.strip():
            alt_signature = item.get("thoughtSignature")
            thought_signature = alt_signature if isinstance(alt_signature, str) else None
        blocks.append(
            ToolCallBlock(
                id=call_id,
                name=str(item.get("name") or ""),
                arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
                thought_signature=thought_signature.strip() if isinstance(thought_signature, str) and thought_signature.strip() else None,
            )
        )
    return blocks
