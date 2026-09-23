"""Native tmux window/pane operations scoped to one chat's private server."""

from __future__ import annotations

import asyncio
import re
from pathlib import PurePosixPath
from shlex import quote
from shlex import split as shell_split
from uuid import uuid4

from app.schemas.runtime import RuntimeExecResult
import app.services.runtime.container_transport as container_transport
from app.services.runtime.tmux import (
    build_host_tmux_command,
    build_pane_feed_script,
    tmux_host_socket_path,
)
from app.services.runtime.workspace import workspace_paths

FOREGROUND_WAIT_SECONDS = 20


def validate_target(value: str, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(re.escape(prefix) + r"[0-9]+", value):
        raise ValueError(
            f"Expected a tmux {'pane' if prefix == '%' else 'window'} ID ({prefix} followed by digits)"
        )
    return value


class TmuxPanes:
    def __init__(self, manager):
        self.manager = manager

    async def command(self, session_id, args, *, timeout=15):
        socket = tmux_host_socket_path(session_id, root=self.manager.workspace_location)
        result = await self.manager.ssh.run(
            await self.manager._tmux_command(["-S", socket, *args]),
            timeout=timeout,
        )
        if result.exit_status != 0:
            raise RuntimeError((result.stderr or "tmux operation failed").strip())
        return result.stdout

    async def tree(self, session_id):
        # Inspection must never boot a workspace, even if it stops mid-listing.
        if (
            isinstance(self.manager.ssh, container_transport.ContainerTransport)
            and not await self.manager.ssh.is_ready()
        ):
            return []
        socket = tmux_host_socket_path(session_id, root=self.manager.workspace_location)

        async def inspect(args):
            transport = self.manager.ssh
            if isinstance(transport, container_transport.ContainerTransport):
                return await transport.run_if_running(
                    build_host_tmux_command(["-S", socket, *args], os_name="linux"), timeout=15
                )
            return await transport.run(
                await self.manager._tmux_command(["-S", socket, *args]), timeout=15
            )

        exists = await inspect(["has-session", "-t", "sentinel"])
        if exists is None or exists.exit_status != 0:
            return []
        try:
            result = await inspect(
                [
                    "list-panes",
                    "-s",
                    "-t",
                    "sentinel",
                    "-F",
                    "#{window_id} #{pane_id} #{pane_dead} #{pane_active} "
                    "x#{q:pane_current_command} x#{q:window_name} x#{q:pane_title} x#{q:host}",
                ],
            )
            if result is None:
                return []
            if result.exit_status != 0:
                raise RuntimeError((result.stderr or "tmux operation failed").strip())
            rows = result.stdout
        except RuntimeError:
            # The final pane may exit between the existence check and listing.
            alive = await inspect(["has-session", "-t", "sentinel"])
            if alive is None or alive.exit_status != 0:
                return []
            raise
        windows = {}
        # Quote display names so spaces and punctuation cannot become field
        # separators. The prefix keeps empty titles representable.
        for row in rows.rstrip("\n").split("\n"):
            window, pane, dead, active, command, name, title, host = shell_split(row)
            command, name, title, host = (value[1:] for value in (command, name, title, host))
            title = "" if title == host else title
            item = windows.setdefault(window, {"window_id": window, "name": name, "panes": []})
            item["panes"].append(
                {
                    "pane_id": pane,
                    "window_id": window,
                    "window_name": name,
                    "title": title,
                    "busy": self.manager._lock_for(session_id, pane).locked()
                    or (dead != "1" and command not in {"bash", "sh", "zsh", "fish", "dash"}),
                    "dead": dead == "1",
                    "active": active == "1",
                    "command": command,
                }
            )
        return list(windows.values())

    async def require_pane(self, session_id, pane_id):
        validate_target(pane_id, "%")
        for window in await self.tree(session_id):
            for pane in window["panes"]:
                if pane["pane_id"] == pane_id:
                    return {**pane, "window_id": window["window_id"]}
        raise ValueError(f"Pane {pane_id} no longer exists. List terminals again.")

    async def create_window(self, session_id, name=None):
        windows = await self.tree(session_id)
        if not windows:
            await self.manager.ensure_session(session_id)
            windows = await self.tree(session_id)
            window = windows[0]
            if name:
                await self.command(session_id, ["rename-window", "-t", window["window_id"], name])
            return {"window_id": window["window_id"], "pane_id": window["panes"][0]["pane_id"]}
        args = ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", "sentinel"]
        if name:
            args += ["-n", name]
        window, pane = (await self.command(session_id, args)).split()
        return {"window_id": window, "pane_id": pane}

    async def split(self, session_id, pane_id, direction, title=None):
        await self.require_pane(session_id, pane_id)
        if direction not in {"horizontal", "vertical"}:
            raise ValueError("Direction must be horizontal or vertical")
        row = await self.command(
            session_id,
            [
                "split-window",
                "-d",
                "-h" if direction == "horizontal" else "-v",
                "-t",
                pane_id,
                "-P",
                "-F",
                "#{window_id} #{pane_id}",
            ],
        )
        window, pane = row.split()
        if title is not None:
            await self.rename_pane(session_id, pane, title)
        return {"window_id": window, "pane_id": pane}

    async def rename_window(self, session_id, window_id, name):
        validate_target(window_id, "@")
        if not any(w["window_id"] == window_id for w in await self.tree(session_id)):
            raise ValueError("Window no longer exists. List terminals again.")
        await self.command(session_id, ["rename-window", "-t", window_id, name])

    async def rename_pane(self, session_id, pane_id, title):
        await self.require_pane(session_id, pane_id)
        await self.command(session_id, ["select-pane", "-t", pane_id, "-T", title])

    async def close_pane(self, session_id, pane_id):
        await self.require_pane(session_id, pane_id)
        await self.command(session_id, ["kill-pane", "-t", pane_id])

    async def close_window(self, session_id, window_id):
        validate_target(window_id, "@")
        if not any(w["window_id"] == window_id for w in await self.tree(session_id)):
            raise ValueError("Window no longer exists. List terminals again.")
        await self.command(session_id, ["kill-window", "-t", window_id])

    async def read(self, session_id, pane_id, lines=100):
        await self.require_pane(session_id, pane_id)
        return await self.command(
            session_id, ["capture-pane", "-p", "-t", pane_id, "-S", str(-lines)]
        )

    async def input(self, session_id, pane_id, text="", key=None):
        pane = await self.require_pane(session_id, pane_id)
        if pane["dead"]:
            raise ValueError("Pane has exited")
        # Paste control bytes as well as text directly to the PTY. send-keys
        # routes them through copy mode when the user is reading scrollback.
        control_keys = {
            "Enter": "\r",
            "Tab": "\t",
            "Escape": "\x1b",
            "C-c": "\x03",
            "C-d": "\x04",
            "C-z": "\x1a",
        }
        if key in control_keys:
            text += control_keys[key]
            key = None
        if text:
            environment = await self.manager.runtime_environment()
            socket = tmux_host_socket_path(session_id, root=self.manager.workspace_location)
            result = await self.manager.ssh.run_script(
                build_pane_feed_script(socket, pane_id, os_name=environment.os),
                args=[text],
                timeout=120,
            )
            if result.exit_status != 0:
                raise RuntimeError("Could not send input to pane")
        if key:
            if key not in {
                "Enter",
                "Tab",
                "Escape",
                "C-c",
                "C-d",
                "C-z",
                "Up",
                "Down",
                "Left",
                "Right",
            }:
                raise ValueError("Unsupported key")
            await self.command(
                session_id,
                [
                    "send-keys",
                    "-X",
                    "-t",
                    pane_id,
                    "cancel",
                    ";",
                    "send-keys",
                    "-t",
                    pane_id,
                    key,
                ],
            )

    async def execute(
        self,
        session_id,
        command,
        *,
        pane_id=None,
        cwd=None,
        env=None,
        timeout=FOREGROUND_WAIT_SECONDS,
        background=False,
        on_complete=None,
    ):
        if pane_id is None:
            windows = await self.tree(session_id)
            if not windows:
                pane_id = (await self.create_window(session_id))["pane_id"]
            elif sum(len(w["panes"]) for w in windows) == 1:
                pane_id = windows[0]["panes"][0]["pane_id"]
            else:
                raise ValueError("Multiple panes exist. Choose pane_id from terminal_list.")
        pane = await self.require_pane(session_id, pane_id)
        if pane["dead"]:
            raise ValueError("Pane has exited. Create a window or split a live pane.")
        lock = self.manager._lock_for(session_id, pane_id)
        if lock.locked():
            raise ValueError("A command is already running in this pane")
        await lock.acquire()
        try:
            current = (
                await self.command(
                    session_id, ["display-message", "-p", "-t", pane_id, "#{pane_current_command}"]
                )
            ).strip()
            if current != "bash":
                raise ValueError(
                    f"Pane is running {current}; use pane_input to interact or choose another pane"
                )
            paths = workspace_paths(session_id, root=self.manager.workspace_location)
            log = str(PurePosixPath(paths.tmux) / f"pane-{pane_id[1:]}.log")
            # The controller owns logs. Never expose their paths to the shell.
            await self.command(session_id, ["pipe-pane", "-t", pane_id])
            await self.command(session_id, ["pipe-pane", "-t", pane_id, f"cat >> {quote(log)}"])
            size = await self.manager.ssh.run(
                f"wc -c < {quote(log)} 2>/dev/null || echo 0", timeout=10
            )
            offset = int(size.stdout.strip() or "0")
            if env and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in env):
                raise ValueError("Invalid environment variable name")
            await self.input(
                session_id,
                pane_id,
                "\x15" + self.manager._build_visible_command(command, cwd=cwd, env=env),
                "Enter",
            )
        except BaseException:
            lock.release()
            raise
        job_id = uuid4().hex
        owns_lock = True

        def release_lock():
            nonlocal owns_lock
            if owns_lock:
                owns_lock = False
                lock.release()

        async def finish():
            try:
                result = await self.manager._await_command_complete(
                    session_id, since_offset=offset, log_path=log, pane_id=pane_id
                )
            except Exception as exc:
                result = RuntimeExecResult(exit_status=-1, stdout="", stderr=str(exc))
            finally:
                release_lock()
            return {
                "pane_id": pane_id,
                "window_id": pane["window_id"],
                "exit_status": result.exit_status,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        # The manager owns the watcher, not the tool call waiting for it. A wait
        # expiry or agent interruption must not cancel monitoring of a live pane.
        task = asyncio.create_task(finish())
        task.add_done_callback(lambda _: release_lock())  # Also covers cancellation before start.
        key = (session_id, pane_id)
        self.manager._track_background_task(key, task)

        async def report_completion():
            result = await task
            if on_complete:
                await on_complete(
                    {
                        "id": job_id,
                        "pane_id": pane_id,
                        "window_id": pane["window_id"],
                        "command": command,
                        "status": "completed" if result["exit_status"] == 0 else "failed",
                        "returncode": result["exit_status"],
                    },
                    result["stdout"],
                    result["stderr"],
                )

        def detach():
            self.manager._track_background_task(key, asyncio.create_task(report_completion()))

        if not background:
            try:
                done, _ = await asyncio.wait({task}, timeout=min(timeout, FOREGROUND_WAIT_SECONDS))
            except asyncio.CancelledError:
                # Sentral cancels pending tool waits when interrupted; the shell
                # itself is still running and its eventual result must not be lost.
                detach()
                raise
            if done:
                return task.result()
        detach()
        return {
            "job_id": job_id,
            "pane_id": pane_id,
            "window_id": pane["window_id"],
            "status": "running",
        }
