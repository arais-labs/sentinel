from __future__ import annotations

import json

import pytest

from app.services.araios.system_modules.python import handlers
from app.services.runtime.base import RuntimeExecResult, RuntimeInstance


def test_python_venv_container_path_defaults_to_workspace() -> None:
    assert (
        handlers._python_venv_container_path("/workspace", None)
        == "/workspace/.venvs/default"
    )
    assert (
        handlers._python_venv_container_path("/workspace", "dev")
        == "/workspace/.venvs/dev"
    )


def test_python_venv_container_path_prefers_runtime_metadata_root() -> None:
    assert (
        handlers._python_venv_container_path(
            "/workspace",
            None,
            venv_root="/srv/sentinel/sessions/abc/venvs",
        )
        == "/srv/sentinel/sessions/abc/venvs/default"
    )
    assert (
        handlers._python_venv_container_path(
            "/workspace",
            "dev",
            venv_root="/srv/sentinel/sessions/abc/venvs",
        )
        == "/srv/sentinel/sessions/abc/venvs/dev"
    )


@pytest.mark.asyncio
async def test_run_python_in_runtime_uses_runtime_metadata_venv_root(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Client:
        async def run(self, command: str, *, timeout: int = 300, cwd=None, env=None, as_root: bool = False):
            _ = as_root
            captured["command"] = command
            captured["timeout"] = timeout
            captured["cwd"] = cwd
            return RuntimeExecResult(
                exit_status=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "stdout": "",
                        "stderr": "",
                        "exception": None,
                        "result": "done",
                        "result_repr": None,
                    }
                ),
                stderr="",
            )

    class _Provider:
        async def ensure(self, session_id):
            assert session_id == "session-123"
            return RuntimeInstance(
                session_id="session-123",
                client=_Client(),
                workspace_path="/workspace",
                host="host.docker.internal",
                metadata={"python_venv_root": "/srv/sentinel/sessions/session-123/venvs"},
            )

    monkeypatch.setattr(handlers, "get_runtime", lambda: _Provider())

    result = await handlers._run_python_in_runtime(
        session_id="session-123",
        code="result = 'done'",
        requirements=[],
        venv_name=None,
        timeout_seconds=30,
    )

    assert result["venv"] == "/srv/sentinel/sessions/session-123/venvs/default"
    command = str(captured["command"])
    assert "SENTINEL_PYTHON_VENV_PATH=/srv/sentinel/sessions/session-123/venvs/default" in command
    assert captured["cwd"] == "/workspace"
