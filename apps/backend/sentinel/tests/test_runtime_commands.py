import subprocess

from app.services.runtime.workspace import WorkspaceLocation
from app.services.runtime.tmux import build_open_tmux_script
from app.services.runtime.status import (
    RuntimeStatusCheck,
    _capabilities,
    _detected_os,
    _detected_sandbox,
)


def test_tmux_bootstrap_streams_config_without_window_aliases():
    root = WorkspaceLocation("/projects/app", "/private/sentinel/workspaces/abc")
    for os_name, sandbox in [("linux", "container")]:
        script, args = build_open_tmux_script("chat", root=root, os_name=os_name, sandbox=sandbox)
        subprocess.run(["/bin/bash", "-n"], input=script, text=True, check=True)
        assert "source-file -" in script
        assert "@sentinel_terminal_id" not in script
        assert "@sentinel_agent_pane" not in script
        assert "__CONTROLLER_COMMAND__" not in script
        assert "-f /dev/null" in script
        assert not args


def test_runtime_capabilities_require_os_sandbox() -> None:
    checks = [
        RuntimeStatusCheck("ssh_connect", "SSH", "pass"),
        RuntimeStatusCheck("ssh_command", "Remote command", "pass"),
        RuntimeStatusCheck("workspace_writable", "Workspace", "pass"),
        RuntimeStatusCheck("os", "OS", "pass", detail="linux"),
        RuntimeStatusCheck("sandbox", "Sandbox", "fail", detail="unavailable"),
        RuntimeStatusCheck("binary_bash", "bash", "pass"),
        RuntimeStatusCheck("binary_tmux", "tmux", "pass"),
        RuntimeStatusCheck("binary_python3", "python3", "pass"),
        RuntimeStatusCheck("binary_git", "git", "pass"),
        RuntimeStatusCheck("binary_gh", "gh", "pass"),
    ]

    assert _detected_os(checks) == "linux"
    assert _detected_sandbox(checks) == "unavailable"
    assert _capabilities(checks)["shell"] == "unavailable"


def test_runtime_capabilities_support_container_core() -> None:
    checks = [
        RuntimeStatusCheck("ssh_connect", "SSH", "pass"),
        RuntimeStatusCheck("ssh_command", "Remote command", "pass"),
        RuntimeStatusCheck("workspace_writable", "Workspace", "pass"),
        RuntimeStatusCheck("os", "OS", "pass", detail="linux"),
        RuntimeStatusCheck("sandbox", "Sandbox", "pass", detail="container"),
        RuntimeStatusCheck("binary_bash", "bash", "pass"),
        RuntimeStatusCheck("binary_tmux", "tmux", "pass"),
        RuntimeStatusCheck("binary_python3", "python3", "pass"),
        RuntimeStatusCheck("binary_git", "git", "pass"),
        RuntimeStatusCheck("binary_gh", "gh", "pass"),
        RuntimeStatusCheck("desktop_stack", "Desktop", "warn", detail="Linux-only", required=False),
    ]

    capabilities = _capabilities(checks)

    assert capabilities["shell"] == "ready"
    assert capabilities["files"] == "ready"
    assert capabilities["git"] == "ready"
    assert capabilities["jobs"] == "ready"
    assert capabilities["desktop"] == "unavailable"
    assert capabilities["browser"] == "unavailable"
