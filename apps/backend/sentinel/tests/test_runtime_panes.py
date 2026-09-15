import asyncio
from uuid import uuid4

import pytest

from app.services.runtime.panes import TmuxPanes, validate_target
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.runtime.workspace import WorkspaceLocation


def test_targets_are_explicit_and_cannot_inject_tmux_commands():
    assert validate_target("%12", "%") == "%12"
    for invalid in ["0", "@1", "%1; kill-server", "sentinel:0.1"]:
        with pytest.raises(ValueError):
            validate_target(invalid, "%")


@pytest.mark.asyncio
async def test_unicode_prompt_preserves_wrapped_command_before_and_after_resize(
    tmp_path, container_transport
):
    manager = RuntimeTerminalManager(
        container_transport,
        workspace_location=WorkspaceLocation("/workspace", "/var/lib/sentinel"),
    )
    bridge = TmuxPanes(manager)
    session = str(uuid4())
    try:
        pane = (await bridge.create_window(session, "unicode"))["pane_id"]
        command = "printf 'BEGIN_MARKER\\n'\n" + "\n".join(
            f"printf 'OUTPUT_{i:02d} padding padding padding\\n'" for i in range(15)
        )
        result = await bridge.execute(session, command, pane_id=pane, timeout=10)
        assert result["exit_status"] == 0, result
        assert result["stdout"].startswith("BEGIN_MARKER\n")
        for width, height in [(200, 50), (80, 24), (130, 45)]:
            await bridge.command(session, ["resize-window", "-x", str(width), "-y", str(height)])
            history = await bridge.read(session, pane, 1000)
            assert "eval $'" in history, "the beginning of the command was overwritten"
            assert history.count("BEGIN_MARKER") == 2, "preserve both command and output"
        result = await bridge.execute(session, "printf 'café 日本語\\n'", pane_id=pane, timeout=10)
        assert result["stdout"] == "café 日本語", result
    finally:
        await bridge.command(session, ["kill-server"])


@pytest.mark.asyncio
async def test_agent_input_bypasses_copy_mode_and_preserves_history(tmp_path, container_transport):
    manager = RuntimeTerminalManager(
        container_transport,
        workspace_location=WorkspaceLocation("/workspace", "/var/lib/sentinel"),
    )
    bridge = TmuxPanes(manager)
    session = str(uuid4())
    try:
        pane = (await bridge.create_window(session, "scrollback"))["pane_id"]
        await bridge.execute(
            session,
            "for ((i=1;i<=600;i++)); do printf 'HISTORY_%04d\\n' \"$i\"; done",
            pane_id=pane,
            timeout=10,
        )
        await bridge.command(session, ["copy-mode", "-t", pane])
        result = await bridge.execute(
            session,
            "printf 'first\\n'\nprintf 'second\\n'",
            pane_id=pane,
            timeout=10,
        )
        assert result["exit_status"] == 0, result
        assert result["stdout"] == "first\nsecond"
        mode = await bridge.command(
            session, ["display-message", "-p", "-t", pane, "#{pane_in_mode}"]
        )
        assert mode.strip() == "1", "agent input should leave the user's scrollback open"
        history = await bridge.read(session, pane, 1000)
        assert "HISTORY_0001" in history and "HISTORY_0600" in history
        completed = asyncio.Event()

        async def on_complete(job, stdout, stderr):
            assert "ANSWER:hello" in stdout
            completed.set()

        await bridge.execute(
            session,
            'read -r answer; printf "ANSWER:%s" "$answer"',
            pane_id=pane,
            background=True,
            timeout=10,
            on_complete=on_complete,
        )
        await bridge.input(session, pane, "hello", "Enter")
        await asyncio.wait_for(completed.wait(), 15)
    finally:
        await bridge.command(session, ["kill-server"])


@pytest.mark.asyncio
async def test_native_pane_execution_and_background_share_shell(tmp_path, container_transport):
    manager = RuntimeTerminalManager(
        container_transport,
        workspace_location=WorkspaceLocation("/workspace", "/var/lib/sentinel"),
    )
    bridge = TmuxPanes(manager)
    session = str(uuid4())
    try:
        first = await bridge.create_window(session, "test")
        second = await bridge.split(session, first["pane_id"], "horizontal", title="API server")
        tree = await bridge.tree(session)
        assert len(tree) == 1 and len(tree[0]["panes"]) == 2
        assert tree[0]["name"] == "test"
        assert tree[0]["panes"][1]["title"] == "API server"
        await bridge.rename_window(session, first["window_id"], "Development tools")
        await bridge.rename_pane(session, second["pane_id"], "Test runner")
        renamed = (await bridge.tree(session))[0]
        assert renamed["name"] == "Development tools"
        assert all(p["window_name"] == "Development tools" for p in renamed["panes"])
        assert renamed["panes"][1]["title"] == "Test runner"
        await bridge.rename_pane(session, second["pane_id"], "")
        assert (await bridge.tree(session))[0]["panes"][1]["title"] == ""
        with pytest.raises(ValueError, match="Multiple panes"):
            await bridge.execute(session, "true")
        await bridge.command(session, ["select-pane", "-t", second["pane_id"]])
        result = await bridge.execute(
            session,
            "export PANE_VALUE=first; printf first-result",
            pane_id=first["pane_id"],
            timeout=10,
        )
        assert result["exit_status"] == 0 and "first-result" in result["stdout"], result
        result = await bridge.execute(
            session, 'printf "%s" "${PANE_VALUE-unset}"', pane_id=second["pane_id"], timeout=10
        )
        assert "unset" in result["stdout"], result
        completed = asyncio.Event()

        async def on_complete(job, stdout, stderr):
            assert job["returncode"] == 0
            completed.set()

        await bridge.execute(
            session,
            "export PANE_VALUE=background; sleep 0.2; printf background-result",
            pane_id=first["pane_id"],
            timeout=10,
            background=True,
            on_complete=on_complete,
        )
        await asyncio.wait_for(completed.wait(), 20)
        result = await bridge.execute(
            session, 'printf "$PANE_VALUE"', pane_id=first["pane_id"], timeout=10
        )
        assert "background" in result["stdout"], result
        assert "unset" in await bridge.read(session, second["pane_id"])
        completed.clear()
        await bridge.execute(
            session,
            'read -r ANSWER; printf "answered:%s" "$ANSWER"',
            pane_id=second["pane_id"],
            timeout=10,
            background=True,
            on_complete=on_complete,
        )
        await bridge.input(session, second["pane_id"], "hello", "Enter")
        await asyncio.wait_for(completed.wait(), 20)
        assert "answered:hello" in await bridge.read(session, second["pane_id"])
        third = await bridge.create_window(session, "other")
        assert len(await bridge.tree(session)) == 2
        await bridge.close_window(session, third["window_id"])
        assert len(await bridge.tree(session)) == 1

        await bridge.close_pane(session, second["pane_id"])
        assert len((await bridge.tree(session))[0]["panes"]) == 1
        with pytest.raises(ValueError, match="no longer exists"):
            await bridge.read(session, second["pane_id"])
    finally:
        await manager.delete_session_state(session)
        await manager.close()


