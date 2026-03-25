from __future__ import annotations

import asyncio
import base64
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.runtime.playwright_runtime import (
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

_INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "slider",
        "tab",
        "searchbox",
        "spinbutton",
        "switch",
        "option",
        "treeitem",
        "gridcell",
    }
)

_SEMANTIC_ROLE_ALIASES: dict[str, str] = {
    "button": "button",
    "link": "link",
    "textbox": "textbox",
    "searchbox": "searchbox",
    "checkbox": "checkbox",
    "radio": "radio",
    "combobox": "combobox",
    "listbox": "listbox",
    "menuitem": "menuitem",
    "slider": "slider",
    "tab": "tab",
    "spinbutton": "spinbutton",
    "switch": "switch",
    "option": "option",
}

_ELEMENT_STATE_JS = """
el => {
  const tag = (el.tagName || '').toLowerCase();
  const base = {
    tag,
    text: String(el.innerText || el.textContent || ''),
    disabled: Boolean(el.disabled),
  };
  if (tag === 'input') {
    return {
      ...base,
      type: String(el.type || ''),
      value: String(el.value ?? ''),
      checked: Boolean(el.checked),
    };
  }
  if (tag === 'textarea') {
    return {
      ...base,
      value: String(el.value ?? ''),
    };
  }
  if (tag === 'select') {
    const selectedOptions = Array.from(el.selectedOptions || []);
    return {
      ...base,
      value: String(el.value ?? ''),
      selected_values: selectedOptions.map(opt => String(opt.value ?? '')),
      selected_texts: selectedOptions.map(opt => String((opt.textContent || '').trim())),
    };
  }
  return base;
}
"""


