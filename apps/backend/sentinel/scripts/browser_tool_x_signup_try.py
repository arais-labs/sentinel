from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from app.services.tools.builtin import build_default_registry
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.browser_pool import BrowserPool
from app.services.tools.executor import ToolExecutor


OUT_DIR = Path("/app/tmp")
_SID = "local"


def _short(value: Any, *, limit: int = 260) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


async def _exec(executor: ToolExecutor, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    result, _ = await executor.execute(name, payload, allow_high_risk=True)
    print(f"{name} {payload} -> {_short(result)}")
    return result


async def _try(executor: ToolExecutor, name: str, payload: dict[str, Any], label: str) -> bool:
    try:
        await _exec(executor, name, payload)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"{label} failed: {exc}")
        return False


def _write_png(image_base64: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(image_base64))


class _LocalBrowserPool(BrowserPool):
    """Thin pool wrapper that always returns the same local manager."""

    def __init__(self, manager: BrowserManager) -> None:
        super().__init__()
        self._local = manager

    async def get(self, session_id: Any = "") -> BrowserManager:  # type: ignore[override]
        return self._local


async def main() -> int:
    # headless=False uses xvfb/noVNC stack in container, matching live browser behavior.
    manager = BrowserManager(headless=False)
    pool = _LocalBrowserPool(manager)
    registry = build_default_registry(browser_pool=pool)
    executor = ToolExecutor(registry)

    before_path = OUT_DIR / "x_signup_before.png"
    after_path = OUT_DIR / "x_signup_after.png"

    try:
        print("=== X Signup Live Attempt ===")
        await _exec(executor, "browser_navigate", {"url": "https://x.com/i/flow/signup", "session_id": _SID})
        shot = await _exec(executor, "browser_screenshot", {"full_page": True, "session_id": _SID})
        _write_png(shot["image_base64"], before_path)
        print(f"saved screenshot: {before_path}")

        snap = await _exec(executor, "browser_snapshot", {"interactive_only": True, "session_id": _SID})
        print("interactive snapshot:")
        print(str(snap.get("snapshot", ""))[:2000])

        # Optional cookie consent variants.
        for selector in [
            "button: Accept all cookies",
            "button: Accept all",
            "button: Accept",
            "button: Allow all",
        ]:
            if await _try(executor, "browser_click", {"selector": selector, "session_id": _SID}, f"cookie click {selector}"):
                break

        # Try switching to email flow when phone is default.
        for selector in [
            "button: Use email instead",
            "link: Use email instead",
        ]:
            if await _try(executor, "browser_click", {"selector": selector, "session_id": _SID}, f"flow switch {selector}"):
                break

        # Fill known signup fields with test data.
        fill_attempts = [
            ("textbox: Name", "John Smith"),
            ("input[name='name']", "John Smith"),
            ("textbox: Email", "qa+sentinel-x-signup@example.com"),
            ("input[name='email']", "qa+sentinel-x-signup@example.com"),
            ("textbox: Phone or email", "qa+sentinel-x-signup@example.com"),
            ("textbox: Month", "January"),
            ("textbox: Day", "1"),
            ("textbox: Year", "1995"),
        ]
        for selector, text in fill_attempts:
            await _try(
                executor,
                "browser_type",
                {"selector": selector, "text": text, "session_id": _SID},
                f"type {selector}",
            )

        # Try moving the flow forward if available.
        for selector in [
            "button: Next",
            "button: Sign up",
            "button: Create your account",
        ]:
            await _try(executor, "browser_click", {"selector": selector, "session_id": _SID}, f"continue click {selector}")

        await _exec(executor, "browser_snapshot", {"interactive_only": True, "session_id": _SID})
        shot = await _exec(executor, "browser_screenshot", {"full_page": True, "session_id": _SID})
        _write_png(shot["image_base64"], after_path)
        print(f"saved screenshot: {after_path}")
        print("=== X Signup Live Attempt Complete ===")
        return 0
    finally:
        await manager.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
