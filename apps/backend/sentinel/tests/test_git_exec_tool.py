from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.models import GitAccount, Session
from app.services.araios.system_modules.git_exec import handlers as git_exec_module
from app.services.tools.executor import ToolExecutionError, ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolApprovalOutcome, ToolApprovalOutcomeStatus, ToolRegistry
from app.services.tools.registry_builder import build_default_registry
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _run_via_executor(tool, payload: dict, *, approval_waiter=None):
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry, approval_waiter=approval_waiter)
    result, _ = _run(executor.execute("git_exec", payload))
    return result


class _SessionCtx:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    def __call__(self):
        return _SessionCtx(self._db)


def _git_exec_tool(*, session_factory=None):
    if session_factory is not None:
        git_exec_module.AsyncSessionLocal = session_factory
    registry = build_default_registry(session_factory=session_factory)
    tool = registry.get("git_exec")
    assert tool is not None
    return tool


git_exec_module.git_exec_tool = _git_exec_tool


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


def test_network_mode_for_request_pull_is_read():
    assert git_exec_module._network_mode_for_command("request-pull") == "read"


def test_resolve_network_repo_url_for_request_pull_with_url(tmp_path):
    repo_url, remote_name = git_exec_module._resolve_network_repo_url(
        "request-pull",
        ["origin/main", "https://github.com/exampleco/exampleco-gitops.git", "feat/branch"],
        tmp_path,
    )
    assert repo_url == "https://github.com/exampleco/exampleco-gitops.git"
    assert remote_name == "origin"


def test_resolve_network_repo_url_for_request_pull_with_remote_name(monkeypatch, tmp_path):
    def _fake_resolve_origin_url(run_dir, remote_name="origin"):
        _ = run_dir
        assert remote_name == "upstream"
        return "https://github.com/exampleco/exampleco-gitops.git"

    monkeypatch.setattr(git_exec_module, "_resolve_origin_url", _fake_resolve_origin_url)

    repo_url, remote_name = git_exec_module._resolve_network_repo_url(
        "request-pull",
        ["origin/main", "upstream", "feat/branch"],
        tmp_path,
    )
    assert repo_url == "https://github.com/exampleco/exampleco-gitops.git"
    assert remote_name == "upstream"


def test_git_exec_supports_gh_repo_list_with_account_token(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="arais-labs/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["redactions"] = redactions or []
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": "arais-labs/sentinel\n",
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    result = _run(
        tool.execute(
            {
                "command": "run_read",
                "session_id": str(session.id),
                "cli_command": "gh repo list arais-labs --limit 5",
            }
        )
    )

    assert result["ok"] is True
    assert result["network_mode"] == "read"
    assert result["account"]["host"] == "github.com"
    assert captured["args"] == ["gh", "repo", "list", "arais-labs", "--limit", "5"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghr_read_token_123"
    assert env["GITHUB_TOKEN"] == "ghr_read_token_123"
    assert "ghr_read_token_123" in captured["redactions"]


def test_git_exec_supports_gh_pr_view_with_read_token(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-pr-view")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    def _fake_resolve_origin_url(run_dir, remote_name="origin"):
        _ = run_dir, remote_name
        return "https://github.com/exampleco/exampleco-gitops.git"

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["redactions"] = redactions or []
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": '{"state":"OPEN"}',
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    monkeypatch.setattr(git_exec_module, "_resolve_origin_url", _fake_resolve_origin_url)
    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    result = _run(
        tool.execute(
            {
                "command": "run_read",
                "session_id": str(session.id),
                "cli_command": "gh pr view 37 --json state,mergeStateStatus,isDraft",
            }
        )
    )

    assert result["ok"] is True
    assert result["network_mode"] == "read"
    assert result["account"]["host"] == "github.com"
    assert captured["args"] == ["gh", "pr", "view", "37", "--json", "state,mergeStateStatus,isDraft"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghr_read_token_123"
    assert env["GITHUB_TOKEN"] == "ghr_read_token_123"
    assert "ghr_read_token_123" in captured["redactions"]


def test_git_exec_rejects_unsupported_gh_write_command():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-write")
    fake_db.add(session)
    tool = git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db))

    with pytest.raises(ToolValidationError, match="Unsupported gh command"):
        _run(
            tool.execute(
                {
                    "command": "run_read",
                    "session_id": str(session.id),
                    "cli_command": "gh repo create arais-labs/new-repo --private",
                }
            )
        )


def test_git_exec_rejects_gh_auth_with_clear_guidance():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-auth")
    fake_db.add(session)
    tool = git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db))

    with pytest.raises(ToolValidationError, match="Authentication is managed automatically"):
        _run(
            tool.execute(
                {
                    "command": "run_read",
                    "session_id": str(session.id),
                    "cli_command": "gh auth status",
                }
            )
        )


