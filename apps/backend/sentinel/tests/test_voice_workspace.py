"""Voice drives the whole window through session_layout; chats keep their own displayed session."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.modules.builtins.session_layout import module as layout
from app.services.modules.tool_adapter import build_module_tools
from app.services.tools.registry import ToolRuntimeContext
from sentral.errors import ToolValidationError


@pytest.mark.asyncio
async def test_switch_chat_is_voice_only_and_routes_to_the_window(monkeypatch):
    manager = SimpleNamespace(request_layout=AsyncMock(return_value={"ok": True, "panes": []}))
    monkeypatch.setattr(layout, "get_ws_manager", lambda: manager)
    tool = build_module_tools(layout.MODULE)[0]
    assert "switch_chat" not in tool.parameters_schema["properties"]["action"]["enum"]
    assert "switch_chat" in tool.voice_parameters_schema["properties"]["action"]["enum"]
    chat_id, voice_id, target = uuid4(), uuid4(), uuid4()
    as_chat = ToolRuntimeContext(session_id=chat_id, instance_name="main", agent_mode="normal")
    with pytest.raises(ToolValidationError, match="Voice agent only"):
        await tool.execute({"action": "switch_chat", "chat_id": str(target)}, as_chat)
    await tool.execute({"action": "inspect"}, as_chat)
    manager.request_layout.assert_awaited_with(
        str(chat_id), {"action": "inspect", "operations": []}
    )
    as_voice = ToolRuntimeContext(session_id=voice_id, instance_name="main", agent_mode="voice")
    await tool.execute({"action": "switch_chat", "chat_id": str(target)}, as_voice)
    manager.request_layout.assert_awaited_with(
        "voice-ui:main", {"action": "switch_session", "session_id": str(target)}
    )
    await tool.execute({"action": "apply", "operations": [{"operation": "restore"}]}, as_voice)
    assert manager.request_layout.await_args.args[0] == "voice-ui:main"
    manager.request_layout.side_effect = RuntimeError("No visible layout")
    with pytest.raises(ToolValidationError, match="No visible layout"):
        await tool.execute({"action": "inspect"}, as_voice)
