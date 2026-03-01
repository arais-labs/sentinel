from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from app.services.tools.builtin import build_default_registry
from app.services.tools.browser_tool import BrowserManager
from app.services.tools.executor import ToolExecutor


def _short(value: Any, *, limit: int = 260) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}..."


@dataclass(slots=True)
class RunResult:
    failures: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.failures.append(message)

    def ok(self) -> bool:
        return not self.failures


async def _execute(
    executor: ToolExecutor,
    name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    output = await executor.execute(name, payload, allow_high_risk=True)
    result = output[0] if isinstance(output, tuple) else output
    print(f"{name} {payload} -> {_short(result)}")
    return result


async def _execute_safe(
    executor: ToolExecutor,
    name: str,
    payload: dict[str, Any],
    status: RunResult,
    failure_label: str,
) -> dict[str, Any] | None:
    try:
        return await _execute(executor, name, payload)
    except Exception as exc:  # noqa: BLE001
        status.fail(f"{failure_label}: {exc}")
        print(f"{name} {payload} -> ERROR: {exc}")
        return None


async def run_live_checks() -> RunResult:
    manager = BrowserManager(headless=True)
    registry = build_default_registry(browser_manager=manager)
    executor = ToolExecutor(registry)
    status = RunResult()

    try:
        print("\n=== Scenario 1: Example.com Semantic Link Click ===")
        nav = await _execute(
            executor, "browser_navigate", {"url": "https://example.com"}
        )
        start_url = nav.get("url", "")
        if "example.com" not in nav.get("url", ""):
            status.fail("scenario1: unexpected url after navigate")

        snap = await _execute(executor, "browser_snapshot", {"interactive_only": True})
        if not snap.get("snapshot"):
            status.fail("scenario1: interactive snapshot empty")

        snapshot_text = str(snap.get("snapshot") or "")
        link_selector = (
            "link: Learn more" if "link: Learn more" in snapshot_text else "a"
        )
        click_res = await _execute_safe(
            executor,
            "browser_click",
            {"selector": link_selector},
            status,
            "scenario1: unable to click primary example.com link",
        )
        heading = await _execute_safe(
            executor,
            "browser_get_text",
            {"selector": "h1"},
            status,
            "scenario1: could not read destination heading",
        )
        navigated = False
        if isinstance(click_res, dict):
            current_url = click_res.get("url", "")
            if current_url and current_url != start_url:
                navigated = True
            if "iana.org" in current_url:
                navigated = True
        if isinstance(heading, dict) and "IANA" in (heading.get("text") or ""):
            navigated = True
        if not navigated:
            status.fail(
                "scenario1: semantic link click did not produce observable navigation"
            )

        print("\n=== Scenario 2: Selenium Web Form (Inputs + Dropdown) ===")
        await _execute(
            executor,
            "browser_navigate",
            {"url": "https://www.selenium.dev/selenium/web/web-form.html"},
        )
        snap = await _execute(executor, "browser_snapshot", {"interactive_only": True})
        if not snap.get("snapshot"):
            status.fail("scenario2: interactive snapshot empty")

        await _execute_safe(
            executor,
            "browser_type",
            {"selector": "input[name='my-text']", "text": "sentinel-e2e"},
            status,
            "scenario2: failed typing plain text input",
        )
        await _execute_safe(
            executor,
            "browser_type",
            {
                "selector": "textarea[name='my-textarea']",
                "text": "hello from sentinel browser tools",
            },
            status,
            "scenario2: failed typing textarea",
        )
        await _execute_safe(
            executor,
            "browser_click",
            {"selector": "input[name='my-check']"},
            status,
            "scenario2: failed checkbox click",
        )
        radio_selector = "input[value='rd1']"
        snapshot_text = str(snap.get("snapshot") or "")
        for line in snapshot_text.splitlines():
            line = line.strip()
            if line.lower().startswith("radio:"):
                radio_selector = line
                break
        await _execute_safe(
            executor,
            "browser_click",
            {"selector": radio_selector},
            status,
            "scenario2: failed radio click",
        )
        await _execute_safe(
            executor,
            "browser_click",
            {"selector": "select[name='my-select']"},
            status,
            "scenario2: failed dropdown focus click",
        )
        await _execute_safe(
            executor,
            "browser_click",
            {"selector": "option[value='2']"},
            status,
            "scenario2: failed dropdown option click",
        )

        selected = await _execute_safe(
            executor,
            "browser_get_text",
            {"selector": "select[name='my-select'] option:checked"},
            status,
            "scenario2: failed reading dropdown selected option",
        )
        if isinstance(selected, dict) and "Two" not in (selected.get("text") or ""):
            status.fail("scenario2: dropdown selection not confirmed as 'Two'")

        textarea_text = await _execute_safe(
            executor,
            "browser_get_text",
            {"selector": "textarea[name='my-textarea']"},
            status,
            "scenario2: failed reading textarea",
        )
        if isinstance(textarea_text, dict) and (
            "hello from sentinel browser tools" not in (textarea_text.get("text") or "")
        ):
            status.fail("scenario2: textarea value not readable via browser_get_text")

        await _execute_safe(
            executor,
            "browser_screenshot",
            {"full_page": True},
            status,
            "scenario2: failed taking full-page screenshot",
        )

        print("\n=== Scenario 3: GitHub Signup Semantic Textbox Selectors ===")
        await _execute_safe(
            executor,
            "browser_navigate",
            {"url": "https://github.com/signup"},
            status,
            "scenario3: github navigate failed",
        )
        await _execute_safe(
            executor,
            "browser_snapshot",
            {"interactive_only": True},
            status,
            "scenario3: github interactive snapshot failed",
        )
        try:
            await _execute(executor, "browser_click", {"selector": "button: Accept"})
        except Exception as exc:  # noqa: BLE001
            print(f"cookie banner accept skipped: {exc}")
        await _execute_safe(
            executor,
            "browser_type",
            {"selector": "textbox: Email", "text": "qa+sentinel-e2e@example.com"},
            status,
            "scenario3: semantic email fill failed",
        )
        await _execute_safe(
            executor,
            "browser_type",
            {"selector": "textbox: Password", "text": "S3ntinel_Test_2026!"},
            status,
            "scenario3: semantic password fill failed",
        )
        await _execute_safe(
            executor,
            "browser_type",
            {"selector": "textbox: Username", "text": "sentinel-qa-e2e-2026"},
            status,
            "scenario3: semantic username fill failed",
        )
        await _execute_safe(
            executor,
            "browser_screenshot",
            {"full_page": True},
            status,
            "scenario3: github screenshot failed",
        )
    finally:
        await manager.close()

    return status


def main() -> int:
    result = asyncio.run(run_live_checks())
    print("\n=== Browser Tool Live Check Result ===")
    if result.ok():
        print("PASS: all curated live scenarios completed")
        return 0
    print("FAIL:")
    for item in result.failures:
        print(f" - {item}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
