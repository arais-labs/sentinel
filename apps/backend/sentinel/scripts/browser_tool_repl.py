from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from app.services.tools.builtin import build_default_registry
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.browser_pool import BrowserPool
from app.services.tools.executor import ToolExecutor


class _LocalBrowserPool(BrowserPool):
    """Thin pool wrapper that always returns the same local manager."""

    def __init__(self, manager: BrowserManager) -> None:
        super().__init__()
        self._local = manager

    async def get(self, session_id: Any = "") -> BrowserManager:  # type: ignore[override]
        return self._local


async def main() -> int:
    manager = BrowserManager(headless=False)
    pool = _LocalBrowserPool(manager)
    registry = build_default_registry(browser_pool=pool)
    executor = ToolExecutor(registry)

    print(json.dumps({"event": "ready"}), flush=True)
    loop = asyncio.get_running_loop()

    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                print(json.dumps({"ok": False, "error": f"invalid_json: {exc}"}), flush=True)
                continue

            if payload.get("cmd") == "quit":
                print(json.dumps({"ok": True, "event": "bye"}), flush=True)
                break

            tool = payload.get("tool")
            tool_input = payload.get("input", {})
            if not isinstance(tool, str) or not tool:
                print(json.dumps({"ok": False, "error": "missing tool"}), flush=True)
                continue
            if not isinstance(tool_input, dict):
                print(json.dumps({"ok": False, "error": "input must be object"}), flush=True)
                continue

            try:
                result, duration_ms = await executor.execute(
                    tool, tool_input, allow_high_risk=True
                )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "tool": tool,
                            "duration_ms": duration_ms,
                            "result": result,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    json.dumps({"ok": False, "tool": tool, "error": str(exc)}, ensure_ascii=False),
                    flush=True,
                )
    finally:
        await manager.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
