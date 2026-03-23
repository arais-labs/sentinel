"""Builds the ToolRegistry from system modules.

Reads SYSTEM_MODULES, converts each action to a ToolDefinition using
the handler function directly attached to the action.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.araios.module_types import ApprovalDefinition
from app.services.araios.system_modules import SYSTEM_MODULES
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

    for module in SYSTEM_MODULES:
        actions = module.actions or []
        for action in actions:
            if not action.handler:
                continue

            gate = _resolve_gate(
                action.approval,
                handler_key=f"{module.name}.{action.id}",
                waiter=waiter,
            )

            tool_def = action.to_tool_definition(
                module_name=module.name,
                module_description=module.description,
                action_count=len(actions),
                approval_gate=gate,
            )
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
