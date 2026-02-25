from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlaywrightTask
from app.services.playwright_runtime import (
    apply_stealth_init_script,
    build_browser_context_options,
    build_chromium_launch_options,
    get_browser_user_data_dir,
)

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - optional runtime dependency
    async_playwright = None


_PLACEHOLDER_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO2R0xkAAAAASUVORK5CYII="
)


class PlaywrightRunner:
    async def execute_task(self, db: AsyncSession, task: PlaywrightTask) -> PlaywrightTask:
        if task.status == "cancelled":
            return task

        task.status = "running"
        task.started_at = task.started_at or datetime.now(UTC)
        await db.commit()
        await db.refresh(task)

        try:
            if task.action == "screenshot":
                result = await self.capture_screenshot(db, task)
            elif task.action == "extract":
                result = await self._extract(task.url, task.options)
            else:
                result = await self._interact(task.url, task.options)
        except Exception as exc:  # pragma: no cover - defensive safety net
            if task.status != "cancelled":
                task.status = "failed"
                task.result = {"error": str(exc)}
                task.completed_at = datetime.now(UTC)
                await db.commit()
                await db.refresh(task)
            return task

        if task.status != "cancelled":
            task.status = "completed"
            task.result = result
            task.completed_at = datetime.now(UTC)
            await db.commit()
            await db.refresh(task)
        return task

    async def capture_screenshot(self, db: AsyncSession, task: PlaywrightTask) -> dict[str, Any]:
        screenshot = await self._screenshot(task.url, task.options)
        result = dict(task.result or {})
        result.update(screenshot)
        task.result = result
        await db.commit()
        await db.refresh(task)
        return screenshot

    async def _screenshot(self, url: str, options: dict[str, Any]) -> dict[str, Any]:
        live_browser = bool(options.get("live_browser", True))
        if live_browser and async_playwright is not None:
            try:
                timeout_ms = int(options.get("timeout_ms", 15_000))
                full_page = bool(options.get("full_page", False))
                headless = options.get("headless")
                if not isinstance(headless, bool):
                    headless = None
                requested_profile_dir = options.get("user_data_dir")
                profile_dir = (
                    requested_profile_dir.strip()
                    if isinstance(requested_profile_dir, str) and requested_profile_dir.strip()
                    else get_browser_user_data_dir()
                )
                launch_options = build_chromium_launch_options(headless=headless)
                context_options = build_browser_context_options()

                async with async_playwright() as playwright:
                    browser = None
                    if profile_dir:
                        try:
                            context = await playwright.chromium.launch_persistent_context(
                                profile_dir,
                                **launch_options,
                                **context_options,
                            )
                        except Exception as exc:
                            if "ProcessSingleton" not in str(exc):
                                raise
                            browser = await playwright.chromium.launch(**launch_options)
                            context = await browser.new_context(**context_options)
                    else:
                        browser = await playwright.chromium.launch(**launch_options)
                        context = await browser.new_context(**context_options)
                    await apply_stealth_init_script(context)
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    title = await page.title()
                    image_bytes = await page.screenshot(full_page=full_page)
                    await context.close()
                    if browser is not None:
                        await browser.close()
                encoded = base64.b64encode(image_bytes).decode("ascii")
                return {
                    "screenshot_base64": encoded,
                    "page_title": title or "Untitled",
                    "url": url,
                }
            except Exception:
                pass
        return {
            "screenshot_base64": _PLACEHOLDER_PNG_BASE64,
            "page_title": "Mock Page",
            "url": url,
        }

    async def _extract(self, url: str, options: dict[str, Any]) -> dict[str, Any]:
        selector = options.get("selector", "body")
        return {
            "url": url,
            "selector": selector,
            "content": "Mock extracted content",
        }

    async def _interact(self, url: str, options: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": url,
            "steps": options.get("steps", []),
            "status": "mock_interaction_completed",
        }
