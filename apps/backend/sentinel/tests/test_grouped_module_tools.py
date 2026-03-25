from __future__ import annotations

import asyncio

import pytest

from app.services.araios.module_types import ActionDefinition, ModuleDefinition
from app.services.tools.executor import ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolRegistry, ToolRuntimeContext


def _run(coro):
    return asyncio.run(coro)


async def _handle_click(payload: dict[str, object], runtime: ToolRuntimeContext) -> dict[str, object]:
    _ = runtime
    return {"clicked": True, "selector": payload["selector"]}


async def _handle_navigate(payload: dict[str, object], runtime: ToolRuntimeContext) -> dict[str, object]:
    _ = runtime
    return {"navigated": True, "url": payload["url"]}


def _grouped_tool():
    module = ModuleDefinition(
        name="browser",
        label="Browser",
        description="Grouped browser tool",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="click",
                label="Click",
                handler=_handle_click,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["selector"],
                    "properties": {
                        "selector": {"type": "string"},
                    },
                },
                requires_runtime_context=True,
            ),
            ActionDefinition(
                id="navigate",
                label="Navigate",
                handler=_handle_navigate,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["url"],
                    "properties": {
                        "url": {"type": "string"},
                    },
                },
                requires_runtime_context=True,
            ),
        ],
    )
    tool = module.to_tool_definitions()[0]
    registry = ToolRegistry()
    registry.register(tool)
    return registry.get("browser")


def test_grouped_tool_schema_uses_command_discriminator():
    browser = _grouped_tool()

    assert browser is not None

    schema = browser.parameters_schema
    assert "command" in schema["properties"]
    assert "command" in schema["required"]
    assert "click" in schema["properties"]["command"]["enum"]
    assert "navigate" in schema["properties"]["command"]["enum"]
    assert "allOf" not in schema
    assert "oneOf" not in schema
    assert "anyOf" not in schema
    assert "click: selector" in schema["properties"]["command"]["description"]
    assert "navigate: url" in schema["properties"]["command"]["description"]


def test_grouped_tool_dispatches_to_internal_action_handler():
    registry = ToolRegistry()
    browser = _grouped_tool()
    assert browser is not None
    registry.register(browser)
    executor = ToolExecutor(registry)

    result, _ = _run(
        executor.execute(
            "browser",
            {
                "command": "click",
                "selector": "#submit",
            },
            runtime=ToolRuntimeContext(),
        )
    )

    assert result["clicked"] is True
    assert result["selector"] == "#submit"


def test_grouped_tool_validates_selected_action_payload():
    registry = ToolRegistry()
    browser = _grouped_tool()
    assert browser is not None
    registry.register(browser)
    executor = ToolExecutor(registry)

    with pytest.raises(ToolValidationError, match="Unknown field\\(s\\): url"):
        _run(
            executor.execute(
                "browser",
                {
                    "command": "click",
                    "selector": "#submit",
                    "url": "https://example.com",
                },
                runtime=ToolRuntimeContext(),
            )
        )


def test_grouped_tool_allows_shared_field_schema_across_actions():
    shared_tab_id = {"type": "string", "description": "Optional browser tab identifier"}
    module = ModuleDefinition(
        name="browser",
        label="Browser",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="tab_focus",
                label="Focus Tab",
                handler=_handle_click,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tab_id"],
                    "properties": {
                        "tab_id": shared_tab_id,
                    },
                },
                requires_runtime_context=True,
            ),
            ActionDefinition(
                id="tab_close",
                label="Close Tab",
                handler=_handle_click,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tab_id"],
                    "properties": {
                        "tab_id": shared_tab_id,
                    },
                },
                requires_runtime_context=True,
            ),
        ],
    )

    tools = module.to_tool_definitions()

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "browser"
    assert "tab_id" in tool.parameters_schema["properties"]
    assert "tab_focus" in tool.parameters_schema["properties"]["command"]["enum"]
    assert "tab_close" in tool.parameters_schema["properties"]["command"]["enum"]


def test_grouped_tool_requires_explicit_selector_field():
    module = ModuleDefinition(
        name="git_exec",
        label="Git Exec",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="run_read",
                label="Run Read",
                handler=_handle_click,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["cli_command"],
                    "properties": {
                        "cli_command": {"type": "string"},
                    },
                },
                requires_runtime_context=True,
            ),
            ActionDefinition(
                id="accounts",
                label="Accounts",
                handler=_handle_navigate,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "host": {"type": "string"},
                    },
                },
            ),
        ],
    )

    tool = module.to_tool_definitions()[0]
    assert "command" in tool.parameters_schema["required"]
    with pytest.raises(ToolValidationError, match="Field 'command' must be a non-empty string"):
        _run(tool.execute({"cli_command": "git status"}, ToolRuntimeContext()))


def test_grouped_tool_ignores_fields_not_used_by_selected_action():
    calls: list[dict[str, object]] = []

    async def _handle_run(payload: dict[str, object], runtime: ToolRuntimeContext) -> dict[str, object]:
        _ = runtime
        calls.append(payload)
        return payload

    async def _handle_accounts(payload: dict[str, object], runtime: ToolRuntimeContext) -> dict[str, object]:
        _ = runtime
        calls.append(payload)
        return payload

    module = ModuleDefinition(
        name="git_exec",
        label="Git Exec",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="run_read",
                label="Run Read",
                handler=_handle_run,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["cli_command"],
                    "properties": {
                        "cli_command": {"type": "string"},
                    },
                },
                requires_runtime_context=True,
            ),
            ActionDefinition(
                id="accounts",
                label="Accounts",
                handler=_handle_accounts,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "host": {"type": "string"},
                    },
                },
            ),
        ],
    )

    tool = module.to_tool_definitions()[0]
    result = _run(tool.execute({"command": "accounts", "host": "github.com"}, ToolRuntimeContext()))

    assert result == {"host": "github.com"}
    assert calls == [{"host": "github.com"}]
