"""Pane output and layout over tmux control mode; the UI owns scrollback."""

from __future__ import annotations

import asyncio
import base64
import logging
import json
import re
from shlex import quote, split

from app.services.runtime.panes import validate_target
from app.services.runtime.tmux import TMUX_HISTORY_LIMIT, tmux_host_socket_path

logger = logging.getLogger(__name__)
_OCTAL = re.compile(rb"\\([0-7]{3})")
# OSC 133 semantic prompt marks: introducer, body without ESC/BEL, then ST or BEL.
_MARK_INTRODUCER = b"\x1b]133;"
_PROMPT_MARK = re.compile(rb"\x1b\]133;[^\x07\x1b]*(?:\x07|\x1b\\)")
_PARTIAL_MARK_LIMIT = 128
_FORMAT = (
    "#{window_id} #{pane_id} #{pane_dead} #{pane_active} "
    "#{pane_width} #{pane_height} #{pane_left} #{pane_top} "
    "#{window_width} #{window_height} #{cursor_x} #{cursor_y} "
    # Older tmux versions omit bracket_paste_flag; keep a numeric field in the layout.
    "#{alternate_on} #{cursor_flag} #{keypad_cursor_flag} #{?bracket_paste_flag,1,0} "
    "x#{q:window_name} x#{q:pane_title} x#{q:host} x#{q:pane_current_command}"
)


def unescape_output(data: bytes) -> bytes:
    return _OCTAL.sub(lambda match: bytes([int(match[1], 8)]), data)


class PromptMarkFilter:
    """Drop OSC 133 prompt marks from one pane's stream, across any chunking.

    The viewer core honours shell integration: on resize it erases the current
    prompt region and waits for the shell to redraw it. Behind tmux nothing
    redraws those cells, because bash repaints only its own readline line and
    tmux sends no diff for a grid it did not change, so the prompt and anything
    printed after it would stay blank until new output arrives. Command capture
    reads the marks from the pane log, so the viewer never needs them.
    """

    def __init__(self) -> None:
        self._tail = b""

    def __call__(self, data: bytes) -> bytes:
        data = _PROMPT_MARK.sub(b"", self._tail + data)
        self._tail = b""
        held = _partial_mark_start(data)
        # Never stall a pane on output that only looks like a truncated mark.
        if held is not None and len(data) - held <= _PARTIAL_MARK_LIMIT:
            data, self._tail = data[:held], data[held:]
        return data


def _partial_mark_start(data: bytes) -> int | None:
    """Offset of a trailing, still incomplete prompt mark, if there is one."""
    start = data.rfind(_MARK_INTRODUCER)
    # Complete marks are already gone, so a remaining introducer is cut off.
    if start >= 0:
        return start
    for size in range(min(len(_MARK_INTRODUCER) - 1, len(data)), 0, -1):
        if data.endswith(_MARK_INTRODUCER[:size]):
            return len(data) - size
    return None


def parse_layout(data: bytes) -> list[dict]:
    windows: dict[str, dict] = {}
    for row in data.decode("utf-8").splitlines():
        values = split(row)
        window_id, pane_id = values[:2]
        (
            dead,
            active,
            width,
            height,
            left,
            top,
            ww,
            wh,
            cx,
            cy,
            alt,
            cursor,
            keys,
            paste,
        ) = map(int, values[2:16])
        name, title, host, command = (item[1:] for item in values[16:20])
        window = windows.setdefault(
            window_id,
            {
                "window_id": window_id,
                "name": name,
                "width": ww,
                "height": wh,
                "panes": [],
            },
        )
        window["panes"].append(
            {
                "pane_id": pane_id,
                "window_id": window_id,
                "window_name": name,
                "title": "" if title == host else title,
                "command": command,
                "dead": bool(dead),
                "active": bool(active),
                "busy": not dead and command not in {"bash", "sh", "zsh", "fish", "dash"},
                "width": width,
                "height": height,
                "left": left,
                "top": top,
                "cursor_x": cx,
                "cursor_y": cy,
                "alternate": bool(alt),
                "cursor": bool(cursor),
                "application_cursor": bool(keys),
                "bracketed_paste": bool(paste),
            }
        )
    return list(windows.values())


