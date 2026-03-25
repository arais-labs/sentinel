"""Adapters from Sentinel tool infrastructure to standalone runtime contracts."""

from __future__ import annotations
from collections.abc import Awaitable, Callable
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
from app.services.tools.registry import ToolDefinition, ToolRegistry, ToolRuntimeContext


class SentinelToolRegistryAdapter(RuntimeToolRegistry):
    """Expose Sentinel's current registry/executor as runtime-neutral tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        agent_mode: AgentMode | str | None = None,
        session_id: UUID | str | None = None,
        on_pending_tool_result: Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._agent_mode = agent_mode
        self._session_id = str(session_id) if session_id is not None else None
        self._on_pending_tool_result = on_pending_tool_result

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
            execution_payload = dict(payload)
            runtime = ToolRuntimeContext(
                session_id=UUID(self._session_id) if self._session_id is not None else None
            )

            async def _on_pending_approval(approval_payload: dict[str, Any]) -> None:
                nonlocal pending_approval_payload
                pending_approval_payload = dict(approval_payload)
                if self._on_pending_tool_result is not None:
                    await self._on_pending_tool_result(tool.name, execution_payload, approval_payload)

            try:
                result, _duration_ms = await self._executor.execute(
                    tool.name,
                    execution_payload,
                    runtime=runtime,
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
            parameters_schema=tool.parameters_schema,
            enabled=tool.enabled,
            execute=_execute,
        )
