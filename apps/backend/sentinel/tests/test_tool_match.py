from app.services.approvals.tool_match import (
    build_runtime_exec_match_key,
    build_tool_match_key,
)


def test_runtime_exec_match_key_scopes_root() -> None:
    assert build_runtime_exec_match_key(command="  echo hi  ", privilege="root") == "runtime_exec:root:echo hi"


def test_runtime_exec_match_key_defaults_user_scope() -> None:
    assert build_runtime_exec_match_key(command="  echo hi  ") == "runtime_exec:echo hi"


def test_build_tool_match_key_uses_runtime_exec_scope() -> None:
    assert build_tool_match_key(
        tool_name="runtime_exec",
        payload={"command": "run", "shell_command": "echo hi", "privilege": "root"},
    ) == "runtime_exec:root:echo hi"
