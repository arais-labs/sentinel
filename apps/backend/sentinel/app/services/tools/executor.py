from __future__ import annotations

import inspect
import time
from typing import Any

from app.services.tools.registry import (
    ToolApprovalDecision,
    ToolApprovalEvaluation,
    ToolApprovalGate,
    ToolApprovalMode,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
)


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
        approved_metadata = await self._resolve_tool_approval(tool, payload)
        if approved_metadata:
            payload["__approval_gate"] = approved_metadata

        started = time.perf_counter()
        try:
            result = await tool.execute(payload)
        except ToolValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise ToolExecutionError(str(exc)) from exc
        finally:
            payload.pop("__approval_gate", None)
        if approved_metadata and isinstance(result, dict) and not isinstance(result.get("approval"), dict):
            result["approval"] = approved_metadata
        duration_ms = int((time.perf_counter() - started) * 1000)
        return result, max(duration_ms, 0)

    async def _resolve_tool_approval(
        self,
        tool: ToolDefinition,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        gate = tool.approval_gate
        if gate is None or gate.mode == ToolApprovalMode.NONE:
            return None

        evaluation = await self._evaluate_gate(tool.name, gate, payload)
        if evaluation.decision == ToolApprovalDecision.ALLOW:
            return None
        if evaluation.decision == ToolApprovalDecision.DENY:
            message = (evaluation.reason or "").strip() or "Execution denied by approval policy."
            raise ToolExecutionError(message)

        requirement = evaluation.requirement
        if requirement is None:
            raise ToolExecutionError("Approval gate requires a request descriptor before execution.")
        if requirement.timeout_seconds < 1:
            raise ToolExecutionError("Approval timeout must be a positive integer.")
        if gate.waiter is None:
            raise ToolExecutionError(f"Tool '{tool.name}' requires approval but no waiter is configured.")

        outcome = await gate.waiter(tool.name, payload, requirement)
        approval_payload = dict(outcome.approval)
        if not isinstance(approval_payload.get("provider"), str):
            approval_payload["provider"] = "tool"
        if not isinstance(approval_payload.get("status"), str):
            approval_payload["status"] = outcome.status.value
        if "pending" not in approval_payload:
            approval_payload["pending"] = False
        if "can_resolve" not in approval_payload:
            approval_payload["can_resolve"] = False
        if outcome.status != ToolApprovalOutcomeStatus.APPROVED:
            message = (outcome.message or "").strip() or f"Approval {outcome.status.value}."
            raise ToolExecutionError(message)
        return approval_payload

    async def _evaluate_gate(
        self,
        tool_name: str,
        gate: ToolApprovalGate,
        payload: dict[str, Any],
    ) -> ToolApprovalEvaluation:
        if gate.mode == ToolApprovalMode.REQUIRED:
            evaluated = await self._run_gate_evaluator(tool_name, gate, payload)
            if evaluated is not None:
                if evaluated.decision == ToolApprovalDecision.DENY:
                    return evaluated
                if evaluated.decision == ToolApprovalDecision.REQUIRE and evaluated.requirement is not None:
                    return evaluated
            return ToolApprovalEvaluation.require(
                gate.required
                if gate.required is not None
                else ToolApprovalRequirement(
                    action=f"{tool_name}.execute",
                    description=f"{tool_name} execution requires approval.",
                )
            )

        if gate.mode == ToolApprovalMode.CONDITIONAL:
            if gate.evaluator is None:
                raise ToolExecutionError(f"Tool '{tool_name}' uses conditional approval without evaluator.")
            evaluated = await self._run_gate_evaluator(tool_name, gate, payload)
            if evaluated is None:
                raise ToolExecutionError(
                    f"Tool '{tool_name}' approval evaluator returned invalid response type."
                )
            return evaluated

        return ToolApprovalEvaluation.allow()

    async def _run_gate_evaluator(
        self,
        tool_name: str,
        gate: ToolApprovalGate,
        payload: dict[str, Any],
    ) -> ToolApprovalEvaluation | None:
        if gate.evaluator is None:
            return None
        evaluated = gate.evaluator(payload)
        if inspect.isawaitable(evaluated):
            evaluated = await evaluated
        if not isinstance(evaluated, ToolApprovalEvaluation):
            raise ToolExecutionError(
                f"Tool '{tool_name}' approval evaluator returned invalid response type."
            )
        return evaluated

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
