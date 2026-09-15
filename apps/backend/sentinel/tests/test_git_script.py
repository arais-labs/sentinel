from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services.modules.builtins.git_tool import credential_broker as broker
from app.services.modules.builtins.git_tool import handlers
from app.services.runtime.local_transport import LocalTransport
from tests.test_git_tool import (
    _fake_db_with_account,
    _runtime_context,
    _SessionFactory,
    _terminal_manager_stub,
    _TerminalManagerStub,
)


@pytest.mark.asyncio
async def test_script_handler_uses_broker_with_selected_account(monkeypatch):
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(_fake_db_with_account()))
    monkeypatch.setattr(
        handlers, "get_runtime_terminal_manager", _terminal_manager_stub(_TerminalManagerStub())
    )
    captured = {}

    async def run(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(broker, "run_brokered", run)
    result = await handlers.handle_run_script(
        {"script": "bash scripts/check.sh", "cwd": "/tmp/repo", "git_account_name": "github-main"},
        _runtime_context(uuid4()),
    )
    assert result["ok"]
    assert captured["tokens"][-1] == "bash scripts/check.sh"
    assert captured["cwd"] == "/tmp/repo"
    assert captured["mode"] == "write"
    assert captured["git_identity"] == {"name": "Sentinel Bot", "email": "sentinel@example.com"}


@pytest.mark.asyncio
@pytest.mark.parametrize("target,allowed", [("o/r", True), ("other/repo", False)])
async def test_nested_script_brokers_git_and_api_without_tokens(
    tmp_path, monkeypatch, target, allowed
):
    seen = []

    async def upstream(request):
        seen.append(request)
        assert "host-only-script-token" in request.headers["authorization"] or request.headers[
            "authorization"
        ].startswith("Basic ")
        if request.url.path.endswith("/info/refs"):
            return httpx.Response(
                200,
                headers={"content-type": "application/x-git-upload-pack-advertisement"},
                content=b"001e# service=git-upload-pack\n00000000",
            )
        assert request.method == "PATCH"
        return httpx.Response(200, json={"updated": True})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        broker.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(upstream), **kw),
    )
    script = tmp_path / "nested.sh"
    script.write_text(
        "set -e\n"
        "git init -q\n"
        "echo example > file.txt\n"
        "git add file.txt\n"
        "git commit -qm example\n"
        "git log -1 --format='%an <%ae>'\n"
        f"git ls-remote https://github.com/{target}.git\n"
        f'curl --fail --silent --show-error -X PATCH https://api.github.com/repos/{target}/issues/1 -d \'{{"title":"test"}}\'\n'
    )
    transport = LocalTransport()
    original = transport.create_process

    async def launch(command, **kw):
        assert "host-only-script-token" not in command
        return await original(command, **kw)

    monkeypatch.setattr(transport, "create_process", launch)
    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=transport),
        tokens=["bash", str(script)],
        cwd=str(tmp_path),
        account=SimpleNamespace(
            host="github.com", scope_pattern="github.com/o/*", token="host-only-script-token"
        ),
        mode="write",
        timeout=20,
        git_identity={"name": "Script Author", "email": "script@example.com"},
    )
    assert result["ok"] is allowed, result
    assert "Script Author <script@example.com>" in result["stdout"]
    assert len(seen) == (2 if allowed else 0)
    assert "host-only-script-token" not in str(result)


@pytest.mark.asyncio
async def test_script_permission_defaults_to_approval():
    from app.services.modules.builtins.git_tool.module import MODULE
    from app.services.modules.tool_adapter import _resolve_action_approval_check

    action = next(action for action in MODULE.actions if action.id == "run_script")
    check = _resolve_action_approval_check(module_name="git", action=action, session_factory=None)
    result = await check()
    assert result.requirement.action == "git.run_script"


@pytest.mark.asyncio
async def test_linux_script_with_nested_gh(container_transport, monkeypatch):
    seen = []

    async def upstream(request):
        seen.append(request.method)
        assert request.headers["authorization"] == "Bearer host-only-script-token"
        return httpx.Response(200, json={"ok": True})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        broker.httpx,
        "AsyncClient",
        lambda **kw: client_type(**({"transport": httpx.MockTransport(upstream)} | kw)),
    )
    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=container_transport),
        tokens=[
            "bash",
            "-e",
            "-c",
            "bash -c 'gh api repos/o/r; gh api -X PATCH repos/o/r/issues/1 -f title=test'",
        ],
        cwd="/tmp",
        account=SimpleNamespace(
            host="github.com", scope_pattern="github.com/o/*", token="host-only-script-token"
        ),
        mode="write",
        timeout=30,
    )
    assert result["ok"], result
    assert seen == ["GET", "PATCH"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "level,decision", [("allow", "allow"), ("deny", "deny"), ("approval", "require")]
)
async def test_script_permission_is_independent(monkeypatch, level, decision):
    from app.services.modules import tool_adapter
    from app.services.modules.builtins.git_tool.module import MODULE

    async def load(**kwargs):
        assert kwargs["action_key"] == "git.run_script"
        return level

    monkeypatch.setattr(tool_adapter, "_load_permission_level", load)
    action = next(action for action in MODULE.actions if action.id == "run_script")
    check = tool_adapter._resolve_action_approval_check(
        module_name="git",
        action=action,
        session_factory=object(),
    )
    assert (await check()).decision.value == decision
