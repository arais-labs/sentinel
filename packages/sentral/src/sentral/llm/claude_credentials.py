"""Renew subscription credentials through the user's installed Claude CLI."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

_refresh_lock = asyncio.Lock()


async def read_claude_access_token() -> str | None:
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    path = directory / ".credentials.json"
    raw = None
    # Claude CLI uses Keychain on macOS; an old credentials file may still exist.
    if sys.platform == "darwin" and not os.environ.get("CLAUDE_CONFIG_DIR"):
        process = await asyncio.create_subprocess_exec(
            "security",
            "find-generic-password",
            "-s",
            "Claude Code-credentials",
            "-w",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 2)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode == 0:
            raw = output.decode()
    if raw is None:
        try:
            raw = await asyncio.to_thread(path.read_text)
        except FileNotFoundError:
            return None
    try:
        token = json.loads(raw).get("claudeAiOauth", {}).get("accessToken")
    except (ValueError, AttributeError):
        return None
    return token if isinstance(token, str) and token.strip() else None


async def run_claude_refresh_probe() -> None:
    executable = shutil.which("claude")
    if executable is None:
        raise RuntimeError("Claude login expired. Install Claude CLI or reconnect your account.")
    environment = dict(os.environ)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="sentinel-claude-probe-") as directory:
        process = await asyncio.create_subprocess_exec(
            executable,
            "-p",
            "Sentinel Prob",
            cwd=directory,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        try:
            try:
                await asyncio.wait_for(process.wait(), 1)
            except asyncio.TimeoutError:
                pass
        finally:
            if process.returncode is None:
                try:
                    if os.name == "nt":
                        process.terminate()
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 0.3)
                except asyncio.TimeoutError:
                    try:
                        if os.name == "nt":
                            process.kill()
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()


async def renew_claude_access_token(previous: str) -> str:
    async with _refresh_lock:
        current = await read_claude_access_token()
        if current and current != previous:
            return current
        await run_claude_refresh_probe()
        current = await read_claude_access_token()
        if not current or current == previous:
            raise RuntimeError(
                "Claude login could not be renewed within the probe window. Reconnect Claude and retry."
            )
        return current
