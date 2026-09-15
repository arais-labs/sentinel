"""Convert persisted Sentinel messages into central runtime conversations."""

from __future__ import annotations
from typing import Any
from app.models import Message
from sentral import ConversationItem, ImageBlock, TextBlock, ToolCallBlock, ToolResultBlock
from app.services.sessions.history import order_steering_history


def db_messages_to_runtime_items(messages: list[Message]) -> list[ConversationItem]:

    messages = order_steering_history(messages)
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
        elif _db_tool_call_blocks(message):
            # Raw Responses items also contain tool calls; omit incomplete exchanges.
            metadata.pop("responses_output", None)
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
                content.append(
                    ImageBlock(media_type=mime_type.strip() or "image/png", data=data.strip())
                )
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
                thought_signature=(
                    thought_signature.strip()
                    if isinstance(thought_signature, str) and thought_signature.strip()
                    else None
                ),
            )
        )
    return blocks
