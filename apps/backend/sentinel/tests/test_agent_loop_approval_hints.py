from app.services.agent.loop import AgentLoop


def test_runtime_exec_root_approval_hint_uses_tool_provider() -> None:
    hint = AgentLoop._approval_hint_for_tool_call(  # noqa: SLF001
        tool_name="runtime_exec",
        arguments={"command": " echo hi ", "privilege": "root"},
    )
    assert hint == {
        "provider": "tool",
        "match_key": "runtime_exec:root:echo hi",
    }


def test_runtime_exec_user_approval_hint_not_emitted() -> None:
    hint = AgentLoop._approval_hint_for_tool_call(  # noqa: SLF001
        tool_name="runtime_exec",
        arguments={"command": "echo hi", "privilege": "user"},
    )
    assert hint is None