def test_git_exec_rejects_gh_api_unsupported_method():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-api")
    fake_db.add(session)
    tool = git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db))

    with pytest.raises(ToolValidationError, match="GET, POST, and PUT"):
        _run(
            tool.execute(
                {
                    "command": "run_read",
                    "session_id": str(session.id),
                    "cli_command": "gh api -X DELETE /orgs/arais-labs/repos",
                }
            )
        )


def test_git_exec_supports_gh_api_post_with_approval(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-api-post")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["redactions"] = redactions or []
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": '{"id": 123}',
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    async def _fake_waiter(tool_name, payload, requirement, pending_callback=None):
        _ = tool_name, payload, requirement, pending_callback
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={"provider": "git_exec", "approval_id": str(session.id), "status": "approved", "pending": False, "can_resolve": False},
            message="ok",
        )

    result = _run_via_executor(
        tool,
        {
            "command": "run_write",
            "session_id": str(session.id),
            "cli_command": "gh api -X POST /repos/exampleco/exampleco-gitops/pulls -f title='Test PR'",
        },
        approval_waiter=_fake_waiter,
    )

    assert result["ok"] is True
    assert result["network_mode"] == "write"
    assert result["approval"]["status"] == "approved"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghw_write_token_456"
    assert env["GITHUB_TOKEN"] == "ghw_write_token_456"
    assert "ghw_write_token_456" in captured["redactions"]


def test_git_exec_supports_gh_api_put_with_approval(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-api-put")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["redactions"] = redactions or []
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": '{"merged": true}',
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    async def _fake_waiter(tool_name, payload, requirement, pending_callback=None):
        _ = tool_name, payload, requirement, pending_callback
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={"provider": "git_exec", "approval_id": str(session.id), "status": "approved", "pending": False, "can_resolve": False},
            message="ok",
        )

    result = _run_via_executor(
        tool,
        {
            "command": "run_write",
            "session_id": str(session.id),
            "cli_command": "gh api -X PUT /repos/exampleco/exampleco-gitops/pulls/35/merge -f merge_method=merge",
        },
        approval_waiter=_fake_waiter,
    )

    assert result["ok"] is True
    assert result["network_mode"] == "write"
    assert result["approval"]["status"] == "approved"
    assert captured["args"][0:4] == ["gh", "api", "-X", "PUT"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghw_write_token_456"
    assert env["GITHUB_TOKEN"] == "ghw_write_token_456"
    assert "ghw_write_token_456" in captured["redactions"]


def test_git_exec_supports_gh_pr_create_with_approval(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-pr-create")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["redactions"] = redactions or []
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": "https://github.com/exampleco/exampleco-gitops/pull/123",
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    async def _fake_waiter(tool_name, payload, requirement, pending_callback=None):
        _ = tool_name, payload, requirement, pending_callback
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={"provider": "git_exec", "approval_id": str(session.id), "status": "approved", "pending": False, "can_resolve": False},
            message="ok",
        )

    result = _run_via_executor(
        tool,
        {
            "command": "run_write",
            "session_id": str(session.id),
            "cli_command": (
                "gh pr create --repo exampleco/exampleco-gitops "
                "--base main --head feat/test --title 'Test' --body 'Body'"
            ),
        },
        approval_waiter=_fake_waiter,
    )

    assert result["ok"] is True
    assert result["network_mode"] == "write"
    assert result["approval"]["status"] == "approved"
    assert captured["args"][0:3] == ["gh", "pr", "create"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghw_write_token_456"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert "ghw_write_token_456" in captured["redactions"]


def test_git_exec_supports_gh_pr_merge_with_approval(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-pr-merge")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        captured["args"] = args
        captured["env"] = env
        captured["timeout_seconds"] = timeout_seconds
        captured["redactions"] = redactions or []
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    async def _fake_waiter(tool_name, payload, requirement, pending_callback=None):
        _ = tool_name, payload, requirement, pending_callback
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.APPROVED,
            approval={"provider": "git_exec", "approval_id": str(session.id), "status": "approved", "pending": False, "can_resolve": False},
            message="ok",
        )

    result = _run_via_executor(
        tool,
        {
            "command": "run_write",
            "session_id": str(session.id),
            "cli_command": (
                "gh pr merge 35 --repo exampleco/exampleco-gitops "
                "--merge --delete-branch"
            ),
        },
        approval_waiter=_fake_waiter,
    )

    assert result["ok"] is True
    assert result["network_mode"] == "write"
    assert result["approval"]["status"] == "approved"
    assert captured["args"][0:3] == ["gh", "pr", "merge"]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "ghw_write_token_456"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert "ghw_write_token_456" in captured["redactions"]


def test_git_exec_rejects_gh_api_post_when_not_approved(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="gh-api-post-denied")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    session_factory = _SessionFactory(fake_db)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    async def _fake_waiter(tool_name, payload, requirement, pending_callback=None):
        _ = tool_name, payload, requirement, pending_callback
        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus.REJECTED,
            approval={"provider": "git_exec", "approval_id": str(session.id), "status": "rejected", "pending": False, "can_resolve": False},
            message="User rejected action.",
        )

    with pytest.raises(ToolExecutionError, match="User rejected action"):
        _run_via_executor(
            tool,
            {
                "command": "run_write",
                "session_id": str(session.id),
                "cli_command": "gh api -X POST /repos/exampleco/exampleco-gitops/pulls",
            },
            approval_waiter=_fake_waiter,
        )


def test_git_exec_uses_explicit_git_account_name_for_clone(monkeypatch):
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="explicit-git-account")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="secondary",
            host="github.com",
            scope_pattern="*",
            author_name="Secondary",
            author_email="secondary@example.com",
            token_read="secondary-read",
            token_write="secondary-write",
        )
    )
    preferred = GitAccount(
        name="arailex",
        host="github.com",
        scope_pattern="*",
        author_name="Arailex",
        author_email="alex@example.com",
        token_read="alex-read",
        token_write="alex-write",
    )
    fake_db.add(preferred)
    session_factory = _SessionFactory(fake_db)

    captured: dict[str, object] = {}

    async def _fake_run_subprocess(*, args, run_dir, env, timeout_seconds, redactions=None):
        _ = timeout_seconds, redactions
        captured["args"] = args
        return {
            "ok": True,
            "returncode": 0,
            "timed_out": False,
            "stdout": "",
            "stderr": "",
            "cwd": str(run_dir),
            "command": " ".join(args),
        }

    async def _noop_configure_clone_author(**kwargs):
        _ = kwargs
        return None

    monkeypatch.setattr(git_exec_module, "_run_git_subprocess", _fake_run_subprocess)
    monkeypatch.setattr(git_exec_module, "_configure_author_identity_after_clone", _noop_configure_clone_author)

    tool = git_exec_module.git_exec_tool(session_factory=session_factory)
    result = _run(
        tool.execute(
            {
                "command": "run_read",
                "session_id": str(session.id),
                "cli_command": "git clone https://github.com/arais-labs/sentinel.git",
                "git_account_name": "arailex",
            }
        )
    )

    assert result["ok"] is True
    assert result["account"]["name"] == "arailex"
    assert captured["args"][0:4] == ["git", "-c", "credential.helper=", "clone"]


