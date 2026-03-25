"""Adapter layer between model-emitted tool calls and tool executor.

Validates payloads, normalizes execution metadata, and returns typed
ToolResultMessage objects for reinjection/persistence.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Message
from app.services.agent.agent_modes import AgentMode
from app.services.estop import EstopService
from app.services.llm.generic.credential_scrubber import scrub
from app.services.llm.generic.types import ToolCallContent, ToolResultMessage, ToolSchema
from app.services.tools.executor import ToolExecutor
from app.services.tools.approval.extractors import extract_approval_metadata_from_tool_result
from app.services.tools.registry import ToolRegistry, ToolRuntimeContext

MAX_TOOL_RESULT_BYTES = 50_000
MAX_INLINE_IMAGE_BASE64_CHARS = 2_000_000
_MODEL_RESULT_STRIP_ROOT_FIELDS = frozenset({"session_id"})
logger = logging.getLogger(__name__)


class ToolExecutionCancelled(RuntimeError):
    """Raised when outer run cancellation happens during tool execution."""

    def __init__(self, results: list[ToolResultMessage]) -> None:
        super().__init__("Tool execution cancelled")
        self.results = results


class ToolAdapter:
    """Translate model tool calls into executor invocations and safe tool results."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        estop_service: EstopService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._estop = estop_service or EstopService()
        self._session_factory = session_factory

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def executor(self) -> ToolExecutor:
        return self._executor

    def get_tool_schemas(self) -> list[ToolSchema]:
        schemas: list[ToolSchema] = []
        for tool in self._registry.list_all():
            if not tool.enabled:
                continue
            schemas.append(
                ToolSchema(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters_schema,
                )
            )
        return schemas

    async def execute_tool_calls(
        self,
        calls: list[ToolCallContent],
        db: Any,
        *,
        session_id: UUID | str | None = None,
        agent_mode: AgentMode | str | None = None,
        on_pending_tool_result: Any = None,
    ) -> list[ToolResultMessage]:
        """Execute all tool calls for a turn and return normalized result messages."""
        tasks = [
            asyncio.create_task(
                self._execute_one(
                call,
                db,
                session_id=session_id,
                agent_mode=agent_mode,
                on_pending_tool_result=on_pending_tool_result,
            )
            )
            for call in calls
        ]
        cancelled_during_wait = False
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            cancelled_during_wait = True
            for task in tasks:
                if not task.done():
                    task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)

        output = self._normalize_execution_results(calls, results)
        if cancelled_during_wait:
            raise ToolExecutionCancelled(output)
        return output

    def _normalize_execution_results(
        self,
        calls: list[ToolCallContent],
        results: list[Any],
    ) -> list[ToolResultMessage]:
        output: list[ToolResultMessage] = []
        for call, item in zip(calls, results, strict=False):
            if isinstance(item, ToolResultMessage):
                output.append(item)
                continue
            if isinstance(item, asyncio.CancelledError):
                output.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=scrub(self._truncate_content('{"status":"cancelled","message":"Tool call cancelled."}')),
                        is_error=True,
                    )
                )
                continue
            if isinstance(item, Exception):
                output.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        tool_name=call.name,
                        content=scrub(self._truncate_content(str(item))),
                        is_error=True,
                    )
                )
                continue
            output.append(
                ToolResultMessage(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    content=scrub(self._truncate_content(str(item))),
                    is_error=False,
                )
            )
        return output

    async def _execute_one(
        self,
        call: ToolCallContent,
        db: Any,
        *,
        session_id: UUID | str | None,
        agent_mode: AgentMode | str | None,
        on_pending_tool_result: Any = None,
    ) -> ToolResultMessage:
        """Execute a single tool call with estop enforcement and consistent error wrapping."""
        pending_message_id: str | None = None

        try:
            tool = self._registry.get(call.name)
            if tool is None:
                raise KeyError(call.name)

            if self._session_factory is not None:
                async with self._session_factory() as estop_db:
                    await self._estop.enforce_tool(estop_db, call.name)
            else:
                await self._estop.enforce_tool(db, call.name)
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            payload = dict(arguments)
            runtime = ToolRuntimeContext(
                session_id=session_id if isinstance(session_id, UUID) else (UUID(str(session_id)) if session_id is not None else None)
            )

            async def _on_pending_approval(approval_payload: dict[str, Any]) -> None:
                nonlocal pending_message_id
                pending_result = self._build_pending_tool_result_message(call, approval_payload)
                if self._session_factory is not None and session_id is not None:
                    pending_message_id = await self._persist_tool_result_message(
                        session_id=session_id,
                        tool_result=pending_result,
                    )
                if callable(on_pending_tool_result):
                    await on_pending_tool_result(pending_result)

            result, _duration_ms = await self._executor.execute(
                call.name,
                payload,
                runtime=runtime,
                agent_mode=agent_mode,
                on_pending_approval=_on_pending_approval,
            )
            truncated, metadata = self._prepare_content_and_metadata(call.name, result)
            if pending_message_id is not None:
                metadata["__persisted_message_id"] = pending_message_id
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=scrub(truncated),
                is_error=False,
                metadata=metadata,
            )
        except KeyError:
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=scrub(self._truncate_content(f"Tool '{call.name}' is not registered")),
                is_error=True,
            )
        except asyncio.CancelledError:
            metadata: dict[str, Any] = {}
            if pending_message_id is not None:
                metadata["__persisted_message_id"] = pending_message_id
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=scrub(
                    self._truncate_content(
                        json.dumps(
                            {
                                "status": "cancelled",
                                "message": "Tool call cancelled while waiting for approval.",
                            }
                        )
                    )
                ),
                is_error=True,
                metadata=metadata,
            )
        except Exception as exc:  # noqa: BLE001
            metadata: dict[str, Any] = {}
            if pending_message_id is not None:
                metadata["__persisted_message_id"] = pending_message_id
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=scrub(self._truncate_content(str(exc))),
                is_error=True,
                metadata=metadata,
            )

    def _prepare_content_and_metadata(self, tool_name: str, result: Any) -> tuple[str, dict[str, Any]]:
        """Serialize tool output and extract rich attachments into metadata."""
        attachments: list[dict[str, Any]] = []
        safe_result = self._extract_attachments(result, attachments=attachments)
        safe_result = self._strip_root_context_fields(safe_result)
        serialized = json.dumps(safe_result, default=str)
        truncated = self._truncate_content(serialized)
        metadata: dict[str, Any] = {}
        if attachments:
            metadata["attachments"] = attachments
        approval = extract_approval_metadata_from_tool_result(tool_name=tool_name, result=safe_result)
        if isinstance(approval, dict):
            metadata["approval"] = approval
            metadata["pending"] = bool(approval.get("pending"))
            logger.info(
                "tool_result_approval_metadata tool=%s provider=%s approval_id=%s status=%s pending=%s can_resolve=%s",
                tool_name,
                approval.get("provider"),
                approval.get("approval_id"),
                approval.get("status"),
                approval.get("pending"),
                approval.get("can_resolve"),
            )
        return truncated, metadata

    def _build_pending_tool_result_message(
        self,
        call: ToolCallContent,
        approval_payload: dict[str, Any],
    ) -> ToolResultMessage:
        content = json.dumps(
            {
                "status": "pending",
                "message": approval_payload.get("description") or "Action requires approval.",
                "approval": approval_payload,
            },
            default=str,
        )
        return ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=scrub(self._truncate_content(content)),
            is_error=False,
            metadata={
                "approval": approval_payload,
                "pending": True,
            },
        )

    async def _persist_tool_result_message(
        self,
        *,
        session_id: UUID | str,
        tool_result: ToolResultMessage,
    ) -> str:
        if self._session_factory is None:
            raise RuntimeError("ToolAdapter session_factory is required to persist pending tool results.")
        normalized_session_id = session_id if isinstance(session_id, UUID) else UUID(str(session_id))
        async with self._session_factory() as db:
            row = Message(
                session_id=normalized_session_id,
                role="tool_result",
                content=tool_result.content,
                metadata_json=dict(tool_result.metadata or {}),
                tool_call_id=tool_result.tool_call_id or None,
                tool_name=tool_result.tool_name or None,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return str(row.id)

    def _strip_root_context_fields(self, result: Any) -> Any:
        if not isinstance(result, dict):
            return result
        return {
            key: value
            for key, value in result.items()
            if key not in _MODEL_RESULT_STRIP_ROOT_FIELDS
        }

    def _extract_attachments(
        self,
        value: Any,
        *,
        attachments: list[dict[str, Any]],
        path: str = "",
        key_hint: str = "",
    ) -> Any:
        """Walk tool result payloads and lift base64 image blobs into attachment metadata."""
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else key
                cleaned[key] = self._extract_attachments(
                    item,
                    attachments=attachments,
                    path=child_path,
                    key_hint=key,
                )
            return cleaned

        if isinstance(value, list):
            return [
                self._extract_attachments(
                    item,
                    attachments=attachments,
                    path=f"{path}[{idx}]",
                    key_hint=key_hint,
                )
                for idx, item in enumerate(value)
            ]

        if isinstance(value, str):
            parsed = self._extract_image_payload(value, key_hint=key_hint)
            if parsed is None:
                return value
            attachment_value, mime_type, size_bytes = parsed
            if len(attachment_value) > MAX_INLINE_IMAGE_BASE64_CHARS:
                attachment_value = attachment_value[:MAX_INLINE_IMAGE_BASE64_CHARS]
            attachments.append(
                {
                    "path": path or key_hint or "payload",
                    "base64": attachment_value,
                    "mime_type": mime_type,
                    "size_bytes": size_bytes,
                    "sha256": hashlib.sha256(attachment_value.encode("ascii", errors="ignore")).hexdigest(),
                }
            )
            return f"[base64 image omitted from context: {len(value)} chars]"

        return value

    def _extract_image_payload(self, value: str, *, key_hint: str) -> tuple[str, str, int] | None:
        hint = key_hint.lower()
        if "image" not in hint and "screenshot" not in hint and "base64" not in hint:
            return None

        payload = value.strip()
        declared_mime: str | None = None
        if payload.startswith("data:image/"):
            comma_idx = payload.find(",")
            if comma_idx == -1:
                return None
            header = payload[:comma_idx]
            declared_mime = header[5:].split(";")[0].strip().lower()
            payload = payload[comma_idx + 1 :]

        payload = "".join(payload.split())
        if len(payload) < 64:
            return None
        if len(payload) % 4 != 0:
            return None

        try:
            decoded = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return None

        detected_mime = self._detect_image_mime(decoded)
        if detected_mime is None:
            return None
        mime_type = declared_mime if declared_mime and declared_mime.startswith("image/") else detected_mime
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"
        return (payload, mime_type, len(decoded))

    def _detect_image_mime(self, data: bytes) -> str | None:
        if len(data) < 12:
            return None
        is_png = data[:4] == b"\x89PNG"
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        is_gif = data[:4] in (b"GIF8",)
        is_webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        if is_png:
            return "image/png"
        if is_jpeg:
            return "image/jpeg"
        if is_gif:
            return "image/gif"
        if is_webp:
            return "image/webp"
        return None

    def _truncate_content(self, content: str) -> str:
        encoded = content.encode("utf-8", errors="replace")
        total_bytes = len(encoded)
        if total_bytes <= MAX_TOOL_RESULT_BYTES:
            return content

        head = encoded[:MAX_TOOL_RESULT_BYTES].decode("utf-8", errors="replace")
        return f"{head}\n...[TRUNCATED - {total_bytes} bytes total]"
