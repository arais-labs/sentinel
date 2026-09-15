"""Explicit imports of existing CLI logins for the standalone TUI."""

import asyncio
from pathlib import Path

from sentral.llm.antigravity_credentials import read_antigravity_credentials
from sentral.llm.claude_credentials import read_claude_access_token
from sentral.llm.codex_credentials import extract_codex_access_token


async def import_login(provider):
    if provider == "openai":
        path = Path.home() / ".codex" / "auth.json"
        try:
            raw = await asyncio.to_thread(path.read_text)
        except FileNotFoundError:
            raise ValueError("Sign in with Codex CLI, then try again.") from None
        return extract_codex_access_token(raw)
    if provider == "anthropic":
        token = await read_claude_access_token()
        if not token:
            raise ValueError("Sign in with Claude Code, then try again.")
        return token
    credentials = await read_antigravity_credentials()
    if credentials is None:
        raise ValueError("Sign in with Antigravity CLI, then try again.")
    return credentials.as_json()
