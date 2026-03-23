"""Builds the ToolRegistry from system modules."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.araios.module_types import ApprovalDefinition
from app.services.araios.system_modules import get_system_modules
from app.services.tools.approval_waiters import build_tool_db_approval_waiter
from app.services.tools.registry import (
    ToolApprovalGate,
    ToolApprovalMode,
    ToolRegistry,
)


def build_default_registry(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ToolRegistry:
    """Build the tool registry from all system modules."""
    registry = ToolRegistry()
    waiter = (
        build_tool_db_approval_waiter(session_factory=session_factory)
        if session_factory
        else None
    )

    for module in get_system_modules():
        actions = [action for action in (module.actions or []) if action.handler]
        if not actions:
            continue

        action_gates = {
            action.id: _resolve_gate(
                action.approval,
                handler_key=f"{module.name}.{action.id}",
                waiter=waiter,
            )
            for action in actions
        }

        for tool_def in module.to_tool_definitions(action_gates=action_gates):
            registry.register(tool_def)

    return registry


def _resolve_gate(
    approval: ApprovalDefinition | dict[str, Any] | None,
    *,
    handler_key: str,
    waiter: Any,
) -> ToolApprovalGate | None:
    if approval is None:
        return None
    if isinstance(approval, dict):
        mode_raw = approval.get("mode")
        if not isinstance(mode_raw, str):
            return None
        mode = ToolApprovalMode(mode_raw)
        evaluator = approval.get("evaluator")
        gate_waiter = approval.get("waiter") if callable(approval.get("waiter")) else waiter
        if callable(evaluator):
            return ToolApprovalGate(mode=mode, evaluator=evaluator, waiter=gate_waiter)
        if mode == ToolApprovalMode.NONE:
            return None
        if mode == ToolApprovalMode.CONDITIONAL:
            raise ValueError(f"Conditional approval for {handler_key} requires a callable evaluator")
        return ToolApprovalGate(mode=mode, waiter=gate_waiter)

    mode = ToolApprovalMode(approval.mode)
    if mode == ToolApprovalMode.NONE:
        return None
    if mode == ToolApprovalMode.CONDITIONAL and not callable(approval.evaluator):
        raise ValueError(f"Conditional approval for {handler_key} requires a callable evaluator")
    return ToolApprovalGate(
        mode=mode,
        evaluator=approval.evaluator if callable(approval.evaluator) else None,
        waiter=approval.waiter if callable(approval.waiter) else waiter,
    )