class ControlClient:
    """One command at a time, with pane bytes delivered in server order."""

    def __init__(self, process, on_output, on_change):
        self.process = process
        self.on_output = on_output
        self.on_change = on_change
        self.lock = asyncio.Lock()
        self.pending = None
        self.result_handler = None
        self.attached = asyncio.Event()

    async def command(self, args, *, result_handler=None):
        async with self.lock:
            line = " ".join(quote(str(arg)) for arg in args)
            if "\n" in line or "\r" in line:
                raise ValueError("Terminal command arguments cannot contain newlines")
            self.pending = asyncio.get_running_loop().create_future()
            self.result_handler = result_handler
            self.process.stdin.write((line + "\n").encode())
            try:
                async with asyncio.timeout(15):
                    return await self.pending
            finally:
                self.pending = None
                self.result_handler = None

    async def read(self):
        guard = None
        body = []
        try:
            while line := await self.process.stdout.readline():
                line = line.rstrip(b"\n")
                if guard is not None:
                    if line in (b"%end " + guard, b"%error " + guard):
                        data = b"\n".join(body)
                        if self.pending is not None and not self.pending.done():
                            if line.startswith(b"%error"):
                                self.pending.set_exception(
                                    RuntimeError(data.decode(errors="replace"))
                                )
                            else:
                                if self.result_handler:
                                    await self.result_handler(data)
                                self.pending.set_result(data)
                        else:
                            self.attached.set()
                        guard, body = None, []
                    else:
                        body.append(line)
                elif line.startswith(b"%begin "):
                    guard = line[7:]
                elif line.startswith(b"%output "):
                    _, pane, data = line.split(b" ", 2)
                    await self.on_output(pane.decode(), unescape_output(data))
                elif line.startswith(b"%exit"):
                    return
                else:
                    await self.on_change(line)
        finally:
            if self.pending is not None and not self.pending.done():
                self.pending.set_exception(RuntimeError("Terminal control connection closed"))


