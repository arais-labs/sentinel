from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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

_MAX_BROWSER_CONTENT_CHARS = 10_000
_TRUNCATION_NOTICE = "\n\n[...TRUNCATED - content too large. Use a specific CSS selector or interactive_only=true to get less data.]"

_INTERACTIVE_ROLES = frozenset({
    "button", "link", "textbox", "checkbox", "radio", "combobox",
    "listbox", "menuitem", "menuitemcheckbox", "menuitemradio",
    "slider", "tab", "searchbox", "spinbutton", "switch", "option",
    "treeitem", "gridcell",
})


def _truncate_content(text: str, max_chars: int = _MAX_BROWSER_CONTENT_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + _TRUNCATION_NOTICE, True


def _filter_snapshot(
    node: dict[str, Any],
    *,
    interactive_only: bool,
    max_depth: int | None,
    _depth: int = 0,
) -> dict[str, Any] | None:
    if max_depth is not None and _depth > max_depth:
        return None
    role = (node.get("role") or "").lower()
    children_raw = node.get("children") or []
    filtered_children = []
    for child in children_raw:
        if isinstance(child, dict):
            fc = _filter_snapshot(child, interactive_only=interactive_only, max_depth=max_depth, _depth=_depth + 1)
            if fc is not None:
                filtered_children.append(fc)
    if interactive_only:
        if role not in _INTERACTIVE_ROLES and not filtered_children:
            return None
    result = {k: v for k, v in node.items() if k != "children"}
    if filtered_children:
        result["children"] = filtered_children
    return result


class BrowserManager:
    def __init__(
        self,
        *,
        timeout_ms: int = 15_000,
        headless: bool | None = None,
        user_data_dir: str | None = None,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._user_data_dir = user_data_dir or get_browser_user_data_dir()
        self._playwright_context: Any = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    async def navigate(self, url: str) -> dict[str, Any]:
        normalized = self._validate_url(url)
        page = await self._ensure_page()
        await page.goto(normalized, wait_until="domcontentloaded", timeout=self._timeout_ms)
        title = await page.title()
        current_url = getattr(page, "url", normalized)
        return {"url": current_url, "title": title or ""}

    async def screenshot(self, *, full_page: bool = True) -> dict[str, Any]:
        page = await self._ensure_page()
        image = await page.screenshot(full_page=bool(full_page))
        encoded = base64.b64encode(image).decode("ascii")
        return {"image_base64": encoded}

    async def click(self, selector: str) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        page = await self._ensure_page()
        await page.click(selector.strip(), timeout=self._timeout_ms)
        return {"clicked": True, "selector": selector.strip()}

    async def type_text(self, selector: str, text: str) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        page = await self._ensure_page()
        await page.fill(selector.strip(), text, timeout=self._timeout_ms)
        return {"typed": True, "selector": selector.strip(), "characters": len(text)}

    async def press_key(self, key: str) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")
        page = await self._ensure_page()
        await page.keyboard.press(key.strip())
        return {"pressed": True, "key": key.strip()}

    async def get_text(self, selector: str | None = None) -> dict[str, Any]:
        page = await self._ensure_page()
        if selector is None:
            # Try Playwright's AI-optimized snapshot first (clean accessibility tree, no CSS/JS noise)
            text = await self._snapshot_for_ai(page)
            if text is None:
                text = await page.inner_text("body", timeout=self._timeout_ms) or ""
            text, truncated = _truncate_content(text)
            result: dict[str, Any] = {"selector": None, "text": text}
            if truncated:
                result["truncated"] = True
            return result
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be null or non-empty string")
        sel = selector.strip()
        # Use innerText via JS eval (respects display:none, excludes hidden content)
        try:
            element = await page.query_selector(sel)
            if element is not None:
                text = await page.evaluate("el => el.innerText || ''", element) or ""
            else:
                text = ""
        except Exception:
            text = await page.text_content(sel, timeout=self._timeout_ms) or ""
        text, truncated = _truncate_content(text)
        result = {"selector": sel, "text": text}
        if truncated:
            result["truncated"] = True
        return result

    async def get_snapshot(
        self,
        *,
        interactive_only: bool = False,
        max_depth: int | None = None,
    ) -> dict[str, Any]:
        import json as _json
        import logging

        logger = logging.getLogger(__name__)
        page = await self._ensure_page()
        methods_tried: list[str] = []

        # ── Method 1: ariaSnapshot (best output — structured ARIA tree with URLs) ──
        # Skip for interactive_only — ariaSnapshot returns full tree; CDP filters better.
        if not interactive_only:
            try:
                locator = page.locator("body")
                aria_fn = getattr(locator, "aria_snapshot", None)
                if aria_fn is not None:
                    methods_tried.append("ariaSnapshot")
                    raw_aria: str = await aria_fn()
                    if raw_aria and len(raw_aria.strip()) > 10:
                        raw, truncated = _truncate_content(raw_aria)
                        result: dict[str, Any] = {"snapshot": raw, "method": "ariaSnapshot"}
                        if truncated:
                            result["truncated"] = True
                        return result
            except Exception as exc:
                logger.debug("ariaSnapshot failed: %s", exc)

        # ── Method 2: _snapshotForAI (Playwright private API) ──
        try:
            snapshot_fn = getattr(page, "_snapshotForAI", None)
            if snapshot_fn is not None:
                methods_tried.append("snapshotForAI")
                ai_result = await snapshot_fn(timeout=min(self._timeout_ms, 10_000))
                text = None
                if isinstance(ai_result, str):
                    text = ai_result
                elif isinstance(ai_result, dict):
                    text = ai_result.get("snapshot") or ai_result.get("text") or str(ai_result)
                elif ai_result:
                    text = str(ai_result)
                if text and len(text.strip()) > 10:
                    raw, truncated = _truncate_content(text)
                    result = {"snapshot": raw, "method": "snapshotForAI"}
                    if truncated:
                        result["truncated"] = True
                    return result
        except Exception as exc:
            logger.debug("snapshotForAI failed: %s", exc)

        # ── Method 3: CDP Accessibility.getFullAXTree (works on any Playwright version) ──
        try:
            methods_tried.append("cdpAXTree")
            client = await page.context.new_cdp_session(page)
            await client.send("Accessibility.enable")
            tree = await client.send("Accessibility.getFullAXTree")
            nodes = tree.get("nodes", [])
            if nodes:
                lines = []
                for n in nodes:
                    role = (n.get("role") or {}).get("value", "")
                    name = (n.get("name") or {}).get("value", "")
                    if not role or role in ("none", "generic", "InlineTextBox", "LineBreak"):
                        continue
                    if interactive_only and role.lower() not in _INTERACTIVE_ROLES:
                        continue
                    if not name and role == "StaticText":
                        continue
                    entry = f"{role}: {name}" if name else role
                    lines.append(entry)
                if lines:
                    text = "\n".join(lines)
                    raw, truncated = _truncate_content(text)
                    result = {"snapshot": raw, "method": "cdpAXTree", "nodes": len(lines)}
                    if interactive_only:
                        result["interactive_only"] = True
                    if truncated:
                        result["truncated"] = True
                    return result
        except Exception as exc:
            logger.debug("CDP AXTree failed: %s", exc)

        # ── Method 4: Legacy page.accessibility.snapshot() ──
        try:
            accessibility = getattr(page, "accessibility", None)
            if accessibility is not None:
                methods_tried.append("accessibilitySnapshot")
                snapshot = await accessibility.snapshot()
                if snapshot:
                    if interactive_only or max_depth is not None:
                        snapshot = _filter_snapshot(snapshot, interactive_only=interactive_only, max_depth=max_depth)
                    raw = _json.dumps(snapshot, ensure_ascii=False)
                    raw, truncated = _truncate_content(raw)
                    result = {"snapshot": raw, "method": "accessibilitySnapshot"}
                    if interactive_only:
                        result["interactive_only"] = True
                    if truncated:
                        result["truncated"] = True
                    return result
        except Exception as exc:
            logger.debug("accessibility.snapshot failed: %s", exc)

        # ── All methods failed ──
        return {
            "snapshot": "",
            "error": (
                "All snapshot methods failed. "
                "Use browser_get_text instead to read the page content, "
                "or browser_screenshot to see the page visually."
            ),
            "methods_tried": methods_tried,
        }

    async def reset(self) -> dict[str, Any]:
        await self.close()
        stale_lock_cleared = self._cleanup_stale_profile_lock()
        page = await self._ensure_page()
        try:
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=self._timeout_ms)
        except Exception:
            pass
        current_url = getattr(page, "url", "about:blank")
        return {
            "reset": True,
            "url": current_url,
            "profile_dir": self._user_data_dir,
            "stale_lock_cleared": stale_lock_cleared,
        }

    async def close(self) -> None:
        page = self._page
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._playwright_context = None

        if page is not None and hasattr(page, "close"):
            try:
                await page.close()
            except Exception:
                pass
        if context is not None and hasattr(context, "close"):
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None and hasattr(browser, "close"):
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None and hasattr(playwright, "stop"):
            try:
                await playwright.stop()
            except Exception:
                pass

    async def _ensure_page(self):
        if self._page is not None:
            return self._page
        if async_playwright is None:
            raise RuntimeError("Playwright runtime is not available")

        launch_options = build_chromium_launch_options(headless=self._headless)
        context_options = build_browser_context_options()

        self._playwright_context = async_playwright()
        self._playwright = await self._playwright_context.start()
        if self._user_data_dir:
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    self._user_data_dir,
                    **launch_options,
                    **context_options,
                )
                await apply_stealth_init_script(self._context)
                self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
                return self._page
            except Exception as exc:
                # A locked profile should not make browser tools unusable.
                if "ProcessSingleton" not in str(exc):
                    raise

        self._browser = await self._playwright.chromium.launch(**launch_options)
        self._context = await self._browser.new_context(**context_options)
        await apply_stealth_init_script(self._context)
        self._page = await self._context.new_page()
        return self._page

    def _validate_url(self, url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be a valid http/https URL")
        return url.strip()

    def _cleanup_stale_profile_lock(self) -> bool:
        if not self._user_data_dir:
            return False

        profile_dir = Path(self._user_data_dir)
        lock_path = profile_dir / "SingletonLock"
        if not lock_path.exists() or not lock_path.is_symlink():
            return False

        try:
            target = lock_path.readlink().name
            lock_pid = target.rsplit("-", 1)[-1]
        except Exception:
            return False

        if not lock_pid.isdigit():
            return False
        if Path(f"/proc/{lock_pid}").exists():
            return False

        cleared = False
        for file_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            path = profile_dir / file_name
            if not path.exists() and not path.is_symlink():
                continue
            try:
                path.unlink()
                cleared = True
            except Exception:
                continue
        return cleared
