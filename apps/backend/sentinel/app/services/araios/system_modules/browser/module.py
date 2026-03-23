from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_clear_network_intercepts,
    handle_click,
    handle_console_logs,
    handle_evaluate,
    handle_fill_form,
    handle_get_cookies,
    handle_get_html,
    handle_get_text,
    handle_get_value,
    handle_navigate,
    handle_network_intercept,
    handle_network_logs,
    handle_press_key,
    handle_reset,
    handle_scroll,
    handle_screenshot,
    handle_select,
    handle_set_cookies,
    handle_snapshot,
    handle_tab_close,
    handle_tab_focus,
    handle_tab_open,
    handle_tabs,
    handle_type,
    handle_wait_for,
)
from .shared import BROWSER_SESSION_PROP


def _base_schema(
    *,
    required: list[str] | None = None,
    properties: dict[str, dict] | None = None,
) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", *(required or [])],
        "properties": {
            **BROWSER_SESSION_PROP,
            **(properties or {}),
        },
    }


def _tab_id_prop() -> dict:
    return {"type": "string", "description": "Optional browser tab identifier"}


def _timeout_prop() -> dict:
    return {"type": "integer", "minimum": 1}


def _steps_prop() -> dict:
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["selector"],
            "properties": {
                "selector": {"type": "string"},
                "action": {"type": "string", "enum": ["type", "select", "click", "wait"]},
                "text": {"type": "string"},
                "value": {"type": "string"},
                "label": {"type": "string"},
                "index": {"type": "integer", "minimum": 0},
                "condition": {
                    "type": "string",
                    "enum": ["visible", "hidden", "attached", "detached", "enabled", "disabled"],
                },
                "timeout_ms": {"type": "integer", "minimum": 1},
                "click": {"type": "boolean"},
            },
        },
    }