async def serve_terminal(manager, session_id, websocket, messages, *, on_panes=None):
    socket = tmux_host_socket_path(session_id, root=manager.workspace_location)
    process = await manager.ssh.create_process(
        await manager._tmux_command(["-C", "-S", socket, "attach-session", "-t", "sentinel"]),
        encoding=None,
    )
    initialized: set[str] = set()
    prompt_marks: dict[str, PromptMarkFilter] = {}
    panes: dict[str, dict] = {}
    changed = asyncio.Event()
    previous = None

    async def output(pane_id, data):
        if pane_id in initialized:
            data = prompt_marks.setdefault(pane_id, PromptMarkFilter())(data)
            if not data:
                return
            await websocket.send_json(
                {
                    "type": "pane_output",
                    "pane_id": pane_id,
                    "data": base64.b64encode(data).decode(),
                }
            )

    async def notification(line):
        if line.startswith(b"%layout-change "):
            # tmux emits geometry before output from the resized application.
            # Forward it here, rather than waiting for the metadata poll.
            layout = line.split(b" ")[2]
            for width, height, left, top, number in re.findall(
                rb"(\d+)x(\d+),(\d+),(\d+),(\d+)(?=[},]|$)", layout
            ):
                pane_id = "%" + number.decode()
                if pane_id in initialized:
                    await websocket.send_json(
                        {
                            "type": "pane_resize",
                            "pane_id": pane_id,
                            "cols": int(width),
                            "rows": int(height),
                        }
                    )
        changed.set()

    control = ControlClient(process, output, notification)

    async def snapshot(pane):
        pane_id = pane["pane_id"]

        async def deliver(data):
            # capture-pane -C encodes control bytes. A final newline is a command
            # separator, not an extra screen row (ControlClient already removes it).
            screen = _PROMPT_MARK.sub(b"", unescape_output(data).replace(b"\n", b"\r\n"))
            prompt_marks.pop(pane_id, None)
            await websocket.send_json(
                {
                    "type": "pane_snapshot",
                    "pane": pane,
                    "data": base64.b64encode(screen).decode(),
                }
            )
            initialized.add(pane_id)

        # The snapshot's %end marks the precise switch to live %output. Earlier
        # notifications are already in this snapshot and are deliberately ignored.
        await control.command(
            [
                "capture-pane",
                "-p",
                "-e",
                "-C",
                "-S",
                str(-TMUX_HISTORY_LIMIT),
                "-t",
                pane_id,
            ],
            result_handler=deliver,
        )

    async def refresh():
        nonlocal previous, panes
        windows = parse_layout(
            await control.command(["list-panes", "-s", "-t", "sentinel", "-F", _FORMAT])
        )
        panes = {p["pane_id"]: p for w in windows for p in w["panes"]}
        initialized.intersection_update(panes)
        for closed in prompt_marks.keys() - panes.keys():
            del prompt_marks[closed]
        # Cursor movement is reflected by raw output; it is not a layout update.
        signature = [
            {
                **w,
                "panes": [
                    {
                        k: v
                        for k, v in p.items()
                        if k
                        not in {
                            "cursor_x",
                            "cursor_y",
                            "cursor",
                            "application_cursor",
                            "bracketed_paste",
                            "alternate",
                        }
                    }
                    for p in w["panes"]
                ],
            }
            for w in windows
        ]
        if signature != previous:
            await websocket.send_json({"type": "terminal_layout", "windows": windows})
            if on_panes:
                await on_panes(list(panes.values()))
            previous = signature
        for pane_id, pane in list(panes.items()):
            if pane_id not in initialized:
                await snapshot(pane)

    async def updates():
        async with asyncio.timeout(15):
            await control.attached.wait()
        # tmux still owns processes and split geometry; its decorations and mouse
        # copy mode do not belong in a native pane view.
        await control.command(["set-option", "-g", "pane-border-status", "off"])
        # resize-window sets manual sizing per window. A global manual default
        # crashes tmux 3.4 when creating another window.
        while True:
            changed.clear()
            try:
                await refresh()
            except (ValueError, RuntimeError):
                logger.warning(
                    "Could not refresh terminal layout session=%s",
                    session_id,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(changed.wait(), 1)
            except TimeoutError:
                pass

    async def input_messages():
        async with asyncio.timeout(15):
            await control.attached.wait()
        while True:
            message = await messages.get()
            if message.get("type") == "websocket.disconnect":
                return
            try:
                payload = json.loads(message.get("text") or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("Expected a terminal action")
                action = payload.get("type")
                pane_id = payload.get("pane_id")
                window_id = payload.get("window_id")
                if pane_id is not None:
                    validate_target(pane_id, "%")
                    if pane_id not in panes:
                        raise ValueError("Pane no longer exists")
                if window_id is not None:
                    validate_target(window_id, "@")
                    if not any(p["window_id"] == window_id for p in panes.values()):
                        raise ValueError("Window no longer exists")
                if action == "input" and pane_id:
                    data = base64.b64decode(payload["data"], validate=True)
                    if len(data) > 65536:
                        raise ValueError("Terminal input is too large")
                    # Hex bytes are not parsed as tmux keys or shell syntax.
                    for offset in range(0, len(data), 1024):
                        await control.command(
                            [
                                "send-keys",
                                "-H",
                                "-t",
                                pane_id,
                                *(f"{b:02x}" for b in data[offset : offset + 1024]),
                            ]
                        )
                elif action == "resize" and window_id:
                    cols = max(20, min(1000, int(payload["cols"])))
                    rows = max(5, min(1000, int(payload["rows"])))
                    await control.command(
                        [
                            "resize-window",
                            "-t",
                            window_id,
                            "-x",
                            str(cols),
                            "-y",
                            str(rows),
                        ]
                    )
                elif action == "select_pane" and pane_id:
                    await control.command(["select-pane", "-t", pane_id])
                elif action == "new_window":
                    await control.command(["new-window", "-d", "-t", "sentinel"])
                elif action == "split" and pane_id:
                    direction = payload.get("direction")
                    if direction not in {"horizontal", "vertical"}:
                        raise ValueError("Invalid split direction")
                    await control.command(
                        [
                            "split-window",
                            "-d",
                            "-h" if direction == "horizontal" else "-v",
                            "-t",
                            pane_id,
                        ]
                    )
                elif action in {"close_pane", "restart_pane"} and pane_id:
                    if action == "restart_pane" and not panes[pane_id]["dead"]:
                        raise ValueError("Pane is still running")
                    await control.command(
                        [
                            "kill-pane" if action == "close_pane" else "respawn-pane",
                            "-t",
                            pane_id,
                        ]
                    )
                else:
                    raise ValueError("Unknown terminal action")
                changed.set()
            except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                await websocket.send_json({"type": "terminal_action_error", "message": str(exc)})

    tasks = [
        asyncio.create_task(control.read()),
        asyncio.create_task(updates()),
        asyncio.create_task(input_messages()),
    ]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if tasks[0] in done:
            await websocket.send_json({"type": "terminal_layout", "windows": []})
            await websocket.close()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        process.terminate()
        await process.wait()
