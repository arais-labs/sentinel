from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import BROWSER_COMMAND_HANDLERS, handle_run
from .shared import BROWSER_SESSION_PROP


def _browser_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "command"],
        "properties": {
            **BROWSER_SESSION_PROP,
            "command": {
                "type": "string",
                "enum": sorted(BROWSER_COMMAND_HANDLERS.keys()),
                "description": (
                    "Browser command to run. Commands: navigate, screenshot, click, type, "
                    "select, wait_for, get_value, fill_form, press_key, scroll, get_text, "
                    "snapshot, reset, tabs, tab_open, tab_focus, tab_close, evaluate, "
                    "get_html, get_cookies, set_cookies, console_logs, network_intercept, "
                    "network_logs, clear_network_intercepts."
                ),
            },
            "tab_id": {"type": "string", "description": "Optional browser tab identifier"},
            "url": {"type": "string"},
            "timeout_ms": {"type": "integer", "minimum": 1},
            "full_page": {"type": "boolean"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "value": {"type": "string"},
            "label": {"type": "string"},
            "index": {"type": "integer", "minimum": 0},
            "condition": {
                "type": "string",
                "enum": ["visible", "hidden", "attached", "detached", "enabled", "disabled"],
            },
            "steps": {
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
            },
            "continue_on_error": {"type": "boolean"},
            "verify": {"type": "boolean"},
            "key": {"type": "string"},
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "minimum": 1},
            "interactive_only": {"type": "boolean"},
            "max_depth": {"type": "integer", "minimum": 1},
            "expression": {"type": "string"},
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
            },
            "url_pattern": {"type": "string"},
            "intercept_action": {"type": "string", "enum": ["log", "block", "mock"]},
            "response_body": {"type": "string"},
            "response_status": {"type": "integer"},
        },
    }


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
    actions=[
        ActionDefinition(
            id="run",
            label="Browser Command",
            description=(
                "Single browser entry point. Choose the operation with the command field. "
                "Recommended flow for web tasks: navigate -> snapshot(interactive_only=true) "
                "-> interact -> verify -> continue. Use fill_form for standard forms and "
                "tabs/tab_focus when popups or new tabs appear."
            ),
            type="standalone",
            handler=handle_run,
            parameters_schema=_browser_parameters_schema(),
        )
    ],
)
