from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AgentMode(StrEnum):
    NORMAL = "normal"
    FULL_PERMISSION = "full_permission"
    READ_ONLY = "read_only"
    CODE_REVIEW = "code_review"


@dataclass(frozen=True, slots=True)
class AgentModePolicy:
    kind: str
    title: str
    explanation: str
    content: str


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
        policy=AgentModePolicy(
            kind="agent_mode_policy",
            title="Agent Mode Policy (Full Permission)",
            explanation="Mode-specific execution policy for this run.",
            content=(
                "## Agent Mode Policy: Full Permission\n"
                "This run is operating in Full Permission mode.\n"
                "Approval-gated actions are auto-approved by backend policy for this run.\n"
                "Proceed with normal execution and provide explicit traceability for high-impact actions."
            ),
        ),
    ),
    AgentModeDefinition(
        id=AgentMode.READ_ONLY,
        label="Read-Only",
        description="Investigation only. Do not modify files, state, or external systems.",
        auto_approve_tool_gates=False,
        policy=AgentModePolicy(
            kind="agent_mode_policy",
            title="Agent Mode Policy (Read-Only)",
            explanation="Mode-specific execution policy for this run.",
            content=(
                "## Agent Mode Policy: Read-Only\n"
                "This run is operating in Read-Only mode.\n"
                "Do not modify files, repositories, database state, configuration, or external systems.\n"
                "Do investigation, diagnostics, and reporting only.\n"
                "Do not output code patches or migration steps as completed actions."
            ),
        ),
    ),
    AgentModeDefinition(
        id=AgentMode.CODE_REVIEW,
        label="Code Review",
        description="Review-first mode: analyze code for bugs/risks and report findings clearly.",
        auto_approve_tool_gates=False,
        policy=AgentModePolicy(
            kind="agent_mode_policy",
            title="Agent Mode Policy (Code Review)",
            explanation="Mode-specific execution policy for this run.",
            content=(
                "## Agent Mode Policy: Code Review\n"
                "This run is operating in Code Review mode.\n"
                "Prioritize identifying bugs, regressions, reliability risks, security issues, and missing tests.\n"
                "Present findings first, ordered by severity, with concrete file/line references when possible.\n"
                "Keep summaries brief and evidence-based.\n"
                "Do not make code changes unless the user explicitly asks for fixes."
            ),
        ),
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
