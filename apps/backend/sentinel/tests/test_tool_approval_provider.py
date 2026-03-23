from app.services.approvals.providers.tool import ToolApprovalProvider


def test_runtime_exec_root_match_key_uses_root_scope() -> None:
    provider = ToolApprovalProvider()
    match = provider.pending_match_from_tool_call(
        tool_name="runtime_exec",
        arguments={"command": "run", "shell_command": "  echo hello  ", "privilege": "root"},
    )
    assert match is not None
    assert match.provider == "tool"
    assert match.match_key == "runtime_exec:root:echo hello"


def test_runtime_exec_user_mode_uses_default_match_key() -> None:
    provider = ToolApprovalProvider()
    match = provider.pending_match_from_tool_call(
        tool_name="runtime_exec",
        arguments={"command": "run", "shell_command": "echo hello", "privilege": "user"},
    )
    assert match is not None
    assert match.provider == "tool"
    assert match.match_key == "runtime_exec:echo hello"
