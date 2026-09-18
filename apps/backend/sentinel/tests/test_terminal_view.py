"""Real tmux control-stream tests; no user sessions or workspace data involved."""

import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.runtime.local_transport import LocalTransport
from app.services.runtime.terminal_view import (
    PromptMarkFilter,
    serve_terminal,
    unescape_output,
)


def test_control_bytes_preserve_unicode_and_literal_escapes():
    assert (
        unescape_output(b"caf\xc3\xa9\\015\\012\\033[31m\\134033")
        == b"caf\xc3\xa9\r\n\x1b[31m\\033"
    )


def test_prompt_marks_are_dropped_from_the_viewer_stream():
    stream = PromptMarkFilter()
    prompt = b"\x1b]133;A\x1b\\\x1b[34mHINOKI\x1b[0m > \x1b]133;B\x1b\\"
    assert stream(prompt) == b"\x1b[34mHINOKI\x1b[0m > "
    assert stream(b"\x1b]133;C\x07hello\r\n") == b"hello\r\n"
    assert stream(b"\x1b]133;D;0\x1b\\") == b""


def test_prompt_marks_split_across_chunks_never_leak():
    stream = PromptMarkFilter()
    assert stream(b"done\r\n\x1b]13") == b"done\r\n"
    assert stream(b"3;D;0\x1b") == b""
    assert stream(b"\\") == b""
    assert stream(b"next") == b"next"


def test_other_sequences_and_long_lookalikes_pass_through():
    stream = PromptMarkFilter()
    assert stream(b"\x1b]0;title\x07\x1b[31mred\x1b[0m") == b"\x1b]0;title\x07\x1b[31mred\x1b[0m"
    stuck = b"\x1b]133;" + b"x" * 200
    assert stream(stuck) == stuck


class Viewer:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.events = []
        self.changed = asyncio.Event()

    async def send_json(self, event):
        self.events.append(event)
        self.changed.set()

    async def close(self, **kwargs):
        await self.messages.put({"type": "websocket.disconnect"})

    async def send(self, **payload):
        await self.messages.put({"type": "websocket.receive", "text": json.dumps(payload)})

    async def see(self, check, task):
        async with asyncio.timeout(10):
            while not check():
                if task.done():
                    task.result()
                    raise AssertionError("Viewer closed early")
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), 0.1)
                except TimeoutError:
                    pass

    def output(self, pane=None):
        return b"".join(
            base64.b64decode(e["data"])
            for e in self.events
            if e["type"] in {"pane_output", "pane_snapshot"}
            and (pane is None or e.get("pane_id", e.get("pane", {}).get("pane_id")) == pane)
        )


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("tmux") is None, reason="Requires tmux")
async def test_live_panes_history_and_reconnect(monkeypatch):
    from shlex import join

    with tempfile.TemporaryDirectory(dir="/tmp", prefix="sentinel-view-") as directory:
        socket = str(Path(directory) / "s")
        transport = LocalTransport()
        transport._env = dict(os.environ)

        async def command(args):
            return join(["tmux", "-u", *args])

        async def tmux(*args):
            result = await transport.run(await command(["-S", socket, *args]))
            assert result.exit_status == 0, result.stderr
            return result.stdout

        monkeypatch.setattr(
            "app.services.runtime.terminal_view.tmux_host_socket_path", lambda *a, **kw: socket
        )
        manager = SimpleNamespace(ssh=transport, _tmux_command=command, workspace_location=None)
        task = None
        try:
            await tmux(
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "sentinel",
                "-x",
                "80",
                "-y",
                "24",
                "bash --norc --noprofile",
            )
            await tmux("rename-window", "API's tests 日本語")
            await tmux("set-option", "-g", "history-limit", "50000")
            await tmux("send-keys", "-l", "printf 'BEFORE_%s\\n' ATTACH")
            await tmux("send-keys", "Enter")
            await asyncio.sleep(0.2)
            viewer = Viewer()
            task = asyncio.create_task(serve_terminal(manager, "test", viewer, viewer.messages))
            await viewer.see(lambda: b"BEFORE_ATTACH" in viewer.output(), task)
            layout = next(e for e in viewer.events if e["type"] == "terminal_layout")["windows"]
            assert layout[0]["name"] == "API's tests 日本語"
            pane = layout[0]["panes"][0]["pane_id"]
            await viewer.send(
                type="input",
                pane_id=pane,
                data=base64.b64encode(b"printf 'LIVE_%s\\n' READY\r").decode(),
            )
            await viewer.see(lambda: b"LIVE_READY" in viewer.output(), task)
            assert viewer.output().count(b"BEFORE_ATTACH") == 1
            await viewer.send(type="split", pane_id=pane, direction="horizontal")
            await viewer.see(
                lambda: any(
                    e["type"] == "terminal_layout" and len(e["windows"][0]["panes"]) == 2
                    for e in viewer.events
                ),
                task,
            )
            new_layout = [e for e in viewer.events if e["type"] == "terminal_layout"][-1]["windows"]
            second = next(p["pane_id"] for p in new_layout[0]["panes"] if p["pane_id"] != pane)
            await viewer.see(
                lambda: any(
                    e["type"] == "pane_snapshot" and e["pane"]["pane_id"] == second
                    for e in viewer.events
                ),
                task,
            )
            await viewer.send(
                type="input",
                pane_id=second,
                data=base64.b64encode(b"printf 'SECOND_%s\\n' ONLY\r").decode(),
            )
            await viewer.see(lambda: b"SECOND_ONLY" in viewer.output(second), task)
            assert b"SECOND_ONLY" not in viewer.output(pane)
            await viewer.send(type="resize", window_id=layout[0]["window_id"], cols=100, rows=40)
            await viewer.see(
                lambda: any(
                    e["type"] == "terminal_layout"
                    and e["windows"][0]["width"] == 100
                    and e["windows"][0]["height"] == 40
                    for e in viewer.events
                ),
                task,
            )
            await viewer.close()
            await asyncio.wait_for(task, 5)
            viewer = Viewer()
            task = asyncio.create_task(serve_terminal(manager, "test", viewer, viewer.messages))
            await viewer.see(lambda: b"SECOND_ONLY" in viewer.output(second), task)
            assert viewer.output(second).count(b"SECOND_ONLY") == 1
            assert b"SECOND_ONLY" not in viewer.output(pane)
            await viewer.send(type="input", pane_id="%99999", data="AA==")
            await viewer.see(
                lambda: any(e["type"] == "terminal_action_error" for e in viewer.events), task
            )
            await viewer.send(
                type="input",
                pane_id=second,
                data=base64.b64encode(b"printf 'STILL_%s\\n' CONNECTED\r").decode(),
            )
            await viewer.see(lambda: b"STILL_CONNECTED" in viewer.output(second), task)
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await tmux("kill-server")
