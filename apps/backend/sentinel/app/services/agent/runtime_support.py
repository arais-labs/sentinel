"""Sentinel-specific support helpers around the shared runtime."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Message, Session
from app.services.agent.agent_modes import AgentMode
from app.services.agent.context_builder import ContextBuilder
from app.services.agent.tool_adapter import ToolAdapter
from app.services.estop import EstopLevel, EstopService
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    ToolSchema,
    SystemMessage,
    UserMessage,
)
from app.services.llm.ids import TierName
from app.services.messages import build_generation_metadata, with_generation_metadata
from app.services.sessions.context_usage import (
    build_context_usage_metrics,
    estimate_agent_messages_tokens,
)
from app.services.sessions.session_naming import (
    apply_conversation_message_delta,
    conversation_delta_for_role,
)


def humanize_error(raw: str) -> str:
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


@dataclass(slots=True)
class PreparedRuntimeTurnContext:
    """Sentinel-prepared context needed to execute one runtime turn."""

    messages: list[AgentMessage]
    tools: list[ToolSchema]
    effective_system_prompt: str | None
    runtime_context_snapshot: dict[str, Any] | None


class SentinelRuntimeSupport:
    """Sentinel-owned context/persistence helpers used by the shared runtime."""

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

    async def estop_level(self, db: AsyncSession) -> EstopLevel:
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
        return self._extract_final_text(messages)

    def collect_attachments(self, messages: list[AgentMessage]) -> list[dict[str, Any]]:
        return self._collect_attachments(messages)

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
        session_record = await db.get(Session, session_id)
        existing_result = await db.execute(select(Message).where(Message.session_id == session_id))
        existing_messages = existing_result.scalars().all()
        latest_existing_created_at = max(
            (
                item.created_at
                for item in existing_messages
                if isinstance(item.created_at, datetime)
            ),
            default=None,
        )
        base_time = datetime.now(UTC)
        if latest_existing_created_at is not None:
            min_base_time = latest_existing_created_at + timedelta(milliseconds=1)
            if base_time < min_base_time:
                base_time = min_base_time
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
                if session_record is not None and not session_record.initial_prompt and text_content.strip():
                    session_record.initial_prompt = text_content.strip()
                metadata = with_generation_metadata(metadata, generation=requested_generation)
                if isinstance(message.content, list):
                    attachments: list[dict[str, Any]] = []
                    for block in message.content:
                        if isinstance(block, ImageContent) and block.data:
                            attachments.append({"mime_type": block.media_type, "base64": block.data})
                    if attachments:
                        existing = metadata.get("attachments")
                        metadata["attachments"] = [*existing, *attachments] if isinstance(existing, list) else attachments
                db.add(
                    Message(
                        session_id=session_id,
                        role="user",
                        content=text_content,
                        metadata_json=metadata,
                        created_at=created_at,
                    )
                )
                conversation_delta += conversation_delta_for_role("user")
                continue
            if isinstance(message, AssistantMessage):
                text = self._assistant_text(message)
                tool_calls_data: list[dict[str, Any]] = []
                for block in message.content:
                    if not isinstance(block, ToolCallContent):
                        continue
                    tool_calls_data.append(
                        {
                            "id": block.id,
                            "name": block.name,
                            "arguments": self._sanitize_tool_call_arguments(block.arguments),
                            "thought_signature": block.thought_signature,
                        }
                    )
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
                metadata = with_generation_metadata(metadata, generation=assistant_generation)
                latest_assistant_generation = assistant_generation
                db.add(
                    Message(
                        session_id=session_id,
                        role="assistant",
                        content=text,
                        metadata_json=metadata,
                        token_count=message.usage.output_tokens,
                        created_at=created_at,
                    )
                )
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
                stored_content, truncation_meta = self._truncate_tool_result_for_storage(message.content or "")
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
                    db.add(
                        Message(
                            session_id=session_id,
                            role="tool_result",
                            content=stored_content,
                            metadata_json=metadata,
                            tool_call_id=message.tool_call_id or None,
                            tool_name=message.tool_name or None,
                            created_at=created_at,
                        )
                    )
        if session_record is not None:
            apply_conversation_message_delta(session_record, conversation_delta)
        await db.commit()

    @staticmethod
    def _extract_runtime_system_prompt(messages: list[AgentMessage]) -> str | None:
        blocks = [(message.content or "").strip() for message in messages if isinstance(message, SystemMessage)]
        blocks = [block for block in blocks if block]
        return "\n\n---\n\n".join(blocks) if blocks else None

    @staticmethod
    def _truncate_runtime_history_preview(value: str, *, max_chars: int = 320) -> str:
        text = value.strip()
        return text if len(text) <= max_chars else f"{text[:max_chars].rstrip()}..."

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
                    elif isinstance(block, ImageContent) and (block.data or "").strip():
                        image_count += 1
            preview = "\n\n".join(text_parts).strip()
            if not preview and image_count > 0:
                preview = f"[{image_count} image attachment{'s' if image_count != 1 else ''}]"
            entry: dict[str, Any] = {
                "role": "user",
                "kind": "history_user",
                "preview": SentinelRuntimeSupport._truncate_runtime_history_preview(preview) if preview else None,
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
                {"id": block.id, "name": block.name}
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
                "preview": SentinelRuntimeSupport._truncate_runtime_history_preview(preview) if preview else None,
                "text_block_count": len(text_parts),
                "tool_call_count": len(tool_calls),
            }
            if tool_calls:
                entry["tool_calls"] = tool_calls
            return entry
        if isinstance(message, ToolResultMessage):
            preview = SentinelRuntimeSupport._truncate_runtime_history_preview((message.content or "").strip())
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
                history_entry = SentinelRuntimeSupport._runtime_history_entry(message)
                if history_entry is not None:
                    history_messages.append(history_entry)
                continue
            content = (message.content or "").strip()
            if not content:
                continue
            system_blocks.append(content)
            metadata = dict(message.metadata or {})
            layer_entry: dict[str, Any] = {
                "layer": str(metadata.get("layer") or "system"),
                "kind": str(metadata.get("kind") or "system_block"),
                "title": str(metadata.get("title") or f"System block #{len(system_blocks)}"),
                "explanation": str(metadata.get("explanation") or "").strip() or None,
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
            {"title": block["title"], "content": str(block.get("content") or "").strip()}
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
                {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
                for tool in tools
            ],
        }

    def _extract_final_text(self, messages: list[AgentMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, AssistantMessage):
                text = self._assistant_text(message).strip()
                if text:
                    return text
        return ""

    @staticmethod
    def _collect_attachments(messages: list[AgentMessage]) -> list[dict[str, Any]]:
        attachments: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, ToolResultMessage) and msg.metadata:
                for att in msg.metadata.get("attachments", []):
                    if isinstance(att, dict) and "base64" in att:
                        attachments.append(att)
        return attachments

    def _assistant_text(self, message: AssistantMessage) -> str:
        parts = [block.text for block in message.content if isinstance(block, TextContent) and block.text]
        return "\n".join(parts)

    def _sanitize_tool_call_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
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
    def user_text(content: str | list[TextContent | ImageContent]) -> str:
        return SentinelRuntimeSupport._user_text(content)

    @staticmethod
    def _user_text(content: str | list[TextContent | ImageContent]) -> str:
        if isinstance(content, str):
            return content
        parts = [block.text for block in content if isinstance(block, TextContent) and block.text]
        return "\n".join(parts)
