from __future__ import annotations

import inspect
import logging
import time
from typing import Any

from app.services.agent.agent_modes import AgentMode, get_agent_mode_definition
from app.services.tools.registry import (
    ToolApprovalDecision,
    ToolApprovalEvaluation,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolApprovalResultRecorderFn,
    ToolApprovalWaiterFn,
    ToolDefinition,
    ToolRegistry,
)

logger = logging.getLogger(__name__)


class ToolExecutionError(RuntimeError):
    pass


class ToolValidationError(ValueError):
    pass


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        approval_waiter: ToolApprovalWaiterFn | None = None,
        approval_result_recorder: ToolApprovalResultRecorderFn | None = None,
    ) -> None:
        self._registry = registry
        self._approval_waiter = approval_waiter
        self._approval_result_recorder = approval_result_recorder

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        agent_mode: AgentMode | str | None = None,
        on_pending_approval: Any = None,
    ) -> tuple[Any, int]:
        tool = self._registry.get(name)
        if tool is None:
            raise KeyError(name)
        if not self._registry.is_allowed(name):
            raise PermissionError(f"Tool '{name}' is disabled")

        self._validate_payload(tool, payload)
        mode_definition = get_agent_mode_definition(agent_mode)
        approved_metadata = await self._resolve_tool_approval(
            tool,
            payload,
            auto_approve_required=mode_definition.auto_approve_tool_gates,
            on_pending_approval=on_pending_approval,
        )

        started = time.perf_counter()
        try:
            result = await tool.execute(payload)
        except ToolValidationError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper
            await self._record_approval_result(
                approval=approved_metadata,
                result={"ok": False, "error": str(exc)},
            )
            raise ToolExecutionError(str(exc)) from exc
        await self._record_approval_result(approval=approved_metadata, result=result)
        if approved_metadata and isinstance(result, dict) and not isinstance(result.get("approval"), dict):
            result["approval"] = approved_metadata
        duration_ms = int((time.perf_counter() - started) * 1000)
        return result, max(duration_ms, 0)

    async def _record_approval_result(
        self,
        *,
        approval: dict[str, Any] | None,
        result: Any,
    ) -> None:
        if self._approval_result_recorder is None or not isinstance(approval, dict):
            return
        approval_id = approval.get("approval_id")
        if not isinstance(approval_id, str) or not approval_id.strip():
            return
        await self._approval_result_recorder(approval_id.strip(), result)

    async def _resolve_tool_approval(
        self,
        tool: ToolDefinition,
        payload: dict[str, Any],
        *,
        auto_approve_required: bool,
        on_pending_approval: Any = None,
    ) -> dict[str, Any] | None:
        approval_check = tool.approval_check
        if approval_check is None:
            return None

        evaluation = await self._run_approval_check(tool.name, approval_check, payload)
        requirement = evaluation.requirement
        logger.info(
            "tool_approval_eval tool=%s decision=%s action=%s session_id=%s",
            tool.name,
            evaluation.decision.value,
            requirement.action if requirement is not None else None,
            payload.get("session_id"),
        )
        if evaluation.decision == ToolApprovalDecision.ALLOW:
            return None
        if evaluation.decision == ToolApprovalDecision.DENY:
            message = (evaluation.reason or "").strip() or "Execution denied by approval policy."
            raise ToolExecutionError(message)

        requirement = evaluation.requirement
        if requirement is None:
            raise ToolExecutionError("Approval gate requires a request descriptor before execution.")
        if auto_approve_required:
            approval_payload = {
                "provider": tool.name,
                "approval_id": f"auto:{tool.name}:{int(time.time() * 1000)}",
                "status": ToolApprovalOutcomeStatus.APPROVED.value,
                "pending": False,
                "can_resolve": False,
                "label": f"{tool.name} approval",
                "action": requirement.action,
                "description": requirement.description,
                "decision_note": "Auto-approved by agent mode full_permission",
            }
            if requirement.requested_by:
                approval_payload["decision_by"] = requirement.requested_by
            logger.info(
                "tool_approval_auto_approved tool=%s action=%s",
                tool.name,
                requirement.action,
            )
            return approval_payload
        if requirement.timeout_seconds < 1:
            raise ToolExecutionError("Approval timeout must be a positive integer.")
        if self._approval_waiter is None:
            raise ToolExecutionError(f"Tool '{tool.name}' requires approval but no waiter is configured.")

        logger.info(
            "tool_approval_wait tool=%s action=%s timeout_seconds=%s requested_by=%s",
            tool.name,
            requirement.action,
            requirement.timeout_seconds,
            requirement.requested_by,
        )
        outcome = await self._approval_waiter(tool.name, payload, requirement, on_pending_approval)
        approval_payload = dict(outcome.approval)
        if not isinstance(approval_payload.get("provider"), str):
            approval_payload["provider"] = tool.name
        if not isinstance(approval_payload.get("status"), str):
            approval_payload["status"] = outcome.status.value
        if "pending" not in approval_payload:
            approval_payload["pending"] = False
        if "can_resolve" not in approval_payload:
            approval_payload["can_resolve"] = False
        logger.info(
            "tool_approval_outcome tool=%s status=%s provider=%s approval_id=%s pending=%s can_resolve=%s",
            tool.name,
            outcome.status.value,
            approval_payload.get("provider"),
            approval_payload.get("approval_id"),
            approval_payload.get("pending"),
            approval_payload.get("can_resolve"),
        )
        if outcome.status != ToolApprovalOutcomeStatus.APPROVED:
            message = (outcome.message or "").strip() or f"Approval {outcome.status.value}."
            raise ToolExecutionError(message)
        return approval_payload

    async def _run_approval_check(
        self,
        tool_name: str,
        approval_check: Any,
        payload: dict[str, Any],
    ) -> ToolApprovalEvaluation:
        evaluator_signature = inspect.signature(approval_check)
        positional_params = [
            parameter
            for parameter in evaluator_signature.parameters.values()
            if parameter.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }
        ]
        if positional_params:
            evaluated = approval_check(payload)
        else:
            evaluated = approval_check()
        if inspect.isawaitable(evaluated):
            evaluated = await evaluated
        if not isinstance(evaluated, ToolApprovalEvaluation):
            raise ToolExecutionError(
                f"Tool '{tool_name}' approval check returned invalid response type."
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
