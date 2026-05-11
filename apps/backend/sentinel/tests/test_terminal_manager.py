from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from app.services.llm.generic.types import ToolCallContent
from app.services.runtime import terminal_manager as tm_module
from app.services.runtime.base import RuntimeExecResult, RuntimeInstance
from app.services.runtime.terminal_manager import (
    TerminalBlockedError,
    TerminalManager,
    configure_terminal_event_broadcaster,
)


@dataclass
class _SessionClientStub:
    """Minimal duck-typed stand-in for QemuSessionClient.

    TerminalManager only ever inspects `client._ssh` and `client._session_user`,
    so we expose those attributes directly without dragging in real SSH wiring.
    """

    _ssh: Any
    _session_user: str = "ssn-test"


class _SSHStub:
    """Records every ssh.run invocation and replies with scripted results.

    Tests inspect `commands` to assert the precise tmux invocation sequence the
    TerminalManager emitted; `responses` lets a test pin specific outputs that
    drive the manager's branching logic (e.g. simulating an exit file appearing,
    or a foreground-busy pane).
    """

    def __init__(self) -> None:
        self.commands: list[str] = []
        # default behaviour: empty stdout, exit 0 — the manager treats any
        # non-`yes`/`no` empty stdout as "no session" / "no output".
        self.responses: list[tuple[int, str, str]] = []

    def push(self, *, stdout: str = "", stderr: str = "", exit_status: int = 0) -> None:
        self.responses.append((exit_status, stdout, stderr))

    async def run(self, command: str, *, timeout: int | None = None, **_: Any) -> RuntimeExecResult:
        self.commands.append(command)
        if self.responses:
            exit_status, stdout, stderr = self.responses.pop(0)
        else:
            exit_status, stdout, stderr = 0, "", ""
        return RuntimeExecResult(exit_status=exit_status, stdout=stdout, stderr=stderr)


def _make_runtime(ssh: _SSHStub, *, session_user: str = "ssn-test", workspace: str = "/srv/workspace") -> RuntimeInstance:
    return RuntimeInstance(
        session_id="sess",
        client=_SessionClientStub(_ssh=ssh, _session_user=session_user),
        workspace_path=workspace,
        host="127.0.0.1",
        metadata={"provider": "stub"},
    )


def _osc_d_log(echoed_command: str, output: str, exit_code: int = 0) -> str:
    """Produce the pipe-pane log bytes our reader should see for one command.

    Format mirrors what bash actually writes through pipe-pane: the command
    line bash echoed back, a newline, the command output, then the OSC 133 D
    marker our bashrc's PROMPT_COMMAND emits before drawing the next prompt.
    """
    return f"{echoed_command}\n{output}\x1b]133;D;{exit_code}\x1b\\"


@pytest.mark.asyncio
async def test_run_command_creates_session_and_captures_output_via_pipe_log(monkeypatch):
    """A fresh terminal is provisioned and we parse output from the pipe-pane log.

    The new flow sends the command plainly into the pane (no wrapper plumbing
    the user has to see) and reads what bash printed by tailing the pipe-pane
    log between the offset right before we typed and the next OSC 133 D
    marker (which carries the exit code).
    """
    ssh = _SSHStub()
    # Fresh manager → _ensure_terminal_locked has no in-memory record, so it
    # skips the has-session check and goes through _create_tmux_session.
    ssh.push(stdout="present")         # tmux availability probe
    ssh.push()                         # mkdir/chown/rcfile prep
    ssh.push()                         # tmux new-session
    ssh.push()                         # tmux options + pipe-pane
    ssh.push(stdout="bash")            # foreground check (display-message)
    ssh.push(stdout="0")               # pipe-log size BEFORE we send (start offset)
    ssh.push()                         # send-keys C-u (clear stale input)
    ssh.push()                         # send-keys -l <command>
    ssh.push()                         # send-keys Enter
    # First poll: empty / no marker yet — exercises the "wait" branch.
    ssh.push(stdout="")
    # Second poll: full slice of the log with the D marker present.
    ssh.push(stdout=_osc_d_log("echo hello", "hello\n", exit_code=0))

    manager = TerminalManager()
    runtime = _make_runtime(ssh)
    session_id = uuid4()

    result = await manager.run_command(
        runtime=runtime,
        session_id=session_id,
        terminal_id="0",
        command="echo hello",
        timeout=5,
        label_hint="echo hello",
    )

    assert result.exit_status == 0
    assert result.stdout == "hello"
    # Option A: stderr is intentionally empty — the pane interleaves stdout
    # and stderr the way a real terminal does; everything lands in stdout.
    assert result.stderr == ""

    # The agent's command was sent plainly via send-keys -l, no wrapper.
    # The visible-in-the-pane line is exactly what the agent asked for.
    sendkeys_literal = [c for c in ssh.commands if "send-keys" in c and " -l '" in c]
    assert sendkeys_literal, ssh.commands
    assert "'echo hello'" in sendkeys_literal[0]
    assert "(" not in sendkeys_literal[0].split(" -l ", 1)[1]  # no subshell wrapper


