from __future__ import annotations

import asyncio

import pytest

from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.modules.tool_adapter import build_module_tools
from sentral.errors import ToolValidationError
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry, ToolRuntimeContext


def _run(coro):
    return asyncio.run(coro)


async def _handle_click(
    payload: dict[str, object], runtime: ToolRuntimeContext
) -> dict[str, object]:
    _ = runtime
    return {"clicked": True, "selector": payload["selector"]}


async def _handle_navigate(
    payload: dict[str, object], runtime: ToolRuntimeContext
) -> dict[str, object]:
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
    tool = build_module_tools(
        module,
    )[0]
    registry = ToolRegistry()
    registry.register(tool)
    return registry.get("browser")


def test_grouped_tool_schema_uses_action_discriminator():
    browser = _grouped_tool()

    assert browser is not None

    schema = browser.parameters_schema
    assert "command" not in schema["properties"]
    assert "action" in schema["properties"]
    assert "action" in schema["required"]
    assert "click" in schema["properties"]["action"]["enum"]
    assert "navigate" in schema["properties"]["action"]["enum"]
    assert "allOf" not in schema
    assert "oneOf" not in schema
    assert "anyOf" not in schema
    assert (
        "click: Click\n  Required arguments: selector."
        in schema["properties"]["action"]["description"]
    )

    assert (
        "navigate: Navigate\n  Required arguments: url."
        in schema["properties"]["action"]["description"]
    )


def test_all_grouped_builtins_expose_action_selector():
    from app.services.modules.builtins import get_builtins

    for module in get_builtins():
        if not module.grouped_tool:
            continue
        tools = build_module_tools(
            module,
        )
        assert len(tools) == 1, module.name
        schema = tools[0].parameters_schema
        assert "action" in schema["required"], module.name
        assert "command" not in schema["properties"], module.name
        assert schema["properties"]["action"]["enum"] == sorted(
            action.id for action in module.actions if action.handler
        )


def test_grouped_tool_rejects_old_command_selector_without_executing():
    tool = _grouped_tool()
    with pytest.raises(ToolValidationError, match="Field 'action'"):
        _run(tool.execute({"command": "click", "selector": "#submit"}, ToolRuntimeContext()))


def test_grouped_runtime_exposes_exec_pane_target_and_action_semantics():
    from app.services.modules.builtins.runtime.module import MODULE

    tool = build_module_tools(
        MODULE,
    )[0]
    description = tool.parameters_schema["properties"]["action"]["description"]
    exec_contract = next(block for block in description.split("\n\n") if block.startswith("exec:"))
    assert "Omit pane_id only when no pane exists" in exec_contract
    assert "Required arguments: shell_command." in exec_contract
    assert "Optional arguments: background, cwd, env, pane_id, timeout_seconds." in exec_contract
    for action in MODULE.actions:
        assert f"{action.id}: {action.description}" in description
    # Schema generation must not change the original per-action schema.
    exec_action = next(action for action in MODULE.actions if action.id == "exec")
    assert exec_action.get_parameters_schema()["required"] == ["shell_command"]


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
                "action": "click",
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
                    "action": "click",
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

    tools = build_module_tools(
        module,
    )

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "browser"
    assert "tab_id" in tool.parameters_schema["properties"]
    assert "tab_focus" in tool.parameters_schema["properties"]["action"]["enum"]
    assert "tab_close" in tool.parameters_schema["properties"]["action"]["enum"]
    description = tool.parameters_schema["properties"]["action"]["description"]
    assert description.count("Required arguments: tab_id.") == 2
    assert description.count("Optional arguments: none.") == 2


def test_grouped_tool_requires_explicit_selector_field():
    module = ModuleDefinition(
        name="git",
        label="Git",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="read",
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

    tool = build_module_tools(
        module,
    )[0]
    assert "action" in tool.parameters_schema["required"]
    with pytest.raises(ToolValidationError, match="Field 'action' must be a non-empty string"):
        _run(tool.execute({"cli_command": "git status"}, ToolRuntimeContext()))


def test_grouped_tool_ignores_fields_not_used_by_selected_action():
    calls: list[dict[str, object]] = []

    async def _handle_run(
        payload: dict[str, object], runtime: ToolRuntimeContext
    ) -> dict[str, object]:
        _ = runtime
        calls.append(payload)
        return payload

    async def _handle_accounts(
        payload: dict[str, object], runtime: ToolRuntimeContext
    ) -> dict[str, object]:
        _ = runtime
        calls.append(payload)
        return payload

    module = ModuleDefinition(
        name="git",
        label="Git",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="read",
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

    tool = build_module_tools(
        module,
    )[0]
    result = _run(tool.execute({"action": "accounts", "host": "github.com"}, ToolRuntimeContext()))

    assert result == {"host": "github.com"}
    assert calls == [{"host": "github.com"}]
