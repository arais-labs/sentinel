from __future__ import annotations

import asyncio

import pytest

from app.services.tools.executor import ToolExecutionError, ToolExecutor
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
)


def _run(coro):
    return asyncio.run(coro)


def _tool_with_check(*, approval_check=None) -> ToolDefinition:
    async def _execute(payload: dict[str, object]) -> dict[str, object]:
        return {
            "ok": True,
            "payload": dict(payload),
        }

    return ToolDefinition(
        name="gated_tool",
        description="Test gated tool",
        parameters_schema={"type": "object", "properties": {}, "required": []},
        execute=_execute,
        approval_check=approval_check,
    )


def _executor_for(
    tool: ToolDefinition,
    *,
    approval_waiter=None,
    approval_result_recorder=None,
) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(
        registry,
        approval_waiter=approval_waiter,
        approval_result_recorder=approval_result_recorder,
    )


def test_allow_check_skips_waiter():
    waiter_called = False

    async def _waiter(_tool_name, _payload, _requirement, _pending_callback=None):
        nonlocal waiter_called
        waiter_called = True
        raise AssertionError("waiter should not be called for allow decision")

    tool = _tool_with_check(
        approval_check=lambda: ToolApprovalEvaluation.allow(),
    )
    executor = _executor_for(tool, approval_waiter=_waiter)

    result, _ = _run(executor.execute("gated_tool", {}))

    assert waiter_called is False
    assert result["ok"] is True
    assert "approval" not in result


def test_require_check_calls_generic_waiter():
    waiter_called = False

    async def _waiter(_tool_name, _payload, _requirement, _pending_callback=None):
        nonlocal waiter_called
        waiter_called = True
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                "provider": "gated_tool",
                "approval_id": "apr_required",
                "status": "approved",
                "pending": False,
                "can_resolve": False,
            },
            message="approved",
        )

    tool = _tool_with_check(
        approval_check=lambda: ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action="gated_tool.execute",
                description="Needs approval",
            )
        ),
    )
    executor = _executor_for(tool, approval_waiter=_waiter)

    result, _ = _run(executor.execute("gated_tool", {}))

    assert waiter_called is True
    assert result["ok"] is True
    assert result["approval"]["approval_id"] == "apr_required"


def test_require_check_rejects_when_waiter_rejects():
    async def _waiter(_tool_name, _payload, _requirement, _pending_callback=None):
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.REJECTED,
            approval={"provider": "gated_tool", "approval_id": "apr_reject"},
            message="Approval rejected.",
        )

    tool = _tool_with_check(
        approval_check=lambda: ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action="gated_tool.execute",
                description="Needs approval",
            )
        ),
    )
    executor = _executor_for(tool, approval_waiter=_waiter)

    with pytest.raises(ToolExecutionError, match="Approval rejected"):
        _run(executor.execute("gated_tool", {}))


def test_full_permission_mode_auto_approves_required_check_without_waiter_call():
    waiter_called = False

    async def _waiter(_tool_name, _payload, _requirement):
        nonlocal waiter_called
        waiter_called = True
        raise AssertionError("waiter should not be called in full_permission mode")

    tool = _tool_with_check(
        approval_check=lambda: ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action="gated_tool.execute",
                description="Needs approval",
            )
        ),
    )
    executor = _executor_for(tool, approval_waiter=_waiter)

    result, _ = _run(
        executor.execute(
            "gated_tool",
            {},
            agent_mode="full_permission",
        )
    )

    assert waiter_called is False
    assert result["ok"] is True
    approval = result.get("approval") or {}
    assert approval.get("status") == "approved"
    assert approval.get("pending") is False
    assert approval.get("provider") == "gated_tool"


def test_executor_records_approved_result_with_generic_recorder():
    recorded: list[tuple[str, object]] = []

    async def _waiter(_tool_name, _payload, _requirement, _pending_callback=None):
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={
                "provider": "gated_tool",
                "approval_id": "apr_recorded",
                "status": "approved",
                "pending": False,
                "can_resolve": False,
            },
            message="approved",
        )

    async def _record(approval_id: str, result: object) -> None:
        recorded.append((approval_id, result))

    tool = _tool_with_check(
        approval_check=lambda: ToolApprovalEvaluation.require(
            ToolApprovalRequirement(
                action="gated_tool.execute",
                description="Needs approval",
            )
        ),
    )
    executor = _executor_for(
        tool,
        approval_waiter=_waiter,
        approval_result_recorder=_record,
    )

    result, _ = _run(executor.execute("gated_tool", {}))

    assert result["ok"] is True
    assert recorded == [("apr_recorded", result)]
