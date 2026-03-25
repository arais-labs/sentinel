"""Browser action handlers."""
from __future__ import annotations

from typing import Any

from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext

from .shared import optional_browser_tab_id, resolve_browser_manager

async def handle_navigate(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    url = payload.get("url")
    timeout_ms = payload.get("timeout_ms")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(url, str) or not url.strip():
        raise ToolValidationError("Field 'url' must be a non-empty string")
    if timeout_ms is not None and (
        not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
    ):
        raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
    return await manager.navigate(url.strip(), timeout_ms=timeout_ms, tab_id=tab_id)


async def handle_screenshot(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    full_page = payload.get("full_page", True)
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(full_page, bool):
        raise ToolValidationError("Field 'full_page' must be a boolean")
    return await manager.screenshot(full_page=full_page, tab_id=tab_id)


async def handle_click(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    selector = payload.get("selector")
    timeout_ms = payload.get("timeout_ms")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(selector, str) or not selector.strip():
        raise ToolValidationError("Field 'selector' must be a non-empty string")
    if timeout_ms is not None and (
        not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
    ):
        raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
    return await manager.click(selector.strip(), timeout_ms=timeout_ms, tab_id=tab_id)


async def handle_type(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    selector = payload.get("selector")
    text = payload.get("text")
    timeout_ms = payload.get("timeout_ms")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(selector, str) or not selector.strip():
        raise ToolValidationError("Field 'selector' must be a non-empty string")
    if not isinstance(text, str):
        raise ToolValidationError("Field 'text' must be a string")
    if timeout_ms is not None and (
        not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
    ):
        raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
    return await manager.type_text(selector.strip(), text, timeout_ms=timeout_ms, tab_id=tab_id)


async def handle_select(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    selector = payload.get("selector")
    value = payload.get("value")
    label = payload.get("label")
    index = payload.get("index")
    timeout_ms = payload.get("timeout_ms")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(selector, str) or not selector.strip():
        raise ToolValidationError("Field 'selector' must be a non-empty string")
    criteria_count = 0
    if value is not None:
        if not isinstance(value, str):
            raise ToolValidationError("Field 'value' must be a string")
        criteria_count += 1
    if label is not None:
        if not isinstance(label, str):
            raise ToolValidationError("Field 'label' must be a string")
        criteria_count += 1
    if index is not None:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ToolValidationError("Field 'index' must be a non-negative integer")
        criteria_count += 1
    if criteria_count == 0:
        raise ToolValidationError("Provide one of 'value', 'label', or 'index' for browser select")
    if timeout_ms is not None and (
        not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
    ):
        raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
    return await manager.select_option(
        selector.strip(),
        value=value,
        label=label,
        index=index,
        timeout_ms=timeout_ms,
        tab_id=tab_id,
    )


async def handle_wait_for(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    selector = payload.get("selector")
    condition = payload.get("condition", "visible")
    timeout_ms = payload.get("timeout_ms")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(selector, str) or not selector.strip():
        raise ToolValidationError("Field 'selector' must be a non-empty string")
    if not isinstance(condition, str) or not condition.strip():
        raise ToolValidationError("Field 'condition' must be a non-empty string")
    if timeout_ms is not None and (
        not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0
    ):
        raise ToolValidationError("Field 'timeout_ms' must be a positive integer")
    return await manager.wait_for(
        selector.strip(),
        condition=condition.strip(),
        timeout_ms=timeout_ms,
        tab_id=tab_id,
    )


async def handle_get_value(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    selector = payload.get("selector")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(selector, str) or not selector.strip():
        raise ToolValidationError("Field 'selector' must be a non-empty string")
    return await manager.get_value(selector.strip(), tab_id=tab_id)


async def handle_fill_form(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    steps = payload.get("steps")
    continue_on_error = payload.get("continue_on_error", False)
    verify = payload.get("verify", False)
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(steps, list) or not steps:
        raise ToolValidationError("Field 'steps' must be a non-empty array")
    if not isinstance(continue_on_error, bool):
        raise ToolValidationError("Field 'continue_on_error' must be a boolean")
    if not isinstance(verify, bool):
        raise ToolValidationError("Field 'verify' must be a boolean")
    return await manager.fill_form(
        steps,
        continue_on_error=continue_on_error,
        verify=verify,
        tab_id=tab_id,
    )


async def handle_press_key(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    key = payload.get("key")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(key, str) or not key.strip():
        raise ToolValidationError("Field 'key' must be a non-empty string")
    return await manager.press_key(key.strip(), tab_id=tab_id)


async def handle_scroll(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    direction = payload.get("direction", "down")
    amount = payload.get("amount", 500)
    selector = payload.get("selector")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(direction, str) or direction.strip().lower() not in {"up", "down", "left", "right"}:
        raise ToolValidationError("Field 'direction' must be one of: up, down, left, right")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ToolValidationError("Field 'amount' must be a positive integer (pixels)")
    if selector is not None and (not isinstance(selector, str) or not selector.strip()):
        raise ToolValidationError("Field 'selector' must be null or a non-empty string")
    return await manager.scroll(
        direction=direction.strip().lower(),
        amount=amount,
        selector=selector,
        tab_id=tab_id,
    )


async def handle_get_text(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    selector = payload.get("selector")
    tab_id = optional_browser_tab_id(payload)
    if selector is not None and (not isinstance(selector, str) or not selector.strip()):
        raise ToolValidationError("Field 'selector' must be null or a non-empty string")
    return await manager.get_text(
        selector.strip() if isinstance(selector, str) else None,
        tab_id=tab_id,
    )


async def handle_snapshot(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    interactive_only = payload.get("interactive_only", False)
    max_depth = payload.get("max_depth")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(interactive_only, bool):
        raise ToolValidationError("Field 'interactive_only' must be a boolean")
    if max_depth is not None and (
        not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 1
    ):
        raise ToolValidationError("Field 'max_depth' must be a positive integer")
    return await manager.get_snapshot(
        interactive_only=interactive_only,
        max_depth=max_depth,
        tab_id=tab_id,
    )


async def handle_reset(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    return await manager.reset()


async def handle_tabs(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    return await manager.list_tabs()


async def handle_tab_open(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    raw_url = payload.get("url", "about:blank")
    if not isinstance(raw_url, str):
        raise ToolValidationError("Field 'url' must be a string")
    return await manager.open_tab(raw_url)


async def handle_tab_focus(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = payload.get("tab_id")
    if not isinstance(tab_id, str) or not tab_id.strip():
        raise ToolValidationError("Field 'tab_id' must be a non-empty string")
    return await manager.focus_tab(tab_id.strip())


async def handle_tab_close(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = payload.get("tab_id")
    if not isinstance(tab_id, str) or not tab_id.strip():
        raise ToolValidationError("Field 'tab_id' must be a non-empty string")
    return await manager.close_tab(tab_id.strip())


async def handle_evaluate(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    expression = payload.get("expression")
    tab_id = optional_browser_tab_id(payload)
    if not isinstance(expression, str) or not expression.strip():
        raise ToolValidationError("Field 'expression' must be a non-empty string")
    return await manager.evaluate(expression.strip(), tab_id=tab_id)


async def handle_get_html(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = optional_browser_tab_id(payload)
    return await manager.get_html(tab_id=tab_id)


async def handle_get_cookies(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = optional_browser_tab_id(payload)
    return await manager.get_cookies(tab_id=tab_id)


async def handle_set_cookies(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    cookies = payload.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        raise ToolValidationError("Field 'cookies' must be a non-empty array")
    return await manager.set_cookies(cookies)


async def handle_console_logs(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = optional_browser_tab_id(payload)
    return await manager.get_console_logs(tab_id=tab_id)


async def handle_network_intercept(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    url_pattern = payload.get("url_pattern")
    tab_id = optional_browser_tab_id(payload)
    action = payload.get("intercept_action", "log")
    response_body = payload.get("response_body")
    response_status = payload.get("response_status", 200)
    if not isinstance(url_pattern, str) or not url_pattern.strip():
        raise ToolValidationError("Field 'url_pattern' must be a non-empty string")
    if not isinstance(action, str) or action not in {"log", "block", "mock"}:
        raise ToolValidationError("Field 'intercept_action' must be one of: log, block, mock")
    if not isinstance(response_status, int) or isinstance(response_status, bool):
        raise ToolValidationError("Field 'response_status' must be an integer")
    return await manager.setup_network_intercept(
        url_pattern.strip(),
        action=action,
        response_body=response_body,
        response_status=response_status,
        tab_id=tab_id,
    )


async def handle_network_logs(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = optional_browser_tab_id(payload)
    return await manager.get_network_logs(tab_id=tab_id)


async def handle_clear_network_intercepts(
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
) -> dict[str, Any]:
    manager = await resolve_browser_manager(payload, runtime)
    tab_id = optional_browser_tab_id(payload)
    return await manager.clear_network_intercepts(tab_id=tab_id)

BROWSER_TAB_MANAGEMENT_COMMANDS = frozenset(
    {"tabs", "tab_open", "tab_focus", "tab_close", "reset"}
)

BROWSER_TAB_TARGETABLE_COMMANDS = frozenset(
    {
        "navigate",
        "screenshot",
        "click",
        "type",
        "select",
        "wait_for",
        "get_value",
        "fill_form",
        "press_key",
        "scroll",
        "get_text",
        "snapshot",
        "evaluate",
        "get_html",
        "get_cookies",
        "console_logs",
        "network_intercept",
        "network_logs",
        "clear_network_intercepts",
    }
)
