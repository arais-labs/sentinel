from __future__ import annotations

from app.services.agent.agent_modes import (
    AgentMode,
    get_agent_mode_definition,
    get_default_agent_mode,
    list_agent_mode_definitions,
    parse_agent_mode,
)


def test_agent_mode_registry_contains_expected_modes():
    definitions = list_agent_mode_definitions()
    ids = {item.id for item in definitions}
    assert ids == {
        AgentMode.NORMAL,
        AgentMode.FULL_PERMISSION,
        AgentMode.READ_ONLY,
        AgentMode.CODE_REVIEW,
    }


def test_default_agent_mode_is_normal():
    assert get_default_agent_mode() == AgentMode.NORMAL


def test_full_permission_mode_auto_approve_flag_enabled():
    definition = get_agent_mode_definition("full_permission")
    assert definition.id == AgentMode.FULL_PERMISSION
    assert definition.auto_approve_tool_gates is True


def test_parse_agent_mode_rejects_unknown_value():
    assert parse_agent_mode("unknown") is None


def test_code_review_mode_has_policy():
    definition = get_agent_mode_definition("code_review")
    assert definition.id == AgentMode.CODE_REVIEW
    assert definition.policy is not None
