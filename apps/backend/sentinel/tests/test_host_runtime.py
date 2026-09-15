import asyncio
import os
import shlex
import sys
from uuid import uuid4

import pytest
import pytest_asyncio

from sentral.tools.host_processes import OUTPUT_LIMIT, HostProcessManager, shell_argv
from app.services.modules.builtins.host_runtime import module
from sentral.errors import ToolValidationError
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry

OWNER = ("instance", "session")


@pytest_asyncio.fixture
async def manager(monkeypatch):
    manager = HostProcessManager()
    monkeypatch.setattr(module, "host_processes", manager)
    yield manager
    await manager.close()


def python_command(code):
    if os.name == "nt":
        return '& "' + sys.executable + '" -c ' + "'" + code.replace("'", "''") + "'"
    return shlex.join([sys.executable, "-c", code])


async def launch(manager, tmp_path, code, wait=10000):
    return await manager.launch(
        OWNER,
        command=python_command(code),
        cwd=str(tmp_path),
        env={},
        login=False,
        wait_ms=wait,
    )


@pytest.mark.asyncio
async def test_host_cwd_full_environment_output_and_exit(manager, tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_TEST_SECRET_TOKEN", "inherited-intentionally")
    result = await launch(
        manager,
        tmp_path,
        "import os,sys; print(os.getcwd()); print(os.environ['SENTINEL_TEST_SECRET_TOKEN']); print('err', file=sys.stderr); sys.exit(7)",
    )
    assert result["status"] == "completed"
    assert result["exit_code"] == 7
    assert str(tmp_path.resolve()) in result["stdout"]
    assert "inherited-intentionally" in result["stdout"]
    assert result["stderr"].strip() == "err"


@pytest.mark.asyncio
async def test_wait_yields_and_command_finishes_without_restart(manager, tmp_path):
    result = await launch(
        manager,
        tmp_path,
        "import time; print('start', flush=True); time.sleep(.2); print('end')",
        wait=1,
    )
    assert result["status"] == "running"
    result = await manager.poll(OWNER, result["process_id"], 10000)
    assert result["status"] == "completed"
    assert result["stdout"].splitlines() == ["start", "end"]
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_cancelled_wait_keeps_process_discoverable_and_scoped(manager, tmp_path):
    waiter = asyncio.create_task(
        launch(manager, tmp_path, "import time; time.sleep(.2); print('finished')")
    )
    while not manager.jobs:
        await asyncio.sleep(0.001)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    key = next(iter(manager.jobs))
    with pytest.raises(ToolValidationError, match="Unknown host process"):
        await manager.poll(("another-instance", "session"), key, 0)
    result = await manager.poll(OWNER, key, 10000)
    assert result["stdout"].strip() == "finished"


@pytest.mark.asyncio
async def test_large_output_is_drained_and_bounded(manager, tmp_path):
    result = await launch(
        manager,
        tmp_path,
        f"import sys; print('x'*{OUTPUT_LIMIT * 4}); print('y'*{OUTPUT_LIMIT * 4}, file=sys.stderr)",
    )
    assert result["exit_code"] == 0
    for stream in ("stdout", "stderr"):
        assert len(result[stream]) == OUTPUT_LIMIT
        assert result[stream + "_truncated"]


@pytest.mark.asyncio
async def test_explicit_force_termination_and_shutdown(manager, tmp_path):
    result = await launch(manager, tmp_path, "import time; time.sleep(60)", wait=0)
    result = await manager.terminate(OWNER, result["process_id"], force=True)
    assert result["status"] == "completed"
    assert result["exit_code"] != 0
    await launch(manager, tmp_path, "import time; time.sleep(60)", wait=0)
    jobs = list(manager.jobs.values())
    await manager.close()
    assert not manager.jobs
    assert all(job.process.returncode is not None for job in jobs)


@pytest.mark.asyncio
async def test_registered_tool_requires_approval_before_launch(manager, tmp_path):
    executor = ToolExecutor(build_default_registry())
    marker = tmp_path / "should-not-exist"
    with pytest.raises(Exception, match="[Aa]pproval"):
        await executor.execute(
            "host_runtime",
            {
                "action": "exec",
                "shell_command": python_command(f"open({str(marker)!r}, 'w').close()"),
            },
            runtime=ToolRuntimeContext(session_id=uuid4()),
        )
    assert not marker.exists()
    assert not manager.jobs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "level,decision", [("allow", "allow"), ("deny", "deny"), ("approval", "require")]
)
async def test_existing_permission_system_is_respected(monkeypatch, level, decision):
    from app.services.modules import tool_adapter

    async def permission(**kwargs):
        return level

    monkeypatch.setattr(tool_adapter, "_load_permission_level", permission)
    check = tool_adapter._resolve_action_approval_check(
        module_name="host_runtime", action=module.MODULE.actions[0], session_factory=object()
    )
    assert (await check()).decision == decision


