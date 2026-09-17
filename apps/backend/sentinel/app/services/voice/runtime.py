"""Owns the optional speech dependency, its files, and its subprocesses in one place."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

from .gateway import VoiceUnavailable

PACKAGE = "faster-whisper==1.2.1"
SPEECH_PACKAGE = "kokoro-onnx==0.6.1"
INSTALL_VERSION = "faster-whisper-1.2.1-base-kokoro-0.6.1-int8-v2"
WORKER = Path(__file__).with_name("worker.py")


class VoiceRuntime:
    def __init__(self, storage: Path):
        self.root = storage.resolve() / "voice"
        self.python = (
            self.root / "runtime" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        self.process: asyncio.subprocess.Process | None = None
        self.installer: asyncio.subprocess.Process | None = None
        self.install_task: asyncio.Task | None = None
        self.reaper: asyncio.Task | None = None
        self.leases: dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.phase = "idle"
        self.error = ""
        self.closed = False

    @property
    def installed(self) -> bool:
        marker = self.root / "installed.json"
        return (
            marker.is_file()
            and self.python.is_file()
            and (self.root / "model/model.bin").is_file()
            and (self.root / "speech/kokoro-v1.0.int8.onnx").is_file()
            and (self.root / "speech/voices-v1.0.bin").is_file()
            and marker.read_text() == INSTALL_VERSION
        )

    def status(self) -> dict:
        return {
            "installed": self.installed,
            "running": bool(self.process and self.process.returncode is None),
            "phase": self.phase,
            "error": self.error,
            "path": str(self.root),
            "installing": bool(self.install_task and not self.install_task.done()),
        }

    def environment(self, *, offline: bool) -> dict[str, str]:
        # No provider keys, PYTHONPATH, proxy settings or inherited package configuration.
        env = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "LANG", "LC_ALL")
            if key in os.environ
        }
        env.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HOME": str(self.root / "cache"),
                "XDG_CACHE_HOME": str(self.root / "cache"),
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
                "TMPDIR": str(self.root / "tmp"),
                "TMP": str(self.root / "tmp"),
                "TEMP": str(self.root / "tmp"),
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        if offline:
            env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        return env

    async def install(self) -> dict:
        if self.closed:
            raise VoiceUnavailable("Voice is shutting down.")
        if self.root.is_symlink():
            raise VoiceUnavailable("The voice directory must not be a symlink.")
        if self.installed or self.install_task and not self.install_task.done():
            return self.status()
        self.phase, self.error = "Preparing speech runtime", ""
        self.install_task = asyncio.create_task(self._install())
        return self.status()

    async def _install_command(self, *args: str):
        # Drain output to keep pipes clear, but do not retain model or audio data in logs.
        self.installer = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.root,
            env=self.environment(offline=False),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(self.installer.communicate(), 900)
            if self.installer.returncode:
                raise VoiceUnavailable(
                    f"{self.phase} failed. Check network access and retry. "
                    + stderr.decode(errors="replace")[-700:]
                )
        finally:
            await self._terminate(self.installer)
            self.installer = None

    async def _install(self):
        async with self.lock:
            try:
                await self._terminate(self.process)
                self.process = None
                self.leases.clear()
                (self.root / "tmp").mkdir(parents=True, exist_ok=True)
                self.phase = "Preparing isolated Python environment"
                await self._install_command(
                    sys.executable, "-I", "-m", "venv", str(self.root / "runtime")
                )
                self.phase = "Downloading speech dependencies"
                await self._install_command(
                    str(self.python),
                    "-I",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-cache-dir",
                    "--only-binary=:all:",
                    PACKAGE,
                    SPEECH_PACKAGE,
                )
                self.phase = "Downloading and verifying Whisper + Kokoro English"
                await self._install_command(
                    str(self.python), "-I", str(WORKER), "install", str(self.root)
                )
                (self.root / "installed.json").write_text(INSTALL_VERSION)
                self.phase = "ready"
            except asyncio.CancelledError:
                self.phase = "idle"
                raise
            except Exception as exc:
                self.error, self.phase = str(exc), "failed"

    @staticmethod
    async def _terminate(process):
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def _start(self):
        if self.process and self.process.returncode is None:
            return
        if self.closed or not self.installed:
            raise VoiceUnavailable("Install the local speech engine first.")
        self.process = await asyncio.create_subprocess_exec(
            str(self.python),
            "-I",
            "-u",
            str(WORKER),
            "serve",
            str(self.root),
            cwd=self.root,
            env=self.environment(offline=True),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=8 * 1024 * 1024,
        )
        try:
            line = await asyncio.wait_for(self.process.stdout.readline(), 45)
            if not json.loads(line).get("ready"):
                raise ValueError("Worker did not become ready")
        except asyncio.CancelledError:
            await self._terminate(self.process)
            self.process = None
            raise
        except Exception:
            await self._terminate(self.process)
            self.process = None
            raise VoiceUnavailable(
                "The speech engine could not start. Remove and reinstall local voice data."
            )

    async def acquire(self) -> str:
        async with self.lock:
            await self._start()
            lease = str(uuid4())
            self.leases[lease] = time.monotonic() + 60
            if self.reaper is None or self.reaper.done():
                self.reaper = asyncio.create_task(self._expire())
            return lease

    def renew(self, lease: str):
        if lease not in self.leases:
            raise VoiceUnavailable("Voice connection expired. Reconnect to continue.")
        self.leases[lease] = time.monotonic() + 60

    async def release(self, lease: str):
        async with self.lock:
            self.leases.pop(lease, None)
            if not self.leases:
                await self._terminate(self.process)
                self.process = None

    async def _expire(self):
        while self.leases:
            await asyncio.sleep(15)
            for lease, deadline in list(self.leases.items()):
                if deadline < time.monotonic():
                    await self.release(lease)

    async def transcribe(self, audio: bytes, lease: str) -> str:
        result = await self._request({"audio": base64.b64encode(audio).decode()}, lease)
        return str(result["text"])

    async def synthesize(self, text: str, lease: str, speed: float = 1.05) -> dict:
        if not text.strip() or len(text) > 400:
            raise ValueError("Speech chunks must contain 1–400 characters.")
        if not 0.75 <= speed <= 2.0:
            raise ValueError("Speech speed must be between 0.75 and 2.0.")
        result = await self._request({"text": text, "speed": speed}, lease)
        return {"audio": result["audio"], "mime_type": "audio/wav"}

    async def _request(self, payload: dict, lease: str) -> dict:
        async with self.lock:
            self.renew(lease)
            await self._start()
            try:
                self.process.stdin.write(json.dumps(payload).encode() + b"\n")
                await self.process.stdin.drain()
                result = json.loads(await asyncio.wait_for(self.process.stdout.readline(), 40))
                if result.get("error"):
                    raise VoiceUnavailable(result["error"])
                return result
            except BaseException:
                await self._terminate(self.process)
                self.process = None
                raise

    async def stop(self):
        if self.install_task and not self.install_task.done():
            self.install_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.install_task
        if self.reaper:
            self.reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reaper
        async with self.lock:
            self.leases.clear()
            await self._terminate(self.process)
            self.process = None

    async def remove(self):
        await self.stop()
        async with self.lock:
            if self.root.name != "voice" or self.root.is_symlink():
                raise VoiceUnavailable("Refusing to remove an unexpected voice directory.")
            if self.root.exists():
                await asyncio.to_thread(shutil.rmtree, self.root)
            self.phase, self.error = "idle", ""

    async def close(self):
        self.closed = True
        await self.stop()