def _truncate_content(
    text: str, max_chars: int = _MAX_BROWSER_CONTENT_CHARS
) -> tuple[str, bool]:
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
            fc = _filter_snapshot(
                child,
                interactive_only=interactive_only,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            if fc is not None:
                filtered_children.append(fc)
    if interactive_only:
        if role not in _INTERACTIVE_ROLES and not filtered_children:
            return None
    result = {k: v for k, v in node.items() if k != "children"}
    if filtered_children:
        result["children"] = filtered_children
    return result


class _ReentrantAsyncLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner_task: asyncio.Task | None = None
        self._depth = 0

    async def acquire(self) -> None:
        current = asyncio.current_task()
        if current is None:
            await self._lock.acquire()
            self._depth = 1
            return
        if self._owner_task is current:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner_task = current
        self._depth = 1

    def release(self) -> None:
        current = asyncio.current_task()
        if self._owner_task is not None and self._owner_task is not current:
            raise RuntimeError("Reentrant lock release by non-owner task")
        if self._depth <= 0:
            raise RuntimeError("Reentrant lock released too many times")
        self._depth -= 1
        if self._depth == 0:
            self._owner_task = None
            self._lock.release()


class BrowserManager:
    def __init__(
        self,
        *,
        timeout_ms: int = 3_000,
        headless: bool | None = None,
        user_data_dir: str | None = None,
        cdp_endpoint: str | None = None,
    ) -> None:
        self._timeout_ms = timeout_ms
        self._headless = headless
        self._user_data_dir = user_data_dir or get_browser_user_data_dir()
        self._cdp_endpoint = cdp_endpoint
        self._playwright_context: Any = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._tab_ids_by_page: dict[int, str] = {}
        self._active_tab_id: str | None = None
        self._tab_counter: int = 0
        self._tab_action_locks: dict[str, _ReentrantAsyncLock] = {}
        self._locks_guard = asyncio.Lock()
        self._tab_admin_lock = _ReentrantAsyncLock()
        self._console_logs: dict[int, list[dict]] = {}
        self._network_logs: dict[int, list[dict]] = {}
        self._active_routes: dict[int, list[str]] = {}

    def _resolve_timeout(self, timeout_ms: int | None) -> int:
        timeout = self._timeout_ms if timeout_ms is None else timeout_ms
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        return timeout

    def _normalize_tab_id(self, tab_id: str | None) -> str | None:
        if tab_id is None:
            return None
        if not isinstance(tab_id, str) or not tab_id.strip():
            raise ValueError("tab_id must be a non-empty string")
        return tab_id.strip()

    async def _resolve_action_page(self, tab_id: str | None) -> tuple[Any, str, bool]:
        normalized_tab_id = self._normalize_tab_id(tab_id)
        if normalized_tab_id is not None:
            page = await self._page_for_tab_id(normalized_tab_id)
            self._register_page(page, make_active=False)
            return page, normalized_tab_id, False
        page = await self._ensure_page()
        resolved_tab_id = self._register_page(page, make_active=True)
        return page, resolved_tab_id, True

    async def _tab_lock_for_id(self, tab_id: str) -> _ReentrantAsyncLock:
        async with self._locks_guard:
            lock = self._tab_action_locks.get(tab_id)
            if lock is None:
                lock = _ReentrantAsyncLock()
                self._tab_action_locks[tab_id] = lock
            return lock

    @asynccontextmanager
    async def _lock_for_tab(self, tab_id: str):
        lock = await self._tab_lock_for_id(tab_id)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()

    async def navigate(
        self,
        url: str,
        *,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = self._validate_url(url)
        timeout = self._resolve_timeout(timeout_ms)
        page, resolved_tab_id, make_active = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            await page.goto(
                normalized, wait_until="domcontentloaded", timeout=timeout
            )
            title = await page.title()
            current_url = getattr(page, "url", normalized)
            result_tab_id = self._register_page(page, make_active=make_active)
            return {"url": current_url, "title": title or "", "tab_id": result_tab_id}

    async def screenshot(
        self,
        *,
        full_page: bool = True,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        page, resolved_tab_id, make_active = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            image = await page.screenshot(full_page=bool(full_page))
            encoded = base64.b64encode(image).decode("ascii")
            result_tab_id = self._register_page(page, make_active=make_active)
            return {"image_base64": encoded, "tab_id": result_tab_id}

    async def warmup(self) -> dict[str, Any]:
        page = await self._ensure_page()
        tab_id = self._register_page(page, make_active=True)
        try:
            title = await page.title()
        except Exception:
            title = ""
        return {
            "url": getattr(page, "url", "") or "",
            "title": title or "",
            "tab_id": tab_id,
        }

    async def click(
        self,
        selector: str,
        *,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        timeout = self._resolve_timeout(timeout_ms)
        page, resolved_tab_id, make_active = await self._resolve_action_page(tab_id)
        resolved = selector.strip()
        async with self._lock_for_tab(resolved_tab_id):
            before_page_ids = {id(p) for p in self._context_pages()}

            semantic = self._parse_semantic_selector(resolved)
            if semantic is not None:
                role, name = semantic
                await self._click_by_accessible_name(
                    page,
                    role=role,
                    name=name,
                    timeout_ms=timeout,
                )
                await self._promote_new_tab_if_any(before_page_ids, activate_new_tab=make_active)
                return await self._click_response(
                    resolved,
                    page=page,
                    tab_id=resolved_tab_id,
                    make_active=make_active,
                )

            if resolved.lower().startswith("aria/"):
                locator = page.locator(f"aria={resolved[5:].strip()}").first
                await self._click_locator_with_overlay_fallback(
                    page,
                    locator,
                    timeout_ms=timeout,
                )
                await self._promote_new_tab_if_any(before_page_ids, activate_new_tab=make_active)
                return await self._click_response(
                    resolved,
                    page=page,
                    tab_id=resolved_tab_id,
                    make_active=make_active,
                )

            if resolved.lower().startswith("aria="):
                locator = page.locator(resolved).first
                await self._click_locator_with_overlay_fallback(
                    page,
                    locator,
                    timeout_ms=timeout,
                )
                await self._promote_new_tab_if_any(before_page_ids, activate_new_tab=make_active)
                return await self._click_response(
                    resolved,
                    page=page,
                    tab_id=resolved_tab_id,
                    make_active=make_active,
                )

            selected = await self._try_select_option_by_selector(
                page,
                resolved,
                timeout_ms=timeout,
            )
            if selected:
                await self._promote_new_tab_if_any(before_page_ids, activate_new_tab=make_active)
                return await self._click_response(
                    resolved,
                    page=page,
                    tab_id=resolved_tab_id,
                    make_active=make_active,
                )

            locator = page.locator(resolved).first
            await self._click_locator_with_overlay_fallback(
                page,
                locator,
                timeout_ms=timeout,
            )
            await self._promote_new_tab_if_any(before_page_ids, activate_new_tab=make_active)
            return await self._click_response(
                resolved,
                page=page,
                tab_id=resolved_tab_id,
                make_active=make_active,
            )

    async def select_option(
        self,
        selector: str,
        *,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        resolved = selector.strip()
        timeout = self._resolve_timeout(timeout_ms)
        page, resolved_tab_id, make_active = await self._resolve_action_page(tab_id)
        criteria: dict[str, Any] = {}
        if value is not None:
            criteria["value"] = value
        if label is not None:
            criteria["label"] = label
        if index is not None:
            criteria["index"] = index

        parsed_option = self._parse_option_selector(resolved)
        target_selector = resolved
        if parsed_option is not None:
            target_selector, parsed_value = parsed_option
            if not criteria:
                criteria["value"] = parsed_value

        async with self._lock_for_tab(resolved_tab_id):
            semantic = self._parse_semantic_selector(target_selector)
            selected_values: list[str] = []
            if semantic is not None:
                role, name = semantic
                selected_values = await self._select_option_by_accessible_name(
                    page,
                    role=role,
                    name=name,
                    criteria=criteria,
                    timeout_ms=timeout,
                )
            elif target_selector.lower().startswith("aria/"):
                locator = page.locator(f"aria={target_selector[5:].strip()}").first
                selected_values = await locator.select_option(
                    **criteria, timeout=timeout
                )
            elif target_selector.lower().startswith("aria="):
                locator = page.locator(target_selector).first
                selected_values = await locator.select_option(
                    **criteria, timeout=timeout
                )
            else:
                selected_values = await page.select_option(
                    target_selector, **criteria, timeout=timeout
                )

            state = await self._page_state(
                page=page,
                tab_id=resolved_tab_id,
                make_active=make_active,
            )
            return {
                "selector": selector.strip(),
                "selected_values": selected_values,
                "criteria": criteria,
                **state,
            }

    async def wait_for(
        self,
        selector: str,
        *,
        condition: str = "visible",
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        resolved = selector.strip()
        cond = (condition or "").strip().lower()
        if cond not in {
            "visible",
            "hidden",
            "attached",
            "detached",
            "enabled",
            "disabled",
        }:
            raise ValueError(
                "condition must be one of: visible, hidden, attached, detached, enabled, disabled"
            )
        timeout = self._resolve_timeout(timeout_ms)

        page, resolved_tab_id, make_active = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            locator = self._locator_from_selector(page, resolved)
            if cond in {"visible", "hidden", "attached", "detached"}:
                await locator.wait_for(state=cond, timeout=timeout)
            elif cond == "enabled":
                await self._wait_for_enabled_state(
                    locator, enabled=True, timeout_ms=timeout
                )
            else:
                await self._wait_for_enabled_state(
                    locator, enabled=False, timeout_ms=timeout
                )

            state = await self._page_state(
                page=page,
                tab_id=resolved_tab_id,
                make_active=make_active,
            )
            return {
                "selector": resolved,
                "condition": cond,
                "satisfied": True,
                **state,
            }

    async def get_value(self, selector: str, *, tab_id: str | None = None) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        resolved = selector.strip()
        page, resolved_tab_id, _ = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            semantic = self._parse_semantic_selector(resolved)
            if semantic is not None:
                role, name = semantic
                state = await self._state_by_accessible_name(page, role=role, name=name)
                return {"selector": resolved, "found": True, **state}

            if resolved.lower().startswith("aria/"):
                locator = page.locator(f"aria={resolved[5:].strip()}").first
                state = await self._state_from_locator(locator)
                return {"selector": resolved, "found": True, **state}
            if resolved.lower().startswith("aria="):
                locator = page.locator(resolved).first
                state = await self._state_from_locator(locator)
                return {"selector": resolved, "found": True, **state}

            element = await page.query_selector(resolved)
            if element is None:
                return {"selector": resolved, "found": False}
            state = await page.evaluate(_ELEMENT_STATE_JS, element)
            return {"selector": resolved, "found": True, **state}

    async def fill_form(
        self,
        steps: list[dict[str, Any]],
        *,
        continue_on_error: bool = False,
        verify: bool = False,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must be a non-empty list")

        results: list[dict[str, Any]] = []
        failed = 0

        _, resolved_tab_id, make_active = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            for idx, raw_step in enumerate(steps):
                if not isinstance(raw_step, dict):
                    raise ValueError(f"steps[{idx}] must be an object")

                selector_raw = raw_step.get("selector")
                if not isinstance(selector_raw, str) or not selector_raw.strip():
                    raise ValueError(f"steps[{idx}].selector must be a non-empty string")
                selector = selector_raw.strip()

                action_raw = raw_step.get("action")
                action = action_raw.strip().lower() if isinstance(action_raw, str) else ""
                if not action:
                    if "text" in raw_step:
                        action = "type"
                    elif "value" in raw_step or "label" in raw_step or "index" in raw_step:
                        action = "select"
                    elif raw_step.get("click") is True:
                        action = "click"
                    elif "condition" in raw_step or "timeout_ms" in raw_step:
                        action = "wait"
                    else:
                        action = "type"

                step_result: dict[str, Any] = {
                    "index": idx,
                    "selector": selector,
                    "action": action,
                }
                timeout_raw = raw_step.get("timeout_ms")
                if timeout_raw is not None and (
                    not isinstance(timeout_raw, int)
                    or isinstance(timeout_raw, bool)
                    or timeout_raw <= 0
                ):
                    raise ValueError(f"steps[{idx}].timeout_ms must be a positive integer")

                try:
                    if action == "type":
                        text = raw_step.get("text")
                        if not isinstance(text, str):
                            raise ValueError(f"steps[{idx}].text must be a string")
                        result = await self.type_text(
                            selector,
                            text,
                            timeout_ms=timeout_raw,
                            tab_id=resolved_tab_id,
                        )
                    elif action == "select":
                        value = raw_step.get("value")
                        label = raw_step.get("label")
                        index_value = raw_step.get("index")
                        criteria_count = 0
                        if value is not None:
                            if not isinstance(value, str):
                                raise ValueError(f"steps[{idx}].value must be a string")
                            criteria_count += 1
                        if label is not None:
                            if not isinstance(label, str):
                                raise ValueError(f"steps[{idx}].label must be a string")
                            criteria_count += 1
                        if index_value is not None:
                            if (
                                not isinstance(index_value, int)
                                or isinstance(index_value, bool)
                                or index_value < 0
                            ):
                                raise ValueError(
                                    f"steps[{idx}].index must be a non-negative integer"
                                )
                            criteria_count += 1
                        if criteria_count == 0:
                            raise ValueError(
                                f"steps[{idx}] select action requires one of value, label, or index"
                            )
                        result = await self.select_option(
                            selector,
                            value=value,
                            label=label,
                            index=index_value,
                            timeout_ms=timeout_raw,
                            tab_id=resolved_tab_id,
                        )
                    elif action == "click":
                        result = await self.click(
                            selector,
                            timeout_ms=timeout_raw,
                            tab_id=resolved_tab_id,
                        )
                    elif action == "wait":
                        condition_raw = raw_step.get("condition", "visible")
                        if not isinstance(condition_raw, str):
                            raise ValueError(f"steps[{idx}].condition must be a string")
                        result = await self.wait_for(
                            selector,
                            condition=condition_raw,
                            timeout_ms=timeout_raw,
                            tab_id=resolved_tab_id,
                        )
                    else:
                        raise ValueError(
                            f"steps[{idx}].action must be one of: type, select, click, wait"
                        )

                    step_result["ok"] = True
                    step_result["result"] = result
                    if verify and action in {"type", "select"}:
                        step_result["verify"] = await self.get_value(
                            selector,
                            tab_id=resolved_tab_id,
                        )
                except Exception as exc:  # noqa: BLE001
                    step_result["ok"] = False
                    step_result["error"] = str(exc)
                    failed += 1
                    results.append(step_result)
                    if not continue_on_error:
                        raise RuntimeError(
                            f"browser command fill_form failed at step {idx} ({action} on {selector}): {exc}"
                        ) from exc
                    continue

                results.append(step_result)

            state = await self._page_state(
                tab_id=resolved_tab_id,
                make_active=make_active,
            )
            return {
                "ok": failed == 0,
                "total": len(steps),
                "completed": len(steps) - failed,
                "failed": failed,
                "steps": results,
                **state,
            }

    async def type_text(
        self,
        selector: str,
        text: str,
        *,
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("selector must be a non-empty string")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        timeout = self._resolve_timeout(timeout_ms)
        page, resolved_tab_id, _ = await self._resolve_action_page(tab_id)
        resolved = selector.strip()

        async with self._lock_for_tab(resolved_tab_id):
            semantic = self._parse_semantic_selector(resolved)
            if semantic is not None:
                role, name = semantic
                await self._fill_by_accessible_name(
                    page,
                    role=role,
                    name=name,
                    text=text,
                    timeout_ms=timeout,
                )
                return {"typed": True, "selector": resolved, "characters": len(text)}

            if resolved.lower().startswith("aria/"):
                await self._fill_by_aria_selector(
                    page,
                    resolved[5:].strip(),
                    text,
                    timeout_ms=timeout,
                )
                return {"typed": True, "selector": resolved, "characters": len(text)}

            await page.fill(resolved, text, timeout=timeout)
            return {"typed": True, "selector": resolved, "characters": len(text)}

    async def press_key(self, key: str, *, tab_id: str | None = None) -> dict[str, Any]:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")
        page, resolved_tab_id, _ = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            await page.keyboard.press(key.strip())
            return {"pressed": True, "key": key.strip()}

    async def scroll(
        self,
        *,
        direction: str = "down",
        amount: int = 500,
        selector: str | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        direction = (direction or "down").strip().lower()
        if direction not in {"up", "down", "left", "right"}:
            raise ValueError("direction must be one of: up, down, left, right")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError("amount must be a positive integer")

        page, resolved_tab_id, _ = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
            dx, dy = 0, 0
            if direction == "down":
                dy = amount
            elif direction == "up":
                dy = -amount
            elif direction == "right":
                dx = amount
            elif direction == "left":
                dx = -amount

            if selector is not None:
                if not isinstance(selector, str) or not selector.strip():
                    raise ValueError("selector must be null or a non-empty string")
                locator = self._locator_from_selector(page, selector.strip())
                await locator.scroll_into_view_if_needed(timeout=self._timeout_ms)
                box = await locator.bounding_box(timeout=self._timeout_ms)
                if box:
                    cx = box["x"] + box["width"] / 2
                    cy = box["y"] + box["height"] / 2
                    await page.mouse.move(cx, cy)

            await page.mouse.wheel(dx, dy)
            await page.wait_for_timeout(300)

            scroll_pos = await page.evaluate(
                "(sel) => {"
                "  const el = sel ? document.querySelector(sel) : document.documentElement;"
                "  if (!el) return {scrollTop: 0, scrollLeft: 0, scrollHeight: 0, clientHeight: 0};"
                "  return {scrollTop: el.scrollTop, scrollLeft: el.scrollLeft,"
                "          scrollHeight: el.scrollHeight, clientHeight: el.clientHeight};"
                "}",
                selector if selector else None,
            )

            return {
                "scrolled": True,
                "direction": direction,
                "amount": amount,
                "selector": selector,
                **scroll_pos,
            }

    async def get_text(
        self,
        selector: str | None = None,
        *,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        page, resolved_tab_id, _ = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
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
            semantic = self._parse_semantic_selector(sel)
            if semantic is not None:
                role, name = semantic
                text = await self._inner_text_by_accessible_name(page, role=role, name=name)
                text, truncated = _truncate_content(text)
                result = {"selector": sel, "text": text}
                if truncated:
                    result["truncated"] = True
                return result
            if sel.lower().startswith("aria/"):
                locator = page.locator(f"aria={sel[5:].strip()}").first
                text = await locator.inner_text(timeout=self._timeout_ms)
                text, truncated = _truncate_content(text or "")
                result = {"selector": sel, "text": text}
                if truncated:
                    result["truncated"] = True
                return result
            # Use innerText via JS eval (respects display:none, excludes hidden content)
            try:
                element = await page.query_selector(sel)
                if element is not None:
                    text = (
                        await page.evaluate(
                            """
                            el => {
                              const tag = (el.tagName || '').toLowerCase();
                              if (tag === 'input' || tag === 'textarea') {
                                return String(el.value ?? '');
                              }
                              if (tag === 'select') {
                                const selected = Array.from(el.selectedOptions || [])
                                  .map(opt => (opt.textContent || '').trim())
                                  .filter(Boolean);
                                return selected.length ? selected.join(', ') : String(el.value ?? '');
                              }
                              return el.innerText || '';
                            }
                            """,
                            element,
                        )
                        or ""
                    )
                else:
                    text = ""
            except Exception:
                text = await page.text_content(sel, timeout=self._timeout_ms) or ""
            text, truncated = _truncate_content(text)
            result = {"selector": sel, "text": text}
            if truncated:
                result["truncated"] = True
            return result

    async def list_tabs(self) -> dict[str, Any]:
        await self._tab_admin_lock.acquire()
        try:
            await self._ensure_page()
            self._sync_tabs()
            pages = self._context_pages()
            tabs: list[dict[str, Any]] = []
            for page in pages:
                tab_id = self._register_page(page, make_active=False)
                try:
                    title = await page.title()
                except Exception:
                    title = ""
                url = getattr(page, "url", "") or ""
                tabs.append(
                    {
                        "tab_id": tab_id,
                        "title": title or "",
                        "url": url,
                        "active": tab_id == self._active_tab_id,
                    }
                )
            return {"tabs": tabs, "active_tab_id": self._active_tab_id}
        finally:
            self._tab_admin_lock.release()

    async def open_tab(self, url: str = "about:blank") -> dict[str, Any]:
        await self._tab_admin_lock.acquire()
        try:
            _ = await self._ensure_page()
            if self._context is None:
                raise RuntimeError("Browser context is not available")
            page = await self._context.new_page()
            target = (url or "").strip() or "about:blank"
            if target != "about:blank":
                target = self._validate_url(target)
                await page.goto(
                    target, wait_until="domcontentloaded", timeout=self._timeout_ms
                )
            tab_id = self._register_page(page, make_active=True)
            try:
                title = await page.title()
            except Exception:
                title = ""
            return {
                "opened": True,
                "tab_id": tab_id,
                "url": getattr(page, "url", target) or target,
                "title": title or "",
            }
        finally:
            self._tab_admin_lock.release()

    async def focus_tab(self, tab_id: str) -> dict[str, Any]:
        if not isinstance(tab_id, str) or not tab_id.strip():
            raise ValueError("tab_id must be a non-empty string")
        await self._tab_admin_lock.acquire()
        try:
            page = await self._page_for_tab_id(tab_id.strip())
            try:
                await page.bring_to_front()
            except Exception:
                pass
            resolved_id = self._register_page(page, make_active=True)
            try:
                title = await page.title()
            except Exception:
                title = ""
            return {
                "focused": True,
                "tab_id": resolved_id,
                "url": getattr(page, "url", "") or "",
                "title": title or "",
            }
        finally:
            self._tab_admin_lock.release()

    async def close_tab(self, tab_id: str) -> dict[str, Any]:
        if not isinstance(tab_id, str) or not tab_id.strip():
            raise ValueError("tab_id must be a non-empty string")
        await self._tab_admin_lock.acquire()
        try:
            normalized_tab_id = tab_id.strip()
            page = await self._page_for_tab_id(normalized_tab_id)
            page_key = id(page)
            closing_active = self._tab_ids_by_page.get(page_key) == self._active_tab_id
            await page.close()
            self._tab_ids_by_page.pop(page_key, None)
            self._tab_action_locks.pop(normalized_tab_id, None)
            self._sync_tabs()
            if closing_active:
                self._active_tab_id = None
                remaining = self._context_pages()
                if remaining:
                    self._register_page(remaining[0], make_active=True)
                else:
                    self._page = None
            return {
                "closed": True,
                "tab_id": normalized_tab_id,
                "active_tab_id": self._active_tab_id,
            }
        finally:
            self._tab_admin_lock.release()

    async def evaluate(self, expression: str, *, tab_id: str | None = None) -> dict[str, Any]:
        """Execute JavaScript in the page context and return the result."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        result = await page.evaluate(expression)
        return {"result": result, "url": page.url}

    async def get_html(self, selector: str | None = None, *, tab_id: str | None = None) -> dict[str, Any]:
        """Get the outer HTML of the page or a specific element."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        if selector:
            element = await page.query_selector(selector)
            if element is None:
                return {"html": None, "found": False, "selector": selector}
            html = await element.evaluate("el => el.outerHTML")
            return {"html": html, "found": True, "selector": selector, "url": page.url}
        html = await page.content()
        return {"html": html, "url": page.url}

    async def get_cookies(self, *, tab_id: str | None = None) -> dict[str, Any]:
        """Return all cookies for the current page."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        cookies = await page.context.cookies()
        return {"cookies": cookies, "url": page.url, "count": len(cookies)}

    async def set_cookies(self, cookies: list[dict], *, tab_id: str | None = None) -> dict[str, Any]:
        """Set one or more cookies in the browser context."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        await page.context.add_cookies(cookies)
        return {"set": True, "count": len(cookies)}

    async def get_console_logs(self, *, tab_id: str | None = None) -> dict[str, Any]:
        """Return buffered console log messages for the current page."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        key = id(page)
        logs = list(self._console_logs.get(key, []))
        return {"logs": logs, "count": len(logs), "url": page.url}

    async def setup_network_intercept(
        self,
        url_pattern: str,
        action: str = "log",
        response_body: str | None = None,
        response_status: int = 200,
        *,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Intercept network requests matching a URL glob pattern.

        action='log'   — record matching requests (retrieve with browser command=network_logs).
        action='block' — abort matching requests.
        action='mock'  — return a static response_body instead.
        """
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        key = id(page)

        async def _handler(route, request):
            entry = {"url": request.url, "method": request.method, "action": action}
            self._network_logs.setdefault(key, []).append(entry)
            if len(self._network_logs[key]) > 200:
                self._network_logs[key] = self._network_logs[key][-200:]
            if action == "block":
                await route.abort()
            elif action == "mock":
                await route.fulfill(status=response_status, body=response_body or "")
            else:
                await route.continue_()

        await page.route(url_pattern, _handler)
        self._active_routes.setdefault(key, []).append(url_pattern)
        return {"intercepting": True, "pattern": url_pattern, "action": action}

    async def get_network_logs(self, *, tab_id: str | None = None) -> dict[str, Any]:
        """Return buffered network intercept log entries."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        key = id(page)
        logs = list(self._network_logs.get(key, []))
        return {"logs": logs, "count": len(logs), "url": page.url}

    async def clear_network_intercepts(self, *, tab_id: str | None = None) -> dict[str, Any]:
        """Remove all active route intercepts on the current page."""
        page, _tid, _locked = await self._resolve_action_page(tab_id)
        await page.unroute_all()
        key = id(page)
        self._active_routes.pop(key, None)
        self._network_logs.pop(key, None)
        return {"cleared": True}

    def _parse_semantic_selector(self, selector: str) -> tuple[str, str] | None:
        # Accept snapshot-friendly selectors such as "textbox: Email" and "button: Accept".
        match = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*:\s*(.+?)\s*$", selector)
        if not match:
            return None
        raw_role = match.group(1).strip().lower()
        raw_name = match.group(2).strip().strip("\"'")
        if not raw_name:
            return None
        role = _SEMANTIC_ROLE_ALIASES.get(raw_role)
        if role is None:
            return None
        return role, raw_name

    def _name_candidates(self, name: str) -> list[str]:
        base = " ".join(name.strip().split())
        if not base:
            return []
        out: list[str] = [base]

        reduced = re.sub(r"\s*,\s*required\b.*$", "", base, flags=re.IGNORECASE).strip()
        if reduced and reduced not in out:
            out.append(reduced)

        before_colon = base.split(":", 1)[0].strip()
        if before_colon and before_colon not in out:
            out.append(before_colon)

        first_words = " ".join(base.split()[:3]).strip()
        if first_words and first_words not in out:
            out.append(first_words)

        return out

    async def _click_by_accessible_name(
        self,
        page: Any,
        *,
        role: str,
        name: str,
        timeout_ms: int,
    ) -> None:
        last_exc: Exception | None = None
        for candidate in self._name_candidates(name):
            try:
                locator = page.get_by_role(role, name=candidate, exact=False).first
                await self._click_locator_with_overlay_fallback(
                    page,
                    locator,
                    timeout_ms=timeout_ms,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise ValueError("No accessible-name candidates available for click")

    async def _click_locator_with_overlay_fallback(
        self,
        page: Any,
        locator: Any,
        *,
        timeout_ms: int,
    ) -> None:
        try:
            await locator.click(timeout=timeout_ms)
            return
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if "intercepts pointer events" not in message:
                raise

        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.05)
        try:
            await locator.click(timeout=min(timeout_ms, 3_000))
            return
        except Exception:
            pass

        await locator.click(timeout=min(timeout_ms, 3_000), force=True)

    async def _fill_by_aria_selector(
        self,
        page: Any,
        name: str,
        text: str,
        *,
        timeout_ms: int,
    ) -> None:
        last_exc: Exception | None = None
        for candidate in self._name_candidates(name):
            try:
                await page.locator(f"aria={candidate}").first.fill(
                    text, timeout=timeout_ms
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise ValueError("No accessible-name candidates available for aria fill")

    async def _fill_by_accessible_name(
        self,
        page: Any,
        *,
        role: str,
        name: str,
        text: str,
        timeout_ms: int,
    ) -> None:
        last_exc: Exception | None = None
        for candidate in self._name_candidates(name):
            try:
                await page.get_by_role(role, name=candidate, exact=False).first.fill(
                    text, timeout=timeout_ms
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

        if role in {"textbox", "searchbox", "combobox", "spinbutton"}:
            for candidate in self._name_candidates(name):
                try:
                    await page.get_by_label(candidate, exact=False).first.fill(
                        text, timeout=timeout_ms
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
            for candidate in self._name_candidates(name):
                try:
                    await page.get_by_placeholder(candidate, exact=False).first.fill(
                        text, timeout=timeout_ms
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise ValueError("No accessible-name candidates available for fill")

    async def _select_option_by_accessible_name(
        self,
        page: Any,
        *,
        role: str,
        name: str,
        criteria: dict[str, Any],
        timeout_ms: int,
    ) -> list[str]:
        last_exc: Exception | None = None
        role_candidates = [role]
        if role == "option":
            role_candidates = ["combobox", "listbox", "option"]
        for role_candidate in role_candidates:
            for candidate in self._name_candidates(name):
                try:
                    locator = page.get_by_role(
                        role_candidate, name=candidate, exact=False
                    ).first
                    return await locator.select_option(
                        **criteria, timeout=timeout_ms
                    )
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
        for candidate in self._name_candidates(name):
            try:
                locator = page.get_by_label(candidate, exact=False).first
                return await locator.select_option(**criteria, timeout=timeout_ms)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise ValueError("No accessible-name candidates available for select_option")

    async def _inner_text_by_accessible_name(
        self, page: Any, *, role: str, name: str
    ) -> str:
        last_exc: Exception | None = None
        for candidate in self._name_candidates(name):
            try:
                return await page.get_by_role(
                    role, name=candidate, exact=False
                ).first.inner_text(timeout=self._timeout_ms)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        return ""

    def _locator_from_selector(self, page: Any, selector: str):
        semantic = self._parse_semantic_selector(selector)
        if semantic is not None:
            role, name = semantic
            return page.get_by_role(role, name=name, exact=False).first
        if selector.lower().startswith("aria/"):
            return page.locator(f"aria={selector[5:].strip()}").first
        return page.locator(selector).first

    async def _wait_for_enabled_state(
        self, locator: Any, *, enabled: bool, timeout_ms: int
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout_ms / 1000.0)
        last_exc: Exception | None = None
        while loop.time() < deadline:
            try:
                current = await locator.is_enabled(timeout=min(timeout_ms, 2_000))
                if bool(current) == enabled:
                    return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            await asyncio.sleep(0.1)
        if last_exc is not None:
            raise TimeoutError(str(last_exc))
        raise TimeoutError(f"Timeout waiting for enabled={enabled}")

    async def _state_from_locator(self, locator: Any) -> dict[str, Any]:
        return await locator.evaluate(_ELEMENT_STATE_JS, timeout=self._timeout_ms)

    async def _state_by_accessible_name(
        self, page: Any, *, role: str, name: str
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for candidate in self._name_candidates(name):
            try:
                locator = page.get_by_role(role, name=candidate, exact=False).first
                return await self._state_from_locator(locator)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if role in {"textbox", "combobox", "searchbox", "spinbutton"}:
            for candidate in self._name_candidates(name):
                try:
                    locator = page.get_by_label(candidate, exact=False).first
                    return await self._state_from_locator(locator)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise ValueError("No accessible-name candidates available for get_value")

    async def _promote_new_tab_if_any(
        self,
        before_page_ids: set[int],
        *,
        activate_new_tab: bool = True,
    ) -> None:
        if self._context is None:
            return
        await asyncio.sleep(0)
        pages = self._context_pages()
        new_pages = [page for page in pages if id(page) not in before_page_ids]
        if not new_pages:
            self._sync_tabs()
            return
        candidate = new_pages[-1]
        try:
            await candidate.wait_for_load_state(
                "domcontentloaded", timeout=min(self._timeout_ms, 5_000)
            )
        except Exception:
            pass
        self._register_page(candidate, make_active=activate_new_tab)
        await self._maximize_window(candidate)
        self._sync_tabs()

    async def _click_response(
        self,
        selector: str,
        *,
        page: Any | None = None,
        tab_id: str | None = None,
        make_active: bool = True,
    ) -> dict[str, Any]:
        state = await self._page_state(
            page=page,
            tab_id=tab_id,
            make_active=make_active,
        )
        return {
            "clicked": True,
            "selector": selector,
            **state,
        }

    async def _page_state(
        self,
        *,
        page: Any | None = None,
        tab_id: str | None = None,
        make_active: bool = True,
    ) -> dict[str, Any]:
        page_ref = page if page is not None else await self._ensure_page()
        resolved_tab_id = tab_id or self._register_page(page_ref, make_active=make_active)
        if tab_id is not None:
            self._register_page(page_ref, make_active=make_active)
        try:
            title = await page_ref.title()
        except Exception:
            title = ""
        return {
            "url": getattr(page_ref, "url", "") or "",
            "title": title or "",
            "tab_id": resolved_tab_id,
        }

    async def _maximize_window(self, page: Any) -> None:
        if self._context is None:
            return
        try:
            session = await self._context.new_cdp_session(page)
            window_info = await session.send("Browser.getWindowForTarget")
            window_id = window_info.get("windowId")
            if window_id is None:
                return
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "maximized"}},
            )
        except Exception:
            # Best effort only; some runtimes do not expose window controls.
            return

    def _parse_option_selector(self, selector: str) -> tuple[str, str] | None:
        # Support selectors like "option[value='2']" and "select[name='x'] option[value='2']".
        global_match = re.match(
            r"""^\s*option\s*\[\s*value\s*=\s*(['"])(.+?)\1\s*\]\s*$""",
            selector,
            flags=re.IGNORECASE,
        )
        if global_match:
            return "select", global_match.group(2).strip()
        scoped_match = re.match(
            r"""^\s*(.+?)\s+option\s*\[\s*value\s*=\s*(['"])(.+?)\2\s*\]\s*$""",
            selector,
            flags=re.IGNORECASE,
        )
        if scoped_match:
            return scoped_match.group(1).strip(), scoped_match.group(3).strip()
        return None

    async def _try_select_option_by_selector(
        self,
        page: Any,
        selector: str,
        *,
        timeout_ms: int,
    ) -> bool:
        parsed = self._parse_option_selector(selector)
        if parsed is None:
            return False
        select_selector, value = parsed
        if not value:
            return False
        try:
            selected_values = await page.select_option(
                select_selector,
                value=value,
                timeout=timeout_ms,
            )
        except Exception:
            return False
        return bool(selected_values)

    async def get_snapshot(
        self,
        *,
        interactive_only: bool = False,
        max_depth: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        import json as _json
        import logging

        logger = logging.getLogger(__name__)
        page, resolved_tab_id, _ = await self._resolve_action_page(tab_id)
        async with self._lock_for_tab(resolved_tab_id):
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
                            result: dict[str, Any] = {
                                "snapshot": raw,
                                "method": "ariaSnapshot",
                            }
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
                        text = (
                            ai_result.get("snapshot")
                            or ai_result.get("text")
                            or str(ai_result)
                        )
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
                        if not role or role in (
                            "none",
                            "generic",
                            "InlineTextBox",
                            "LineBreak",
                        ):
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
                        result = {
                            "snapshot": raw,
                            "method": "cdpAXTree",
                            "nodes": len(lines),
                        }
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
                            snapshot = _filter_snapshot(
                                snapshot,
                                interactive_only=interactive_only,
                                max_depth=max_depth,
                            )
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
                    "Use browser with command='get_text' instead to read the page content, "
                    "or browser with command='screenshot' to see the page visually."
                ),
                "methods_tried": methods_tried,
            }

    async def reset(self) -> dict[str, Any]:
        await self.close()
        stale_lock_cleared = self._cleanup_stale_profile_lock()
        page = await self._ensure_page()
        try:
            await page.goto(
                "about:blank", wait_until="domcontentloaded", timeout=self._timeout_ms
            )
        except Exception:
            pass
        current_url = getattr(page, "url", "about:blank")
        return {
            "reset": True,
            "url": current_url,
            "profile_dir": self._user_data_dir,
            "stale_lock_cleared": stale_lock_cleared,
        }

    async def ensure_connected(self) -> None:
        page = await self._ensure_page()
        try:
            await page.title()
        except Exception:
            await self.close()
            page = await self._ensure_page()
            await page.title()

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
        self._tab_ids_by_page = {}
        self._active_tab_id = None
        self._tab_counter = 0
        self._tab_action_locks = {}

        if page is not None and hasattr(page, "close"):
            try:
                await page.close()
            except Exception:
                pass
        if context is not None and hasattr(context, "close"):
            try:
                # context.close() on a CDP connection closes the remote browser
                # context and can kill Chromium — skip it for CDP.
                if not self._cdp_endpoint:
                    await context.close()
            except Exception:
                pass
        if browser is not None and hasattr(browser, "close"):
            try:
                # browser.close() on a CDP connection terminates the remote
                # Chromium process — skip it for CDP.
                if not self._cdp_endpoint:
                    await browser.close()
            except Exception:
                pass
        if playwright is not None and hasattr(playwright, "stop"):
            try:
                await playwright.stop()
            except Exception:
                pass

    async def _ensure_page(self):
        if self._page is not None and self._context is None:
            return self._page
        if self._context is not None:
            self._sync_tabs()
            if self._page is not None:
                return self._page
        if async_playwright is None:
            raise RuntimeError("Playwright runtime is not available")

        self._playwright_context = async_playwright()
        self._playwright = await self._playwright_context.start()

        # Remote CDP connection (runtime container)
        if self._cdp_endpoint:
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_endpoint)
            self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            await apply_stealth_init_script(self._context)
            page = (
                self._context.pages[0]
                if self._context.pages
                else await self._context.new_page()
            )
            self._register_page(page, make_active=True)
            await self._maximize_window(page)
            self._sync_tabs()
            return page

        # Local launch
        launch_options = build_chromium_launch_options(headless=self._headless)
        context_options = build_browser_context_options()

        if self._user_data_dir:
            try:
                self._context = (
                    await self._playwright.chromium.launch_persistent_context(
                        self._user_data_dir,
                        **launch_options,
                        **context_options,
                    )
                )
                await apply_stealth_init_script(self._context)
                page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
                self._register_page(page, make_active=True)
                await self._maximize_window(page)
                self._sync_tabs()
                return page
            except Exception as exc:
                # A locked profile should not make browser tools unusable.
                if "ProcessSingleton" not in str(exc):
                    raise

        self._browser = await self._playwright.chromium.launch(**launch_options)
        self._context = await self._browser.new_context(**context_options)
        await apply_stealth_init_script(self._context)
        page = await self._context.new_page()
        self._register_page(page, make_active=True)
        await self._maximize_window(page)
        self._sync_tabs()
        return page

    def _next_tab_id(self) -> str:
        self._tab_counter += 1
        return f"t{self._tab_counter}"

    def _register_page(self, page: Any, *, make_active: bool) -> str:
        page_key = id(page)
        tab_id = self._tab_ids_by_page.get(page_key)
        if tab_id is None:
            tab_id = self._next_tab_id()
            self._tab_ids_by_page[page_key] = tab_id
            # Capture console messages for this page
            def _on_console(msg, _key=page_key):
                entry = {"type": msg.type, "text": msg.text}
                buf = self._console_logs.setdefault(_key, [])
                buf.append(entry)
                if len(buf) > 500:
                    self._console_logs[_key] = buf[-500:]
            page.on("console", _on_console)
        if make_active:
            self._active_tab_id = tab_id
            self._page = page
        return tab_id

    def _sync_tabs(self) -> None:
        if self._context is None:
            self._tab_ids_by_page = {}
            self._active_tab_id = None
            self._page = None
            return

        pages = self._context_pages()
        alive_keys = {id(page) for page in pages}
        stale_keys = [key for key in self._tab_ids_by_page if key not in alive_keys]
        for key in stale_keys:
            removed = self._tab_ids_by_page.pop(key, None)
            if removed and removed == self._active_tab_id:
                self._active_tab_id = None

        for page in pages:
            self._register_page(page, make_active=False)

        if self._active_tab_id is not None:
            active = self._active_page_from_context()
            if active is not None:
                self._page = active
                return
            self._active_tab_id = None

        if pages:
            self._register_page(pages[0], make_active=True)
        else:
            self._page = None

    def _active_page_from_context(self):
        if self._context is None:
            return None
        pages = self._context_pages()
        if not pages:
            return None
        if self._active_tab_id is None:
            return None
        for page in pages:
            tab_id = self._tab_ids_by_page.get(id(page))
            if tab_id == self._active_tab_id:
                return page
        return None

    async def _page_for_tab_id(self, tab_id: str):
        await self._ensure_page()
        if self._context is None:
            if self._page is None:
                raise ValueError("tab not found")
            current_tab_id = self._register_page(self._page, make_active=False)
            if current_tab_id == tab_id:
                return self._page
            raise ValueError("tab not found")
        self._sync_tabs()
        for page in self._context_pages():
            current_id = self._tab_ids_by_page.get(id(page))
            if current_id == tab_id:
                return page
        raise ValueError("tab not found")

    def _context_pages(self) -> list[Any]:
        if self._context is None:
            return []
        pages = list(getattr(self._context, "pages", []) or [])
        if pages:
            return pages
        if self._page is not None:
            return [self._page]
        return []

    def _validate_url(self, url: str) -> str:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string")
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be a valid http/https URL")
        return url.strip()

    async def _snapshot_for_ai(self, page: Any) -> str | None:
        snapshot_fn = getattr(page, "_snapshotForAI", None)
        if snapshot_fn is None:
            return None
        try:
            result = await snapshot_fn(timeout=min(self._timeout_ms, 10_000))
        except Exception:
            return None
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            value = result.get("snapshot") or result.get("text")
            return value if isinstance(value, str) else str(result)
        return str(result) if result is not None else None

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