@pytest.mark.asyncio
async def test_run_command_reuses_existing_terminal_via_pipe_log():
    """Second call into the same terminal_id should skip session creation."""
    ssh = _SSHStub()
    # First call: full provisioning + pipe-log capture.
    ssh.push(stdout="present")                # tmux probe
    ssh.push()                                # prep
    ssh.push()                                # new-session
    ssh.push()                                # options
    ssh.push(stdout="bash")                   # foreground check
    ssh.push(stdout="0")                      # initial pipe-log size
    ssh.push()                                # C-u
    ssh.push()                                # send-keys -l
    ssh.push()                                # Enter
    ssh.push(stdout=_osc_d_log("true", "", 0))  # poll returns full log+marker
    # Second call: in-memory record exists, so we hit has-session first
    # (returns yes → skip new-session), then go straight into the run flow.
    ssh.push(stdout="yes")                    # has-session
    ssh.push(stdout="bash")                   # foreground check
    ssh.push(stdout="0")                      # pipe-log size (still 0 — stub is naive)
    ssh.push()                                # C-u
    ssh.push()                                # send-keys -l
    ssh.push()                                # Enter
    ssh.push(stdout=_osc_d_log("true", "", 0))

    manager = TerminalManager()
    runtime = _make_runtime(ssh)
    session_id = uuid4()

    await manager.run_command(
        runtime=runtime, session_id=session_id, terminal_id="0",
        command="true", timeout=5,
    )
    new_session_count_after_first = sum(1 for c in ssh.commands if "tmux new-session" in c)
    await manager.run_command(
        runtime=runtime, session_id=session_id, terminal_id="0",
        command="true", timeout=5,
    )
    new_session_count_after_second = sum(1 for c in ssh.commands if "tmux new-session" in c)
    # Crucially the second invocation does NOT create another tmux session.
    assert new_session_count_after_first == 1
    assert new_session_count_after_second == 1


def test_build_visible_command_is_bare_when_no_overrides():
    """No env/cwd → the pane should see the agent's command verbatim."""
    manager = TerminalManager()
    assert manager._build_visible_command("ls -la", env={}, cwd=None) == "ls -la"
    assert manager._build_visible_command("ls -la", env=None, cwd=None) == "ls -la"


def test_build_visible_command_wraps_cwd_in_subshell():
    """`cwd` is scoped to one command — wrap in (cd && cmd) so it doesn't persist."""
    manager = TerminalManager()
    out = manager._build_visible_command("ls -la", env={}, cwd="/tmp/work")
    assert out == "(cd '/tmp/work' && ls -la)"


def test_build_visible_command_wraps_env_in_subshell():
    """`env` is scoped — wrap in (export K=V; cmd) so vars don't leak."""
    manager = TerminalManager()
    out = manager._build_visible_command("printenv FOO", env={"FOO": "bar"}, cwd=None)
    assert out == "(export FOO='bar'; printenv FOO)"


def test_build_visible_command_combines_cwd_and_env():
    """Both overrides: cwd first (with &&), then env (with ;), then the command."""
    manager = TerminalManager()
    out = manager._build_visible_command(
        "make build", env={"CC": "gcc"}, cwd="/src",
    )
    assert out == "(cd '/src' && export CC='gcc'; make build)"


@pytest.mark.asyncio
async def test_run_command_refuses_when_foreground_busy():
    """When pane_current_command isn't a shell, we surface TerminalBlockedError.

    This is the safety mechanism that prevents agents from injecting commands
    into a user's vim/top/build session.
    """
    ssh = _SSHStub()
    # Fresh manager has no in-memory record so _ensure_terminal_locked skips
    # the `has-session` check and goes straight into:
    #   tmux probe → prep → new-session → options → display-message.
    ssh.push(stdout="present")     # tmux probe
    ssh.push()                     # prep
    ssh.push()                     # new-session
    ssh.push()                     # options (remain-on-exit + history-limit + pipe-pane)
    ssh.push(stdout="vim")         # display-message (foreground check) — non-shell → refuse

    manager = TerminalManager()
    runtime = _make_runtime(ssh)
    session_id = uuid4()

    with pytest.raises(TerminalBlockedError) as info:
        await manager.run_command(
            runtime=runtime, session_id=session_id, terminal_id="0",
            command="ls", timeout=5,
        )
    assert info.value.reason == "user_foreground_process"
    assert info.value.current_command == "vim"


