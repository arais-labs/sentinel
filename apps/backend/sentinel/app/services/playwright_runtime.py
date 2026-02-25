from __future__ import annotations

import os
from typing import Any

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, "webdriver", { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, "languages", { get: () => ["en-US", "en"] });
Object.defineProperty(navigator, "plugins", { get: () => [1, 2, 3, 4, 5] });
"""


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return value


def _split_extra_args(raw: str) -> list[str]:
    parts = [part.strip() for part in raw.split(",")]
    return [part for part in parts if part]


def build_chromium_launch_options(*, headless: bool | None = None) -> dict[str, Any]:
    live_view_enabled = _env_bool("BROWSER_LIVE_VIEW_ENABLED", False)
    default_headless = not live_view_enabled
    resolved_headless = _env_bool("BROWSER_HEADLESS", default_headless) if headless is None else bool(headless)
    no_sandbox = _env_bool("BROWSER_NO_SANDBOX", True)
    slow_mo = max(_env_int("BROWSER_SLOW_MO_MS", 0), 0)

    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if no_sandbox:
        args.extend(["--no-sandbox", "--disable-setuid-sandbox"])
    if not resolved_headless:
        args.append("--start-maximized")

    args.extend(_split_extra_args(os.getenv("BROWSER_EXTRA_ARGS", "")))

    launch_options: dict[str, Any] = {
        "headless": resolved_headless,
        "args": args,
    }
    if slow_mo > 0:
        launch_options["slow_mo"] = slow_mo
    return launch_options


def build_browser_context_options() -> dict[str, Any]:
    viewport_width = max(_env_int("BROWSER_VIEWPORT_WIDTH", 1600), 320)
    viewport_height = max(_env_int("BROWSER_VIEWPORT_HEIGHT", 900), 320)

    options: dict[str, Any] = {
        "viewport": {"width": viewport_width, "height": viewport_height},
        "ignore_https_errors": _env_bool("BROWSER_IGNORE_HTTPS_ERRORS", False),
    }

    user_agent = os.getenv("BROWSER_USER_AGENT", "").strip()
    if user_agent:
        options["user_agent"] = user_agent

    locale = os.getenv("BROWSER_LOCALE", "").strip()
    if locale:
        options["locale"] = locale

    timezone_id = os.getenv("BROWSER_TIMEZONE_ID", "").strip()
    if timezone_id:
        options["timezone_id"] = timezone_id

    return options


def get_browser_user_data_dir() -> str | None:
    value = os.getenv("BROWSER_USER_DATA_DIR", "").strip()
    return value or None


async def apply_stealth_init_script(context: Any) -> None:
    if context is None or not hasattr(context, "add_init_script"):
        return
    try:
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
    except Exception:
        # Best effort only; some mocked contexts in tests may not support this.
        return
