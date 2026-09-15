"""Read the user's Antigravity consumer OAuth login without modifying its store."""

from __future__ import annotations

import asyncio
import base64
import sys

from sentral.llm.providers.gemini_oauth import GeminiOAuthCredentials


async def read_antigravity_credentials() -> GeminiOAuthCredentials | None:
    if sys.platform != "darwin":
        raise ValueError(
            "Automatic Antigravity import currently supports macOS Keychain. "
            "Paste an exported Antigravity OAuth credential bundle on other platforms."
        )
    process = await asyncio.create_subprocess_exec(
        "security",
        "find-generic-password",
        "-s",
        "gemini",
        "-a",
        "antigravity",
        "-w",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode == 44:  # errSecItemNotFound
        return None
    if process.returncode != 0:
        raise ValueError("Could not read the Antigravity login from macOS Keychain.")
    raw = output.decode().strip()
    if raw.startswith("go-keyring-base64:"):
        raw = base64.b64decode(raw.split(":", 1)[1], validate=True).decode()
    return GeminiOAuthCredentials.parse_input(raw)
