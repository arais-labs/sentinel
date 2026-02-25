from __future__ import annotations

import asyncio
import base64
import binascii
import json
from typing import Any
from uuid import UUID

from app.services.estop import EstopService
from app.services.llm.credential_scrubber import scrub
from app.services.llm.types import ToolCallContent, ToolResultMessage, ToolSchema
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry

MAX_TOOL_RESULT_BYTES = 50_000
MAX_INLINE_IMAGE_BASE64_CHARS = 2_000_000


class ToolAdapter:
    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        estop_service: EstopService | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._estop = estop_service or EstopService()

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
        allow_high_risk: bool = False,
    ) -> list[ToolResultMessage]:
        tasks = [
            self._execute_one(
                call,
                db,
                allow_high_risk=allow_high_risk,
                session_id=session_id,
            )
            for call in calls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output: list[ToolResultMessage] = []
        for call, item in zip(calls, results, strict=False):
            if isinstance(item, ToolResultMessage):
                output.append(item)
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
        allow_high_risk: bool,
        session_id: UUID | str | None,
    ) -> ToolResultMessage:
        try:
            tool = self._registry.get(call.name)
            if tool is None:
                raise KeyError(call.name)

            await self._estop.enforce_tool(db, call.name, tool.risk_level)
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            payload = dict(arguments)
            schema_properties = tool.parameters_schema.get("properties", {}) if tool.parameters_schema else {}
            supports_session_id = isinstance(schema_properties, dict) and "session_id" in schema_properties
            if session_id is not None and supports_session_id and "session_id" not in payload:
                payload["session_id"] = str(session_id)
            result, _duration_ms = await self._executor.execute(
                call.name,
                payload,
                allow_high_risk=allow_high_risk,
            )
            truncated, metadata = self._prepare_content_and_metadata(result)
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
        except Exception as exc:  # noqa: BLE001
            return ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=scrub(self._truncate_content(str(exc))),
                is_error=True,
            )

    def _prepare_content_and_metadata(self, result: Any) -> tuple[str, dict[str, Any]]:
        attachments: list[dict[str, str]] = []
        safe_result = self._extract_attachments(result, attachments=attachments)
        serialized = json.dumps(safe_result, default=str)
        truncated = self._truncate_content(serialized)
        metadata: dict[str, Any] = {}
        if attachments:
            metadata["attachments"] = attachments
        return truncated, metadata

    def _extract_attachments(
        self,
        value: Any,
        *,
        attachments: list[dict[str, str]],
        path: str = "",
        key_hint: str = "",
    ) -> Any:
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

        if isinstance(value, str) and self._looks_like_image_base64(value, key_hint):
            attachment_value = value
            if len(attachment_value) > MAX_INLINE_IMAGE_BASE64_CHARS:
                attachment_value = attachment_value[:MAX_INLINE_IMAGE_BASE64_CHARS]
            attachments.append(
                {
                    "path": path or key_hint or "payload",
                    "base64": attachment_value,
                }
            )
            return f"[base64 image omitted from context: {len(value)} chars]"

        return value

    def _looks_like_image_base64(self, value: str, key_hint: str) -> bool:
        hint = key_hint.lower()
        if "image" not in hint and "screenshot" not in hint and "base64" not in hint:
            return False

        payload = value.strip()
        if payload.startswith("data:image/"):
            comma_idx = payload.find(",")
            if comma_idx == -1:
                return False
            payload = payload[comma_idx + 1 :]

        payload = "".join(payload.split())
        if len(payload) < 64:
            return False
        if len(payload) % 4 != 0:
            return False

        try:
            decoded = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            return False
        return self._is_known_image(decoded)

    def _is_known_image(self, data: bytes) -> bool:
        if len(data) < 12:
            return False
        is_png = data[:4] == b"\x89PNG"
        is_jpeg = data[:3] == b"\xff\xd8\xff"
        is_gif = data[:4] in (b"GIF8",)
        is_webp = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        return is_png or is_jpeg or is_gif or is_webp

    def _truncate_content(self, content: str) -> str:
        encoded = content.encode("utf-8", errors="replace")
        total_bytes = len(encoded)
        if total_bytes <= MAX_TOOL_RESULT_BYTES:
            return content

        head = encoded[:MAX_TOOL_RESULT_BYTES].decode("utf-8", errors="replace")
        return f"{head}\n...[TRUNCATED - {total_bytes} bytes total]"
