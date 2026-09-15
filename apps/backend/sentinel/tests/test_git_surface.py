from types import SimpleNamespace

import httpx
import pytest

from app.services.modules.builtins.git_tool import credential_broker as broker
from app.services.modules.builtins.git_tool import handlers
from sentral.errors import ToolValidationError


@pytest.mark.parametrize(
    "command,mode",
    [
        ("git stash list", "read"),
        ("git worktree list --porcelain", "read"),
        ("git tag --list release", "read"),
        ("git add --dry-run -- file", "read"),
        ("git config user.name Alice", "write"),
        ("git config --get user.name", "read"),
        ("git switch -c test", "write"),
        ("git branch new", "write"),
        ("git pull --ff-only", "write"),
        ("git fetch origin", "read"),
        ("gh api repos/o/r -f title=changed", "write"),
        ("gh api repos/o/r -F title=changed", "write"),
        ("gh api repos/o/r --raw-field=title=changed", "write"),
        ("gh api repos/o/r --input data.json", "write"),
        ("gh api repos/o/r -X GET -f q=hello", "read"),
        ("gh api repos/o/r -XPATCH -f title=x", "write"),
        ("gh pr list", "read"),
        ("gh pr close 1", "write"),
        ("gh search repos sample-project", "read"),
    ],
)
def test_classification(command, mode):
    assert handlers._command_mode(handlers._parse_cli_command(command)) == mode


def test_container_paths_and_single_command():
    assert handlers._project_cwd("/tmp/sample-project", "/project") == "/tmp/sample-project"
    with pytest.raises(ToolValidationError, match="one git"):
        handlers._parse_cli_command("git status && git log")


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/repos/o/r/pulls/1", b"{}"),
        (
            "POST",
            "/graphql",
            b'{"query":"mutation { closePullRequest(input:{pullRequestId:1}) { clientMutationId } }"}',
        ),
        ("GET", "/o/r.git/info/refs?service=git-receive-pack", b""),
    ],
)
def test_broker_read_rejects_actual_mutations(method, path, body):
    with pytest.raises(ToolValidationError):
        broker.authorize_request(
            host="github.com",
            api_host="api.github.com",
            authority=(
                "api.github.com" if path.startswith(("/repos", "/graphql")) else "github.com"
            ),
            method=method,
            path=path,
            mode="read",
            scope="*",
            repo=None,
            body=body,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("client", ["curl", "gh"])
async def test_real_client_uses_broker_without_guest_token(tmp_path, monkeypatch, client):
    import shutil
    import sys

    from app.services.runtime.local_transport import LocalTransport

    if client == "gh" and sys.platform == "darwin":
        pytest.skip("macOS Go uses Keychain roots; production gh runs in Linux")
    if not shutil.which(client):
        pytest.skip("gh executable required")
    seen = []
    token = "host-only-test-credential"

    async def upstream(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer " + token
        return httpx.Response(200, json={"login": "broker-test"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        broker.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(upstream), **kw),
    )
    transport = LocalTransport()
    original = transport.create_process

    async def launch(command, **kw):
        assert token not in command
        return await original(command, **kw)

    monkeypatch.setattr(transport, "create_process", launch)
    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=transport),
        tokens=[
            shutil.which(client),
            *(
                ["api", "user"]
                if client == "gh"
                else ["--silent", "--show-error", "https://api.github.com/user"]
            ),
        ],
        cwd=str(tmp_path),
        account=SimpleNamespace(host="github.com", scope_pattern="*", token=token),
        mode="read",
        timeout=20,
    )
    assert result["ok"], result["stderr"]
    assert "broker-test" in result["stdout"]
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_real_git_protocol_read_through_broker(tmp_path, monkeypatch):
    import shutil

    from app.services.runtime.local_transport import LocalTransport

    seen = []

    async def upstream(request):
        seen.append(request)
        assert request.url.path == "/o/r.git/info/refs"
        assert request.headers["authorization"].startswith("Basic ")
        # Empty protocol-v0 repository advertisement.
        return httpx.Response(
            200,
            headers={"content-type": "application/x-git-upload-pack-advertisement"},
            content=b"001e# service=git-upload-pack\n00000000",
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        broker.httpx,
        "AsyncClient",
        lambda **kw: client_type(transport=httpx.MockTransport(upstream), **kw),
    )
    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=LocalTransport()),
        tokens=[shutil.which("git"), "ls-remote", "https://github.com/o/r.git"],
        cwd=str(tmp_path),
        account=SimpleNamespace(host="github.com", scope_pattern="*", token="host-only"),
        mode="read",
        timeout=20,
        repo="o/r",
    )
    assert result["ok"], result["stderr"]
    assert seen


