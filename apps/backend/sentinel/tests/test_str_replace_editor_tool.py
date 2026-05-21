from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.araios.system_modules.str_replace_editor.handlers import _parse_str_replace_output
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry_builder import build_default_registry
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRuntimeContext


class _WorkspaceFilesStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def str_replace(
        self,
        session_id: str,
        *,
        path: str,
        old_str: str,
        new_str: str,
    ) -> dict:
        self.calls.append(
            {
                "session_id": session_id,
                "path": path,
                "old_str": old_str,
                "new_str": new_str,
            }
        )
        return {
            "path": path,
            "message": "File patched successfully",
            "old_str_count": 1,
        }


def test_parse_str_replace_output_accepts_last_json_line() -> None:
    payload = _parse_str_replace_output("\nnoise\n{\"ok\":true,\"path\":\"a\"}\n")
    assert payload["ok"] is True
    assert payload["path"] == "a"


def test_parse_str_replace_output_rejects_invalid() -> None:
    with pytest.raises(ToolValidationError):
        _parse_str_replace_output("not-json")


def test_str_replace_editor_tool_is_registered() -> None:
    registry = build_default_registry()
    tool = registry.get("str_replace_editor")

    assert tool is not None
    assert tool.parameters_schema["required"] == ["path", "old_str", "new_str"]


@pytest.mark.asyncio
async def test_str_replace_editor_runs_through_runtime_workspace(monkeypatch) -> None:
    from app.services.araios.system_modules.str_replace_editor import handlers

    stub = _WorkspaceFilesStub()
    monkeypatch.setattr(handlers, "runtime_configured", lambda: True)
    monkeypatch.setattr(handlers, "get_runtime_workspace_files", lambda: stub)

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "str_replace_editor",
        {
            "path": "app.py",
            "old_str": "hello",
            "new_str": "goodbye",
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["path"] == "app.py"
    assert result["old_str_count"] == 1
    assert stub.calls == [
        {
            "session_id": str(session_id),
            "path": "app.py",
            "old_str": "hello",
            "new_str": "goodbye",
        }
    ]
