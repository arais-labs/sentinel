from __future__ import annotations

import time
from typing import Any

from app.services.tools.registry import ToolDefinition, ToolRegistry


class ToolExecutionError(RuntimeError):
    pass


class ToolValidationError(ValueError):
    pass


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        allow_high_risk: bool,
    ) -> tuple[Any, int]:
        tool = self._registry.get(name)
        if tool is None:
            raise KeyError(name)
        if not self._registry.is_allowed(name):
            raise PermissionError(f"Tool '{name}' is disabled")
        if tool.risk_level == "high" and not allow_high_risk:
            raise PermissionError("High-risk tool execution disabled for this run context")

        self._validate_payload(tool, payload)

        started = time.perf_counter()
        try:
            result = await tool.execute(payload)
        except ToolValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise ToolExecutionError(str(exc)) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        return result, max(duration_ms, 0)

    def _validate_payload(self, tool: ToolDefinition, payload: dict[str, Any]) -> None:
        schema = tool.parameters_schema or {}
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional_properties = schema.get("additionalProperties", True)

        if not isinstance(payload, dict):
            raise ToolValidationError("Input payload must be an object")

        missing = [field for field in required if field not in payload]
        if missing:
            raise ToolValidationError(f"Missing required field(s): {', '.join(missing)}")

        if not additional_properties:
            unknown = [field for field in payload.keys() if field not in properties]
            if unknown:
                raise ToolValidationError(f"Unknown field(s): {', '.join(unknown)}")

        for field_name, field_schema in properties.items():
            if field_name not in payload:
                continue
            self._validate_field(field_name, payload[field_name], field_schema)

    def _validate_field(self, field_name: str, value: Any, field_schema: dict[str, Any]) -> None:
        expected_type = field_schema.get("type")
        if expected_type:
            if expected_type == "string" and not isinstance(value, str):
                raise ToolValidationError(f"Field '{field_name}' must be a string")
            if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ToolValidationError(f"Field '{field_name}' must be an integer")
            if expected_type == "boolean" and not isinstance(value, bool):
                raise ToolValidationError(f"Field '{field_name}' must be a boolean")
            if expected_type == "object" and not isinstance(value, dict):
                raise ToolValidationError(f"Field '{field_name}' must be an object")
            if expected_type == "array" and not isinstance(value, list):
                raise ToolValidationError(f"Field '{field_name}' must be an array")

        enum = field_schema.get("enum")
        if enum and value not in enum:
            raise ToolValidationError(f"Field '{field_name}' must be one of: {', '.join(map(str, enum))}")
