"""One-shot host commands. Wait deadlines yield; only explicit termination kills.

Independent of workspace transports: no container, terminal pane, or attached workspace.
Handles live for this backend's lifetime and are scoped to their creating session.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

if os.name != "nt":
    import pwd

from sentral.errors import ToolValidationError

OUTPUT_LIMIT = 64 * 1024
MAX_PROCESSES = 256


def default_shell() -> str:
    if os.name == "nt":
        return (
            shutil.which("pwsh")
            or shutil.which("powershell")
            or os.environ.get("COMSPEC", "cmd.exe")
        )

    try:
        shell = pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        shell = None
    return shell or os.environ.get("SHELL") or "/bin/sh"


def shell_argv(shell: str, command: str, login: bool) -> list[str]:
    name = Path(shell).stem.lower()
    if name in {"pwsh", "powershell"}:
        return [shell, *([] if login else ["-NoProfile"]), "-Command", command]
    if name == "cmd":
        return [shell, "/c", command]
    return [shell, "-lc" if login else "-c", command]


@dataclass
class Job:
    owner: tuple[str, str]
    process: asyncio.subprocess.Process
    command: str
    cwd: str
    shell: str
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)
    collector: asyncio.Task | None = None

    async def drain(self, stream, name: str) -> None:
        while chunk := await stream.read(16384):
            setattr(self, name + "_bytes", getattr(self, name + "_bytes") + len(chunk))
            buffer = getattr(self, name)
            buffer.extend(chunk)
            del buffer[:-OUTPUT_LIMIT]

    async def collect(self) -> None:
        try:
            await asyncio.gather(
                self.drain(self.process.stdout, "stdout"),
                self.drain(self.process.stderr, "stderr"),
            )
            await self.process.wait()
        finally:
            self.done.set()

    def result(self, job_id: str) -> dict:
        return {
            "process_id": job_id,
            "status": "completed" if self.done.is_set() else "running",
            "exit_code": self.process.returncode,
            "stdout": self.stdout.decode("utf-8", errors="replace"),
            "stderr": self.stderr.decode("utf-8", errors="replace"),
            "stdout_truncated": self.stdout_bytes > len(self.stdout),
            "stderr_truncated": self.stderr_bytes > len(self.stderr),
            "command": self.command,
            "cwd": self.cwd,
            "shell": self.shell,
            "os": platform.system(),
        }


class HostProcessManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._launch_lock = asyncio.Lock()

    def get(self, owner: tuple[str, str], process_id: str) -> Job:
        job = self.jobs.get(process_id)
        if job is None or job.owner != owner:
            raise ToolValidationError(
                "Unknown host process for this session (handles do not survive backend restarts)"
            )
        return job

    async def launch(self, owner, *, command, cwd, env, login, wait_ms):
        async with self._launch_lock:
            # Reclaim completed handles only; never kill a running job to make room.
            if len(self.jobs) >= MAX_PROCESSES:
                for key, job in list(self.jobs.items()):
                    if job.done.is_set():
                        del self.jobs[key]
                        break
            if len(self.jobs) >= MAX_PROCESSES:
                raise ToolValidationError(
                    "Host process capacity reached; terminate an unused process first"
                )
            shell = default_shell()
            try:
                process = await asyncio.create_subprocess_exec(
                    *shell_argv(shell, command, login),
                    cwd=cwd,
                    env={**os.environ, **env},
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **({"start_new_session": True} if os.name != "nt" else {}),
                )
            except OSError as exc:
                raise ToolValidationError(f"Cannot launch host command: {exc}") from exc
            process_id = uuid4().hex
            job = Job(owner, process, command, cwd, shell)
            self.jobs[process_id] = job
            job.collector = asyncio.create_task(job.collect())
        return await self.poll(owner, process_id, wait_ms)

    async def poll(self, owner, process_id, wait_ms):
        job = self.get(owner, process_id)
        if wait_ms and not job.done.is_set():
            try:
                await asyncio.wait_for(job.done.wait(), wait_ms / 1000)
            except TimeoutError:
                pass  # This deadline only bounds the caller's wait, never execution.
        return job.result(process_id)

    async def terminate(self, owner, process_id, force=False):
        job = self.get(owner, process_id)
        if not job.done.is_set():
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(job.process.pid),
                    "/T",
                    *(["/F"] if force else []),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                try:
                    os.killpg(job.process.pid, signal.SIGKILL if force else signal.SIGTERM)
                except ProcessLookupError:
                    pass
        return await self.poll(owner, process_id, 1000)

    async def close(self):
        await asyncio.gather(
            *(self.terminate(job.owner, key, force=True) for key, job in list(self.jobs.items())),
            return_exceptions=True,
        )
        for job in self.jobs.values():
            if job.collector and not job.collector.done():
                job.collector.cancel()
        await asyncio.gather(
            *(job.collector for job in self.jobs.values() if job.collector),
            return_exceptions=True,
        )
        self.jobs.clear()