def _browser_actions() -> list[ActionDefinition]:
    return [
        ActionDefinition(
            id="navigate",
            label="Navigate",
            description="Navigate the current browser tab to a URL.",
            handler=handle_navigate,
            parameters_schema=_base_schema(
                required=["url"],
                properties={
                    "url": {"type": "string"},
                    "timeout_ms": _timeout_prop(),
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="screenshot",
            label="Screenshot",
            description="Capture a screenshot of the current page or tab.",
            handler=handle_screenshot,
            parameters_schema=_base_schema(
                properties={
                    "full_page": {"type": "boolean"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="click",
            label="Click",
            description="Click an element matching a selector.",
            handler=handle_click,
            parameters_schema=_base_schema(
                required=["selector"],
                properties={
                    "selector": {"type": "string"},
                    "timeout_ms": _timeout_prop(),
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="type",
            label="Type Text",
            description="Type text into an input or editable element.",
            handler=handle_type,
            parameters_schema=_base_schema(
                required=["selector", "text"],
                properties={
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "timeout_ms": _timeout_prop(),
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="select",
            label="Select Option",
            description="Select an option in a select/combobox input.",
            handler=handle_select,
            parameters_schema=_base_schema(
                required=["selector"],
                properties={
                    "selector": {"type": "string"},
                    "value": {"type": "string"},
                    "label": {"type": "string"},
                    "index": {"type": "integer", "minimum": 0},
                    "timeout_ms": _timeout_prop(),
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="wait_for",
            label="Wait For",
            description="Wait for an element or state condition.",
            handler=handle_wait_for,
            parameters_schema=_base_schema(
                required=["selector"],
                properties={
                    "selector": {"type": "string"},
                    "condition": {
                        "type": "string",
                        "enum": ["visible", "hidden", "attached", "detached", "enabled", "disabled"],
                    },
                    "timeout_ms": _timeout_prop(),
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="get_value",
            label="Get Value",
            description="Read the current value of a form control.",
            handler=handle_get_value,
            parameters_schema=_base_schema(
                required=["selector"],
                properties={
                    "selector": {"type": "string"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="fill_form",
            label="Fill Form",
            description="Execute a sequence of form-filling steps.",
            handler=handle_fill_form,
            parameters_schema=_base_schema(
                required=["steps"],
                properties={
                    "steps": _steps_prop(),
                    "continue_on_error": {"type": "boolean"},
                    "verify": {"type": "boolean"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="press_key",
            label="Press Key",
            description="Press a keyboard key in the browser.",
            handler=handle_press_key,
            parameters_schema=_base_schema(
                required=["key"],
                properties={
                    "key": {"type": "string"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="scroll",
            label="Scroll",
            description="Scroll the page or an element.",
            handler=handle_scroll,
            parameters_schema=_base_schema(
                properties={
                    "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                    "amount": {"type": "integer", "minimum": 1},
                    "selector": {"type": "string"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="get_text",
            label="Get Text",
            description="Read text content from the page or a specific element.",
            handler=handle_get_text,
            parameters_schema=_base_schema(
                properties={
                    "selector": {"type": "string"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="snapshot",
            label="Snapshot",
            description="Capture an accessibility-style DOM snapshot.",
            handler=handle_snapshot,
            parameters_schema=_base_schema(
                properties={
                    "interactive_only": {"type": "boolean"},
                    "max_depth": {"type": "integer", "minimum": 1},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="reset",
            label="Reset",
            description="Reset the browser session state.",
            handler=handle_reset,
            parameters_schema=_base_schema(),
        ),
        ActionDefinition(
            id="tabs",
            label="List Tabs",
            description="List open browser tabs.",
            handler=handle_tabs,
            parameters_schema=_base_schema(),
        ),
        ActionDefinition(
            id="tab_open",
            label="Open Tab",
            description="Open a new browser tab.",
            handler=handle_tab_open,
            parameters_schema=_base_schema(
                properties={"url": {"type": "string"}},
            ),
        ),
        ActionDefinition(
            id="tab_focus",
            label="Focus Tab",
            description="Focus an existing browser tab.",
            handler=handle_tab_focus,
            parameters_schema=_base_schema(
                required=["tab_id"],
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
        ActionDefinition(
            id="tab_close",
            label="Close Tab",
            description="Close an existing browser tab.",
            handler=handle_tab_close,
            parameters_schema=_base_schema(
                required=["tab_id"],
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
        ActionDefinition(
            id="evaluate",
            label="Evaluate",
            description="Evaluate a browser-side JavaScript expression.",
            handler=handle_evaluate,
            parameters_schema=_base_schema(
                required=["expression"],
                properties={
                    "expression": {"type": "string"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="get_html",
            label="Get HTML",
            description="Get the current page HTML.",
            handler=handle_get_html,
            parameters_schema=_base_schema(
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
        ActionDefinition(
            id="get_cookies",
            label="Get Cookies",
            description="Read cookies for the current browser context.",
            handler=handle_get_cookies,
            parameters_schema=_base_schema(
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
        ActionDefinition(
            id="set_cookies",
            label="Set Cookies",
            description="Set cookies in the current browser context.",
            handler=handle_set_cookies,
            parameters_schema=_base_schema(
                required=["cookies"],
                properties={
                    "cookies": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name", "value"],
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                                "url": {"type": "string"},
                                "domain": {"type": "string"},
                                "path": {"type": "string"},
                                "httpOnly": {"type": "boolean"},
                                "secure": {"type": "boolean"},
                                "sameSite": {"type": "string", "enum": ["Strict", "Lax", "None"]},
                            },
                        },
                    }
                },
            ),
        ),
        ActionDefinition(
            id="console_logs",
            label="Console Logs",
            description="Read collected browser console logs.",
            handler=handle_console_logs,
            parameters_schema=_base_schema(
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
        ActionDefinition(
            id="network_intercept",
            label="Network Intercept",
            description="Add a network interception rule.",
            handler=handle_network_intercept,
            parameters_schema=_base_schema(
                required=["url_pattern"],
                properties={
                    "url_pattern": {"type": "string"},
                    "intercept_action": {"type": "string", "enum": ["log", "block", "mock"]},
                    "response_body": {"type": "string"},
                    "response_status": {"type": "integer"},
                    "tab_id": _tab_id_prop(),
                },
            ),
        ),
        ActionDefinition(
            id="network_logs",
            label="Network Logs",
            description="Read captured network request logs.",
            handler=handle_network_logs,
            parameters_schema=_base_schema(
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
        ActionDefinition(
            id="clear_network_intercepts",
            label="Clear Network Intercepts",
            description="Clear active network interception rules.",
            handler=handle_clear_network_intercepts,
            parameters_schema=_base_schema(
                properties={"tab_id": _tab_id_prop()},
            ),
        ),
    ]


MODULE = ModuleDefinition(
    name="browser",
    label="Browser",
    description=(
        "Playwright-based browser automation through one entry point. "
        "Use command=navigate, snapshot, screenshot, click, type, select, wait_for, "
        "get_value, fill_form, tabs, tab_open, tab_focus, tab_close, evaluate, "
        "network_intercept, and related browser operations while preserving the same "
        "session browser context across calls."
    ),
    icon="chrome",
    pinned=True,
    system=True,
    grouped_tool=True,
    actions=_browser_actions(),
)
