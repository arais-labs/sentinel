from __future__ import annotations

import asyncio
import shutil
import subprocess
from functools import lru_cache
from uuid import uuid4

import pytest

from app.services.tools.editor import (
    _parse_str_replace_output,
    _run_str_replace_in_runtime_exec,
)
from app.services.tools.executor import ToolValidationError


@lru_cache(maxsize=1)
def _runtime_exec_user_sandbox_available() -> bool:
    bwrap_bin = shutil.which("bwrap")
    if not bwrap_bin:
        return False
    probe = [
        bwrap_bin,
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev-bind",
        "/dev",
        "/dev",
        "--",
        "/bin/bash",
        "-lc",
        "true",
    ]
    try:
        result = subprocess.run(
            probe,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def test_parse_str_replace_output_accepts_last_json_line() -> None:
    payload = _parse_str_replace_output("\nnoise\n{\"ok\":true,\"path\":\"a\"}\n")
    assert payload["ok"] is True
    assert payload["path"] == "a"


def test_parse_str_replace_output_rejects_invalid() -> None:
    with pytest.raises(ToolValidationError):
        _parse_str_replace_output("not-json")


def test_run_str_replace_in_runtime_exec_success(tmp_path: pytest.TempPathFactory) -> None:
    if not _runtime_exec_user_sandbox_available():
        pytest.skip("runtime_exec user sandbox unavailable")

    workspace = tmp_path / str(uuid4())
    workspace.mkdir(parents=True, exist_ok=True)
    file_path = workspace / "sample.txt"
    file_path.write_text("hello old world", encoding="utf-8")

    result = asyncio.run(
        _run_str_replace_in_runtime_exec(
            workspace_dir=workspace,
            path="sample.txt",
            old_str="old",
            new_str="new",
        )
    )
    assert result["message"] == "File patched successfully"
    assert file_path.read_text(encoding="utf-8") == "hello new world"


def test_run_str_replace_in_runtime_exec_rejects_overlapping_non_unique(
    tmp_path: pytest.TempPathFactory,
) -> None:
    if not _runtime_exec_user_sandbox_available():
        pytest.skip("runtime_exec user sandbox unavailable")

    workspace = tmp_path / str(uuid4())
    workspace.mkdir(parents=True, exist_ok=True)
    file_path = workspace / "sample.txt"
    file_path.write_text("aaa", encoding="utf-8")

    with pytest.raises(ToolValidationError, match="occurs multiple times"):
        asyncio.run(
            _run_str_replace_in_runtime_exec(
                workspace_dir=workspace,
                path="sample.txt",
                old_str="aa",
                new_str="Z",
            )
        )