@pytest.mark.asyncio
async def test_run_command_fails_loud_when_tmux_missing():
    """If the guest image lacks tmux, surface TerminalUnavailableError immediately
    instead of letting the exit-file poll hang for the full timeout.

    Regression guard for the "rebuild the base image" gap — agents and users
    should get a clear, actionable error in seconds, not 5 minutes of silence.
    """
    from app.services.runtime.terminal_manager import TerminalUnavailableError

    ssh = _SSHStub()
    ssh.push(stdout="missing")     # tmux probe → not present

    manager = TerminalManager()
    runtime = _make_runtime(ssh)

    with pytest.raises(TerminalUnavailableError) as info:
        await manager.run_command(
            runtime=runtime,
            session_id="sess",
            terminal_id="0",
            command="ls",
            timeout=5,
        )
    assert info.value.reason == "tmux_not_installed"
    # The probe should be the only command issued — we must NOT proceed to
    # prep / new-session etc. when tmux is missing.
    assert len(ssh.commands) == 1


@pytest.mark.asyncio
async def test_terminal_manager_shutdown_cancels_tracked_background_tasks():
    """The lifespan `finally` calls `TerminalManager.shutdown()`. Every
    fire-and-forget watcher spawned via `_spawn_background_task` has to be
    cancelled within the deadline, otherwise uvicorn --reload waits forever
    on an asyncssh poll loop.
    """
    manager = TerminalManager()

    async def _never_ending() -> None:
        await asyncio.sleep(60)

    task = manager._spawn_background_task(_never_ending())
    assert task in manager._background_tasks

    await manager.shutdown(timeout=0.5)
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_terminal_manager_shutdown_is_noop_when_no_tasks():
    manager = TerminalManager()
    # Must complete instantly — no live tasks, nothing to cancel.
    await manager.shutdown(timeout=0.5)


@pytest.mark.asyncio
async def test_terminal_manager_background_task_self_evicts_on_completion():
    """Steady-state: completed watchers must NOT linger in the registry,
    otherwise the set grows unbounded and shutdown latency creeps up over
    time. The done_callback handles eviction.
    """
    manager = TerminalManager()

    async def _quick() -> None:
        return None

    task = manager._spawn_background_task(_quick())
    await task
    # Give the loop a tick for the done_callback.
    await asyncio.sleep(0)
    assert task not in manager._background_tasks


@pytest.mark.asyncio
async def test_run_command_rejects_invalid_terminal_id():
    ssh = _SSHStub()
    manager = TerminalManager()
    runtime = _make_runtime(ssh)
    with pytest.raises(ValueError):
        await manager.run_command(
            runtime=runtime,
            session_id="sess",
            terminal_id="../escape",
            command="ls",
            timeout=5,
        )


@pytest.mark.asyncio
async def test_rehydrate_filters_by_session_prefix():
    """Recovery after backend restart only adopts tmux sessions tied to this chat."""
    ssh = _SSHStub()
    # tmux list-sessions returns mixed names; we should keep only the
    # `sentinel_<slug>_*` entries derived from our session id.
    session_id = uuid4()
    slug = "".join(ch for ch in str(session_id) if ch.isalnum())[:12]
    listing = "\n".join(
        [
            f"sentinel_{slug}_0",
            f"sentinel_{slug}_build",
            "sentinel_otherchat_xx",
            "unrelated-tmux-session",
        ]
    )
    ssh.push(stdout=listing)

    manager = TerminalManager()
    runtime = _make_runtime(ssh)

    recovered = await manager.rehydrate(runtime=runtime, session_id=session_id)
    ids = sorted(r.terminal_id for r in recovered)
    assert ids == ["0", "build"]




@pytest.mark.asyncio
async def test_broadcaster_receives_lifecycle_events():
    """terminal_opened/closed/busy events should reach the configured broadcaster.

    The frontend pill UI is driven by these events, so silently missing them
    would mean pills never appear.
    """
    events: list[tuple[str, dict[str, Any]]] = []

    async def _capture(session_id: str, payload: dict[str, Any]) -> None:
        events.append((session_id, payload))

    configure_terminal_event_broadcaster(_capture)
    try:
        ssh = _SSHStub()
        ssh.push(stdout="present")     # tmux probe
        ssh.push()                     # prep
        ssh.push()                     # new-session
        ssh.push()                     # options
        ssh.push(stdout="bash")        # foreground check
        ssh.push()                     # token dir mkdir
        ssh.push()                     # C-u
        ssh.push()                     # wrapper send
        ssh.push()                     # Enter
        ssh.push(stdout="0")           # exit poll
        ssh.push(stdout="")            # stdout
        ssh.push(stdout="")            # stderr
        ssh.push()                     # cleanup

        manager = TerminalManager()
        runtime = _make_runtime(ssh)
        await manager.run_command(
            runtime=runtime,
            session_id="sess-1",
            terminal_id="0",
            command="true",
            timeout=5,
        )
    finally:
        configure_terminal_event_broadcaster(None)

    event_types = [payload["type"] for _, payload in events]
    # Expect opened (once) followed by busy=True/busy=False around the run.
    assert "terminal_opened" in event_types
    assert any(payload["type"] == "terminal_busy" and payload.get("busy") is True for _, payload in events)
    assert any(payload["type"] == "terminal_busy" and payload.get("busy") is False for _, payload in events)