@pytest.mark.asyncio
async def test_invalid_arguments_do_not_launch(manager):
    runtime = ToolRuntimeContext(session_id=uuid4())
    for payload in (
        {"shell_command": "echo hi", "cwd": "relative"},
        {"shell_command": "echo hi", "env": {"bad=name": "value"}},
        {"shell_command": "echo hi", "yield_time_ms": -1},
    ):
        with pytest.raises(ToolValidationError):
            await module.handler("exec")(payload, runtime)
    assert not manager.jobs


def test_shell_command_conventions():
    assert shell_argv("/bin/zsh", "echo hi | cat", True) == [
        "/bin/zsh",
        "-lc",
        "echo hi | cat",
    ]
    assert shell_argv("pwsh.exe", "Write-Output hi", False) == [
        "pwsh.exe",
        "-NoProfile",
        "-Command",
        "Write-Output hi",
    ]
    assert shell_argv("cmd.exe", "echo hi", True) == ["cmd.exe", "/c", "echo hi"]


@pytest.mark.asyncio
async def test_registered_exec_and_list_without_workspace(manager, tmp_path):
    from app.services.tools.registry import (
        ToolApprovalOutcome,
        ToolApprovalOutcomeStatus,
    )

    async def approve(*args):
        return ToolApprovalOutcome(status=ToolApprovalOutcomeStatus.APPROVED, approval={})

    executor = ToolExecutor(build_default_registry(), approval_waiter=approve)
    runtime = ToolRuntimeContext(session_id=uuid4(), instance_name="test")
    result, _ = await executor.execute(
        "host_runtime",
        {
            "action": "exec",
            "shell_command": python_command("import os; print(os.environ['HOST_TEST_OVERRIDE'])"),
            "cwd": str(tmp_path),
            "env": {"HOST_TEST_OVERRIDE": "override"},
            "login": False,
        },
        runtime=runtime,
    )
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "override"
    listing, _ = await executor.execute("host_runtime", {"action": "list"}, runtime=runtime)
    assert listing["processes"][0]["process_id"] == result["process_id"]
    empty, _ = await executor.execute(
        "host_runtime",
        {"action": "list"},
        runtime=ToolRuntimeContext(session_id=uuid4(), instance_name="test"),
    )
    assert empty == {"processes": []}


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX process group regression")
async def test_force_kills_child_holding_output_pipe(manager, tmp_path):
    result = await launch(
        manager,
        tmp_path,
        "import subprocess,sys,time,signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); print('ready', flush=True); time.sleep(60)",
        wait=0,
    )
    job = manager.jobs[result["process_id"]]
    async with asyncio.timeout(5):
        while b"ready" not in job.stdout:
            await asyncio.sleep(0.01)
    result = await manager.terminate(OWNER, result["process_id"], force=False)
    assert result["status"] == "running"
    result = await manager.terminate(OWNER, result["process_id"], force=True)
    assert result["status"] == "completed"  # Child must also close its inherited pipe.