@pytest.mark.asyncio
async def test_actual_post_cannot_cross_read_broker(tmp_path, monkeypatch):
    import shutil

    from app.services.runtime.local_transport import LocalTransport

    async def upstream(request):
        pytest.fail("Read broker forwarded a mutation")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        broker.httpx,
        "AsyncClient",
        lambda **kw: client_type(**({"transport": httpx.MockTransport(upstream)} | kw)),
    )
    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=LocalTransport()),
        tokens=[
            shutil.which("curl"),
            "--fail-with-body",
            "--silent",
            "-X",
            "POST",
            "-d",
            "title=changed",
            "https://api.github.com/repos/o/r/pulls/1",
        ],
        cwd=str(tmp_path),
        account=SimpleNamespace(host="github.com", scope_pattern="*", token="host-only"),
        mode="read",
        timeout=20,
    )
    assert not result["ok"]
    assert "cannot send a mutation" in result["stdout"]


@pytest.mark.asyncio
async def test_repo_discovery_includes_orgs_without_workspace(monkeypatch):
    from tests.test_git_tool import _fake_db_with_account, _SessionFactory

    db = _fake_db_with_account()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(db))

    async def upstream(request):
        assert request.url.params["affiliation"] == "owner,collaborator,organization_member"
        return httpx.Response(
            200,
            json=[
                {
                    "full_name": "example-org/sample-project",
                    "html_url": "https://github.com/example-org/sample-project",
                    "private": True,
                    "permissions": {"push": True},
                }
            ],
        )

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: client_type(**({"transport": httpx.MockTransport(upstream)} | kw)),
    )
    result = await handlers.handle_repos({"query": "sample-project"}, None)
    assert not result["errors"]
    assert result["repositories"][0]["name"] == "example-org/sample-project"


@pytest.mark.asyncio
async def test_linux_gh_broker(container_transport, monkeypatch):
    async def upstream(request):
        assert request.headers["authorization"] == "Bearer host-only-test"
        return httpx.Response(200, json={"login": "broker-test"})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        broker.httpx,
        "AsyncClient",
        lambda **kw: client_type(**({"transport": httpx.MockTransport(upstream)} | kw)),
    )
    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=container_transport),
        tokens=["gh", "api", "user"],
        cwd="/tmp",
        account=SimpleNamespace(host="github.com", scope_pattern="*", token="host-only-test"),
        mode="read",
        timeout=30,
    )
    assert result["ok"], result["stderr"]
    assert "broker-test" in result["stdout"]


@pytest.mark.asyncio
async def test_cherry_pick_supplies_committer_without_overriding_author(monkeypatch):
    from uuid import uuid4

    from tests.test_git_tool import (
        _fake_db_with_account,
        _runtime_configured_stub,
        _runtime_context,
        _SessionFactory,
        _terminal_manager_stub,
        _TerminalManagerStub,
    )

    manager = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(_fake_db_with_account()))
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    result = await handlers.handle_write(
        {"cli_command": "git cherry-pick --continue"}, _runtime_context(uuid4())
    )
    assert result["ok"]
    env = manager.ssh.calls[-1]["env"]
    assert env["GIT_COMMITTER_NAME"]
    assert "GIT_AUTHOR_NAME" not in env
    assert "account-token" not in str(env)