def test_git_exec_rejects_unknown_explicit_git_account_name():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="missing-account")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="secondary",
            host="github.com",
            scope_pattern="*",
            author_name="Secondary",
            author_email="secondary@example.com",
            token_read="secondary-read",
            token_write="secondary-write",
        )
    )
    tool = git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db))

    with pytest.raises(ToolValidationError, match="Requested git account 'arailex' was not found"):
        _run(
            tool.execute(
                {
                    "command": "run_read",
                    "session_id": str(session.id),
                    "cli_command": "git clone https://github.com/arais-labs/sentinel.git",
                    "git_account_name": "arailex",
                }
            )
        )


def test_git_exec_rejects_explicit_git_account_scope_mismatch():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="scope-mismatch")
    fake_db.add(session)
    fake_db.add(
        GitAccount(
            name="secondary",
            host="github.com",
            scope_pattern="other-org/*",
            author_name="Secondary",
            author_email="secondary@example.com",
            token_read="secondary-read",
            token_write="secondary-write",
        )
    )
    tool = git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db))

    with pytest.raises(ToolValidationError, match="scope does not match repository"):
        _run(
            tool.execute(
                {
                    "command": "run_read",
                    "session_id": str(session.id),
                    "cli_command": "git clone https://github.com/arais-labs/sentinel.git",
                    "git_account_name": "secondary",
                }
            )
        )


def test_git_exec_accounts_operation_lists_matching_accounts():
    fake_db = FakeDB()
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )
    fake_db.add(
        GitAccount(
            name="github-readonly",
            host="github.com",
            scope_pattern="exampleco/readonly",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_readonly_123",
            token_write="",
        )
    )

    result = _run_via_executor(
        git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db)),
        {
            "command": "accounts",
            "host": "github.com",
            "repo_url": "https://github.com/exampleco/exampleco-gitops.git",
            "require_write": True,
        },
    )

    assert result["total"] == 1
    assert result["repo_target"] == "github.com/exampleco/exampleco-gitops"
    assert result["accounts"][0]["name"] == "github-main"


def test_git_exec_accounts_ignores_injected_session_id():
    fake_db = FakeDB()
    fake_db.add(
        GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
    )

    result = _run_via_executor(
        git_exec_module.git_exec_tool(session_factory=_SessionFactory(fake_db)),
        {
            "command": "accounts",
            "session_id": str(uuid4()),
            "host": "github.com",
        },
    )

    assert result["total"] == 1
    assert result["accounts"][0]["name"] == "github-main"
