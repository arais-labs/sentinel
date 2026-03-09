from __future__ import annotations

import asyncio

import pytest

from app.services.tools.executor import ToolExecutionError, ToolExecutor
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalGate,
    ToolApprovalMode,
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
)


def _run(coro):
    return asyncio.run(coro)


def _tool_with_gate(
    *,
    approval_gate: ToolApprovalGate,
) -> ToolDefinition:
    async def _execute(payload: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "gate_seen": isinstance(payload.get("__approval_gate"), dict),
        }

    return ToolDefinition(
        name="gated_tool",
        risk_level="low",
        description="Test gated tool",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        execute=_execute,
        approval_gate=approval_gate,
    )


def _executor_for(tool: ToolDefinition) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry)


def test_required_gate_cannot_be_bypassed_by_allow_evaluator():
    waiter_called = False

    async def _waiter(_tool_name, _payload, _requirement):
        nonlocal waiter_called
        waiter_called = True
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                "provider": "tool",
                "approval_id": "apr_required",
                "status": "approved",
                "pending": False,
                "can_resolve": False,
            },
            message="approved",
        )

    gate = ToolApprovalGate(
        mode=ToolApprovalMode.REQUIRED,
        evaluator=lambda _payload: ToolApprovalEvaluation.allow(),
        waiter=_waiter,
    )
    tool = _tool_with_gate(approval_gate=gate)
    executor = _executor_for(tool)

    result, _ = _run(executor.execute("gated_tool", {}, allow_high_risk=True))

    assert waiter_called is True
    assert result["ok"] is True
    assert result["gate_seen"] is True
    assert result["approval"]["approval_id"] == "apr_required"


def test_conditional_gate_allow_skips_waiter():
    async def _waiter(_tool_name, _payload, _requirement):  # pragma: no cover - defensive
        raise AssertionError("waiter should not be called for allow decision")

    gate = ToolApprovalGate(
        mode=ToolApprovalMode.CONDITIONAL,
        evaluator=lambda _payload: ToolApprovalEvaluation.allow(),
        waiter=_waiter,
    )
    tool = _tool_with_gate(approval_gate=gate)
    executor = _executor_for(tool)

    result, _ = _run(executor.execute("gated_tool", {}, allow_high_risk=True))

    assert result["ok"] is True
    assert result["gate_seen"] is False
    assert "approval" not in result


def test_conditional_gate_rejects_when_waiter_rejects():
    async def _waiter(_tool_name, _payload, _requirement):
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.REJECTED,
            approval={"provider": "tool", "approval_id": "apr_reject"},
            message="Approval rejected.",
        )

    gate = ToolApprovalGate(
        mode=ToolApprovalMode.CONDITIONAL,
        evaluator=lambda _payload: ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action="gated_tool.execute",
                description="Needs approval",
            )
        ),
        waiter=_waiter,
    )
    tool = _tool_with_gate(approval_gate=gate)
    executor = _executor_for(tool)

    with pytest.raises(ToolExecutionError, match="Approval rejected"):
        _run(executor.execute("gated_tool", {}, allow_high_risk=True))


def test_full_permission_mode_auto_approves_required_gate_without_waiter_call():
    waiter_called = False

    async def _waiter(_tool_name, _payload, _requirement):
        nonlocal waiter_called
        waiter_called = True
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={"provider": "tool", "approval_id": "apr_should_not_be_used"},
            message="approved",
        )

    gate = ToolApprovalGate(
        mode=ToolApprovalMode.CONDITIONAL,
        evaluator=lambda _payload: ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action="gated_tool.execute",
                description="Needs approval",
            )
        ),
        waiter=_waiter,
    )
    tool = _tool_with_gate(approval_gate=gate)
    executor = _executor_for(tool)

    result, _ = _run(
        executor.execute(
            "gated_tool",
            {},
            allow_high_risk=True,
            agent_mode="full_permission",
        )
    )

    assert waiter_called is False
    assert result["ok"] is True
    approval = result.get("approval") or {}
    assert approval.get("status") == "approved"
    assert approval.get("pending") is False
    assert approval.get("provider") == "tool"
