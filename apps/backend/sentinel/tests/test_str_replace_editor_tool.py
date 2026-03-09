from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.services.tools.editor import str_replace_editor_tool
from app.services.tools.executor import ToolValidationError


def test_str_replace_editor_success(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "sample.txt"
    file_path.write_text("hello old world", encoding="utf-8")

    monkeypatch.setattr("app.services.tools.editor.runtime_workspace_dir", lambda _sid: workspace)

    class _DummyResult:
        def scalars(self):
            return self
        def first(self):
            return object()

    class _DummySession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return None
        async def execute(self, _query):
            return _DummyResult()

    def _session_factory():
        return _DummySession()

    tool = str_replace_editor_tool(session_factory=_session_factory)
    result = asyncio.run(
        tool.execute(
            {
                "session_id": str(session_id),
                "path": "sample.txt",
                "old_str": "old",
                "new_str": "new",
            }
        )
    )

    assert result["message"] == "File patched successfully"
    assert file_path.read_text(encoding="utf-8") == "hello new world"


def test_str_replace_editor_requires_unique_match(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "sample.txt"
    file_path.write_text("dup dup", encoding="utf-8")

    monkeypatch.setattr("app.services.tools.editor.runtime_workspace_dir", lambda _sid: workspace)

    class _DummyResult:
        def scalars(self):
            return self
        def first(self):
            return object()

    class _DummySession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return None
        async def execute(self, _query):
            return _DummyResult()

    def _session_factory():
        return _DummySession()

    tool = str_replace_editor_tool(session_factory=_session_factory)
    with pytest.raises(ToolValidationError) as exc:
        asyncio.run(
            tool.execute(
                {
                    "session_id": str(session_id),
                    "path": "sample.txt",
                    "old_str": "dup",
                    "new_str": "one",
                }
            )
        )

    assert "occurs 2 times" in str(exc.value)


def test_str_replace_editor_rejects_missing_match(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = uuid4()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_path = workspace / "sample.txt"
    file_path.write_text("abc", encoding="utf-8")

    monkeypatch.setattr("app.services.tools.editor.runtime_workspace_dir", lambda _sid: workspace)

    class _DummyResult:
        def scalars(self):
            return self
        def first(self):
            return object()

    class _DummySession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return None
        async def execute(self, _query):
            return _DummyResult()

    def _session_factory():
        return _DummySession()

    tool = str_replace_editor_tool(session_factory=_session_factory)
    with pytest.raises(ToolValidationError) as exc:
        asyncio.run(
            tool.execute(
                {
                    "session_id": str(session_id),
                    "path": "sample.txt",
                    "old_str": "zzz",
                    "new_str": "yyy",
                }
            )
        )

    assert "exact string to replace was not found" in str(exc.value)