@pytest.mark.asyncio
async def test_revert_creates_commit_without_configured_identity(monkeypatch, tmp_path):
    import os
    import subprocess
    from uuid import uuid4

    from tests.test_git_tool import (
        _fake_db_with_account,
        _runtime_configured_stub,
        _runtime_context,
        _SessionFactory,
        _terminal_manager_stub,
        _TerminalManagerStub,
    )

    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    clean_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

    def git(*args, env=None):
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            env=clean_env | (env or {}),
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    git("init")
    git("remote", "add", "origin", "https://github.com/example-org/sample-app.git")
    original_identity = {
        "GIT_AUTHOR_NAME": "Original Author",
        "GIT_AUTHOR_EMAIL": "original@example.com",
        "GIT_COMMITTER_NAME": "Original Author",
        "GIT_COMMITTER_EMAIL": "original@example.com",
    }
    file = tmp_path / "file.txt"
    file.write_text("before\n")
    git("add", "file.txt")
    git("commit", "-m", "baseline", env=original_identity)
    file.write_text("after\n")
    git("commit", "-am", "change", env=original_identity)

    manager = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "AsyncSessionLocal", _SessionFactory(_fake_db_with_account()))
    monkeypatch.setattr(handlers, "get_runtime_terminal_manager", _terminal_manager_stub(manager))
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)

    async def run_actual_git(**kwargs):
        assert "account-token" not in str(kwargs.get("env"))
        output = git(*kwargs["tokens"][1:], env=kwargs.get("env"))
        return {"ok": True, "returncode": 0, "stdout": output}

    monkeypatch.setattr(handlers, "_run_hidden_runtime_command", run_actual_git)
    result = await handlers.handle_write(
        {"cli_command": "git revert --no-edit HEAD", "cwd": str(tmp_path)},
        _runtime_context(uuid4()),
    )
    assert result["ok"]
    assert file.read_text() == "before\n"
    assert git("status", "--porcelain") == ""
    assert git("log", "-1", "--format=%an <%ae>|%cn <%ce>").strip() == (
        "Sentinel Bot <sentinel@example.com>|Sentinel Bot <sentinel@example.com>"
    )
    assert git("log", "-1", "HEAD~1", "--format=%an").strip() == "Original Author"


@pytest.mark.parametrize(
    "query,expected",
    [
        (
            "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){name}}",
            True,
        ),
        (
            'mutation{closePullRequest(input:{pullRequestId:"x"}){clientMutationId}}',
            False,
        ),
        ('{other:repository(owner:"elsewhere",name:"repo"){name}}', False),
        ("{viewer{repositories(first:100){nodes{name}}}}", False),
    ],
)
def test_scoped_graphql(query, expected):
    import json

    body = json.dumps({"query": query, "variables": {"owner": "org", "name": "repo"}}).encode()
    assert broker.graphql_in_scope(body, "github.com", "github.com/org/*") is expected


@pytest.mark.asyncio
async def test_scoped_graphql_resolves_mutation_target_on_host():
    import json

    async def upstream(request):
        assert request.headers["authorization"] == "Bearer host-only"
        return httpx.Response(
            200, json={"data": {"node": {"repository": {"nameWithOwner": "org/repo"}}}}
        )

    account = SimpleNamespace(
        host="github.com", scope_pattern="github.com/org/*", token="host-only"
    )
    body = json.dumps(
        {
            "query": "mutation($input:ClosePullRequestInput!){closePullRequest(input:$input){clientMutationId}}",
            "variables": {"input": {"pullRequestId": "PR_TEST"}},
        }
    ).encode()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        assert await broker.verify_graphql_scope(client, account, body)
        account.scope_pattern = "github.com/other/*"
        assert not await broker.verify_graphql_scope(client, account, body)


@pytest.mark.asyncio
async def test_broker_timeout_is_reported_and_process_is_reaped(tmp_path):
    import sys

    from app.services.runtime.local_transport import LocalTransport

    result = await broker.run_brokered(
        terminal=SimpleNamespace(ssh=LocalTransport()),
        tokens=[sys.executable, "-c", "import time;time.sleep(30)"],
        cwd=str(tmp_path),
        account=SimpleNamespace(host="github.com", scope_pattern="*", token="host-only"),
        mode="read",
        timeout=0.5,
    )
    assert result["timed_out"]
    assert not result["ok"]
