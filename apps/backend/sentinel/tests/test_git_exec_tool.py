from __future__ import annotations

import pytest

from app.services.tools import git_exec as git_exec_module
from app.services.tools.executor import ToolValidationError


def test_resolve_origin_url_reports_not_git_repo(monkeypatch, tmp_path):
    def _fake_run_blocking(*, args, cwd, env, timeout_seconds):
        _ = args, cwd, env, timeout_seconds
        return {
            "returncode": 128,
            "stdout": "",
            "stderr": "fatal: not a git repository (or any of the parent directories): .git",
            "timed_out": False,
        }

    monkeypatch.setattr(git_exec_module, "_run_blocking", _fake_run_blocking)

    with pytest.raises(ToolValidationError, match="requires a git repository"):
        git_exec_module._resolve_origin_url(tmp_path)


def test_resolve_origin_url_reports_missing_remote(monkeypatch, tmp_path):
    def _fake_run_blocking(*, args, cwd, env, timeout_seconds):
        _ = args, cwd, env, timeout_seconds
        return {
            "returncode": 2,
            "stdout": "",
            "stderr": "error: No such remote 'upstream'",
            "timed_out": False,
        }

    monkeypatch.setattr(git_exec_module, "_run_blocking", _fake_run_blocking)

    with pytest.raises(ToolValidationError, match="Git remote 'upstream' was not found"):
        git_exec_module._resolve_origin_url(tmp_path, remote_name="upstream")


def test_resolve_origin_url_includes_generic_git_error(monkeypatch, tmp_path):
    def _fake_run_blocking(*, args, cwd, env, timeout_seconds):
        _ = args, cwd, env, timeout_seconds
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": "fatal: remote helper crashed unexpectedly\nstack trace omitted",
            "timed_out": False,
        }

    monkeypatch.setattr(git_exec_module, "_run_blocking", _fake_run_blocking)

    with pytest.raises(ToolValidationError, match="remote helper crashed unexpectedly"):
        git_exec_module._resolve_origin_url(tmp_path)


def test_resolve_origin_url_success(monkeypatch, tmp_path):
    def _fake_run_blocking(*, args, cwd, env, timeout_seconds):
        _ = args, cwd, env, timeout_seconds
        return {
            "returncode": 0,
            "stdout": "https://github.com/arais-labs/sentinel.git\n",
            "stderr": "",
            "timed_out": False,
        }

    monkeypatch.setattr(git_exec_module, "_run_blocking", _fake_run_blocking)

    repo_url = git_exec_module._resolve_origin_url(tmp_path)
    assert repo_url == "https://github.com/arais-labs/sentinel.git"
