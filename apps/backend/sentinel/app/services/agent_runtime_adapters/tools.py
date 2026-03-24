"""Adapters from Sentinel tool infrastructure to standalone runtime contracts."""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID

from app.sentral import (
    ToolDefinition as RuntimeToolDefinition,
    ToolExecutionResult,
    ToolRegistry as RuntimeToolRegistry,
)
from app.services.agent.agent_modes import AgentMode
from app.services.agent_runtime_adapters.conversions import approval_payload_to_request
from app.services.tools.approval.extractors import extract_approval_metadata_from_tool_result
from app.services.tools.executor import ToolExecutionError, ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolDefinition, ToolRegistry


class SentinelToolRegistryAdapter(RuntimeToolRegistry):
    """Expose Sentinel's current registry/executor as runtime-neutral tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        agent_mode: AgentMode | str | None = None,
        session_id: UUID | str | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._agent_mode = agent_mode
        self._session_id = str(session_id) if session_id is not None else None

    def list_tools(self) -> list[RuntimeToolDefinition]:
        tools: list[RuntimeToolDefinition] = []
        for tool in self._registry.list_all():
            if not tool.enabled:
                continue
            tools.append(self._wrap_tool(tool))
        return tools

    def get_tool(self, name: str) -> RuntimeToolDefinition | None:
        tool = self._registry.get(name)
        if tool is None or not tool.enabled:
            return None
        return self._wrap_tool(tool)

    def _wrap_tool(self, tool: ToolDefinition) -> RuntimeToolDefinition:
        async def _execute(payload: dict[str, Any]) -> ToolExecutionResult:
            pending_approval_payload: dict[str, Any] | None = None
            execution_payload = self._execution_payload(tool, payload)

            async def _on_pending_approval(approval_payload: dict[str, Any]) -> None:
                nonlocal pending_approval_payload
                pending_approval_payload = dict(approval_payload)

            try:
                result, _duration_ms = await self._executor.execute(
                    tool.name,
                    execution_payload,
                    agent_mode=self._agent_mode,
                    on_pending_approval=_on_pending_approval,
                )
            except (ToolExecutionError, ToolValidationError, PermissionError, KeyError) as exc:
                if pending_approval_payload is not None:
                    return ToolExecutionResult(
                        status="pending_approval",
                        error=str(exc),
                        approval_request=approval_payload_to_request(
                            pending_approval_payload,
                            payload=execution_payload,
                        ),
                    )
                return ToolExecutionResult(
                    status="error",
                    error=str(exc),
                )

            metadata: dict[str, Any] = {}
            approval = extract_approval_metadata_from_tool_result(
                tool_name=tool.name,
                result=result,
            )
            if approval is not None:
                metadata["approval"] = approval
            return ToolExecutionResult(
                status="ok",
                content=result,
                metadata=metadata,
            )

        return RuntimeToolDefinition(
            name=tool.name,
            description=tool.description,
            parameters_schema=self._schema_for_model(tool.parameters_schema),
            enabled=tool.enabled,
            execute=_execute,
        )

    def _execution_payload(self, tool: ToolDefinition, payload: dict[str, Any]) -> dict[str, Any]:
        execution_payload = dict(payload)
        properties = tool.parameters_schema.get("properties", {}) if isinstance(tool.parameters_schema, dict) else {}
        if (
            isinstance(properties, dict)
            and "session_id" in properties
            and self._session_id is not None
            and "session_id" not in execution_payload
        ):
            execution_payload["session_id"] = self._session_id
        return execution_payload

    def _schema_for_model(self, raw_schema: Any) -> dict[str, Any]:
        if not isinstance(raw_schema, dict):
            return {}
        schema = copy.deepcopy(raw_schema)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("session_id", None)
        required = schema.get("required")
        if isinstance(required, list):
            filtered = [item for item in required if item != "session_id"]
            if filtered:
                schema["required"] = filtered
            else:
                schema.pop("required", None)
        return schema