@pytest.mark.asyncio
async def test_viewer_attaches_to_session_and_selects_native_panes(tmp_path, container_transport):
    manager = RuntimeTerminalManager(
        container_transport,
        workspace_location=WorkspaceLocation("/workspace", "/var/lib/sentinel"),
    )
    bridge = TmuxPanes(manager)
    session = str(uuid4())

    class Viewer:
        def __init__(self):
            self.input = asyncio.Queue()
            self.output = bytearray()
            self.states = []

        async def receive(self):
            return await self.input.get()

        async def send_bytes(self, data):
            self.output.extend(data)

        async def send_json(self, data):
            self.states.append(data)

        async def close(self):
            pass

    viewer = Viewer()
    snapshots = []

    async def snapshot(panes):
        snapshots.append(panes)

    task = asyncio.create_task(manager.attach_ws(session, viewer, on_panes=snapshot))

    async def wait_until(check):
        async with asyncio.timeout(15):
            while not await check():
                if task.done():
                    task.result()
                await asyncio.sleep(0.05)

    async def ready():
        return bool(snapshots)

    try:
        await wait_until(ready)
        await bridge.command(session, ["set-buffer", "-w", "clipboard café"])

        async def clipboard_received():
            return b"\x1b]52;" in viewer.output

        await wait_until(clipboard_received)
        first = (await bridge.tree(session))[0]["panes"][0]["pane_id"]
        await viewer.input.put({"type": "websocket.receive", "bytes": b"\x02%"})

        async def split():
            return len((await bridge.tree(session))[0]["panes"]) == 2

        await wait_until(split)
        second = next(
            p["pane_id"] for p in (await bridge.tree(session))[0]["panes"] if p["pane_id"] != first
        )
        import json

        await viewer.input.put(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "select_pane", "pane_id": first}),
            }
        )

        async def selected():
            return (
                await bridge.command(session, ["display-message", "-p", "#{pane_id}"])
            ).strip() == first

        await wait_until(selected)
        await viewer.input.put(
            {
                "type": "websocket.receive",
                "text": json.dumps({"type": "resize", "cols": 110, "rows": 35}),
            }
        )

        async def resized():
            return (
                await bridge.command(
                    session, ["display-message", "-p", "#{window_width}:#{window_height}"]
                )
            ).strip() == "110:34"

        await wait_until(resized)
        result = await bridge.execute(session, "printf SECOND_PANE", pane_id=second, timeout=10)
        assert result["exit_status"] == 0 and "SECOND_PANE" in result["stdout"]
        assert "SECOND_PANE" not in await bridge.read(session, first)
        await viewer.input.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 10)
        assert len((await bridge.tree(session))[0]["panes"]) == 2
        await bridge.close_pane(session, first)
        assert (await bridge.tree(session))[0]["panes"][0]["pane_id"] == second
        # A fresh viewer attaches to the surviving pane without recreating a main window.
        viewer = Viewer()
        task = asyncio.create_task(manager.attach_ws(session, viewer))

        async def reattached():
            return bool(viewer.output)

        await wait_until(reattached)
        await bridge.close_pane(session, second)
        await asyncio.wait_for(task, 10)
        assert any(state.get("empty") for state in viewer.states)
        assert await bridge.tree(session) == []

    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await manager.delete_session_state(session)
        await manager.close()


@pytest.mark.asyncio
async def test_terminal_disconnect_cancels_pending_workspace_setup():
    from types import SimpleNamespace
    from app.services.runtime.terminal_manager import RuntimeTerminalManager
    from app.services.runtime.workspace import WorkspaceLocation

    manager = RuntimeTerminalManager(
        None, workspace_location=WorkspaceLocation("/workspace", "/var/lib/sentinel")
    )
    preparing, cancelled = asyncio.Event(), asyncio.Event()

    async def setup(_):
        preparing.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def receive():
        await preparing.wait()
        return {"type": "websocket.disconnect"}

    manager.ensure_session = setup
    await asyncio.wait_for(manager.attach_ws("session", SimpleNamespace(receive=receive)), 1)
    assert cancelled.is_set()
