from uuid import uuid4

import pytest

from app.services.modules.builtins.runtime.module import MODULE
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry


@pytest.mark.asyncio
async def test_exec_dispatches_to_native_pane_bridge(monkeypatch):
    from app.services.modules.builtins.runtime import handlers

    calls = []

    async def manager(**kwargs):
        return object()

    class Bridge:
        def __init__(self, manager):
            pass

        async def execute(self, session, command, **kwargs):
            calls.append((session, command, kwargs))
            return {"pane_id": kwargs["pane_id"], "exit_status": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", manager)
    monkeypatch.setattr(handlers, "TmuxPanes", Bridge)
    session = uuid4()
    result, _ = await ToolExecutor(build_default_registry()).execute(
        "runtime",
        {"action": "exec", "shell_command": "echo ok", "pane_id": "%12", "background": True},
        runtime=ToolRuntimeContext(session_id=session),
    )
    assert result["stdout"] == "ok"
    assert calls[0][0] == str(session)
    assert calls[0][2]["pane_id"] == "%12"
    assert calls[0][2]["background"] is True


def test_runtime_actions_are_separate_permission_targets():
    actions = {action.id: action for action in MODULE.actions}
    assert "exec" in actions and "user" not in actions and "root" not in actions
    assert {
        "window_create",
        "window_close",
        "pane_split",
        "pane_close",
        "pane_read",
        "pane_input",
    } <= actions.keys()
    assert actions["exec"].get_parameters_schema()["additionalProperties"] is False
    assert "root" not in actions["exec"].get_parameters_schema()["properties"]
