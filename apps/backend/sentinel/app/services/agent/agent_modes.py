from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services.agent.policies import (
    AgentModePolicy,
    FULL_PERMISSION_POLICY,
    READ_ONLY_POLICY,
    CODE_REVIEW_POLICY,
    INTERACTIVE_OUTPUT_POLICY,
)


class AgentMode(StrEnum):
    NORMAL = "normal"
    FULL_PERMISSION = "full_permission"
    READ_ONLY = "read_only"
    CODE_REVIEW = "code_review"
    INTERACTIVE_OUTPUT = "interactive_output"


@dataclass(frozen=True, slots=True)
class AgentModeDefinition:
    id: AgentMode
    label: str
    description: str
    auto_approve_tool_gates: bool
    policy: AgentModePolicy | None = None


_DEFAULT_AGENT_MODE = AgentMode.NORMAL


_AGENT_MODE_DEFINITIONS: tuple[AgentModeDefinition, ...] = (
    AgentModeDefinition(
        id=AgentMode.NORMAL,
        label="Normal",
        description="Standard execution with approval gates enabled.",
        auto_approve_tool_gates=False,
        policy=None,
    ),
    AgentModeDefinition(
        id=AgentMode.FULL_PERMISSION,
        label="Full Permission",
        description="Auto-approves approval-gated tool actions.",
        auto_approve_tool_gates=True,
        policy=FULL_PERMISSION_POLICY,
    ),
    AgentModeDefinition(
        id=AgentMode.READ_ONLY,
        label="Read-Only",
        description="Investigation only. Do not modify files, state, or external systems.",
        auto_approve_tool_gates=False,
        policy=READ_ONLY_POLICY,
    ),
    AgentModeDefinition(
        id=AgentMode.CODE_REVIEW,
        label="Code Review",
        description="Review-first mode: analyze code for bugs/risks and report findings clearly.",
        auto_approve_tool_gates=False,
        policy=CODE_REVIEW_POLICY,
    ),
    AgentModeDefinition(
        id=AgentMode.INTERACTIVE_OUTPUT,
        label="Interactive Output",
        description="Assistant can render HTML artifacts in a sandboxed iframe; optionally uses auto-injected Sentinel theme components.",
        auto_approve_tool_gates=False,
        policy=INTERACTIVE_OUTPUT_POLICY,
    ),
)

_AGENT_MODE_MAP: dict[AgentMode, AgentModeDefinition] = {
    item.id: item for item in _AGENT_MODE_DEFINITIONS
}


def parse_agent_mode(value: AgentMode | str | None) -> AgentMode | None:
    if value is None:
        return None
    if isinstance(value, AgentMode):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    try:
        return AgentMode(normalized)
    except ValueError:
        return None


def get_default_agent_mode() -> AgentMode:
    return _DEFAULT_AGENT_MODE


def get_agent_mode_definition(value: AgentMode | str | None) -> AgentModeDefinition:
    parsed = parse_agent_mode(value) or _DEFAULT_AGENT_MODE
    return _AGENT_MODE_MAP[parsed]


def list_agent_mode_definitions() -> list[AgentModeDefinition]:
    return list(_AGENT_MODE_DEFINITIONS)


def normalize_agent_mode_value(value: AgentMode | str | None) -> str:
    return get_agent_mode_definition(value).id.value


def agent_mode_metadata(value: AgentMode | str | None) -> dict[str, Any]:
    mode = get_agent_mode_definition(value)
    return {"agent_mode": mode.id.value}
