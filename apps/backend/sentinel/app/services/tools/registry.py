from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Awaitable, Callable
from uuid import UUID


@dataclass(slots=True)
class ToolRuntimeContext:
    session_id: UUID | None = None


ToolExecutorFn = Callable[[dict[str, Any], ToolRuntimeContext], Awaitable[Any]]
ToolApprovalCheckFn = (
    Callable[[], "ToolApprovalEvaluation" | Awaitable["ToolApprovalEvaluation"]]
    | Callable[[dict[str, Any]], "ToolApprovalEvaluation" | Awaitable["ToolApprovalEvaluation"]]
    | Callable[[dict[str, Any], ToolRuntimeContext], "ToolApprovalEvaluation" | Awaitable["ToolApprovalEvaluation"]]
)
ToolApprovalPendingFn = Callable[[dict[str, Any]], Awaitable[None]]
ToolApprovalWaiterFn = Callable[
    [str, dict[str, Any], ToolRuntimeContext, "ToolApprovalRequirement", ToolApprovalPendingFn | None],
    "Awaitable[ToolApprovalOutcome]",
]
ToolApprovalResultRecorderFn = Callable[[str, Any], Awaitable[None]]


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
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any]
    execute: ToolExecutorFn
    enabled: bool = True
    approval_check: ToolApprovalCheckFn | None = None


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
