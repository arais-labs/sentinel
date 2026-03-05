from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable


ToolExecutorFn = Callable[[dict[str, Any]], Awaitable[Any]]
ToolApprovalEvaluatorFn = Callable[
    [dict[str, Any]],
    "ToolApprovalEvaluation" | Awaitable["ToolApprovalEvaluation"],
]
ToolApprovalWaiterFn = Callable[
    [str, dict[str, Any], "ToolApprovalRequirement"],
    "Awaitable[ToolApprovalOutcome]",
]


class ToolApprovalMode(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    CONDITIONAL = "conditional"


class ToolApprovalDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE = "require"
    DENY = "deny"


class ToolApprovalOutcomeStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ToolApprovalRequirement:
    action: str
    description: str
    timeout_seconds: int = 600
    match_key: str | None = None
    metadata: dict[str, Any] | None = None
    requested_by: str | None = None


@dataclass(slots=True)
class ToolApprovalEvaluation:
    decision: ToolApprovalDecision
    requirement: ToolApprovalRequirement | None = None
    reason: str | None = None

    @classmethod
    def allow(cls) -> "ToolApprovalEvaluation":
        return cls(decision=ToolApprovalDecision.ALLOW)

    @classmethod
    def require(cls, requirement: ToolApprovalRequirement) -> "ToolApprovalEvaluation":
        return cls(decision=ToolApprovalDecision.REQUIRE, requirement=requirement)

    @classmethod
    def deny(cls, reason: str) -> "ToolApprovalEvaluation":
        return cls(decision=ToolApprovalDecision.DENY, reason=reason)


@dataclass(slots=True)
class ToolApprovalOutcome:
    status: ToolApprovalOutcomeStatus
    approval: dict[str, Any]
    message: str | None = None


@dataclass(slots=True)
class ToolApprovalGate:
    mode: ToolApprovalMode
    evaluator: ToolApprovalEvaluatorFn | None = None
    waiter: ToolApprovalWaiterFn | None = None
    required: ToolApprovalRequirement | None = None


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    parameters_schema: dict[str, Any]
    execute: ToolExecutorFn
    enabled: bool = True
    approval_gate: ToolApprovalGate | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        return sorted(self._tools.values(), key=lambda item: item.name)

    def is_allowed(self, name: str) -> bool:
        tool = self.get(name)
        return bool(tool and tool.enabled)
