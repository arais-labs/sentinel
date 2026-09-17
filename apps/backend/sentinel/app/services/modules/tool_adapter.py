"""Adapt module definitions to the shared tool executor and approval contracts."""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.modules import ModulePermission
from app.services.modules.definitions import (
    ActionDefinition,
    ModuleDefinition,
    _normalize_permission_default_value,
)
from sentral.errors import ToolValidationError
from app.services.tools.executor import evaluate_approval_check, validate_payload
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRuntimeContext,
)

_GROUPED_ACTION_FIELD = "action"


def build_action_tool(
    action: ActionDefinition,
    *,
    module_name: str,
    module_description: str,
    action_count: int,
    approval_check: Any | None = None,
) -> ToolDefinition:
    """Convert this action into a ToolDefinition for the runtime executor."""
    if not action.handler:
        raise ValueError(f"Action {module_name}.{action.id} has no handler")

    if action_count == 1:
        name = module_name
        description = module_description or action.description or action.label
    else:
        name = f"{module_name}_{action.id}"
        description = action.description or action.label

    async def _execute(payload: dict[str, Any], runtime: ToolRuntimeContext) -> Any:
        return await _invoke_action_handler(
            handler=action.handler,
            payload=payload,
            runtime=runtime if action.requires_runtime_context else None,
        )

    return ToolDefinition(
        name=name,
        description=description,
        parameters_schema=action.get_parameters_schema() or {},
        execute=_execute,
        approval_check=approval_check,
    )


def build_module_tools(
    module: ModuleDefinition,
    *,
    session_factory: "async_sessionmaker[AsyncSession] | None" = None,
) -> list[ToolDefinition]:
    actions = [action for action in (module.actions or []) if action.handler]
    if not actions:
        return []
    if any(action.voice_only for action in actions) and not module.grouped_tool:
        raise ValueError(f"Module '{module.name}': voice_only actions require grouped_tool")

    checks = {
        action.id: _resolve_action_approval_check(
            module_name=module.name,
            action=action,
            session_factory=session_factory,
        )
        for action in actions
    }
    if module.grouped_tool:
        return [_build_grouped_tool(module, actions=actions, action_checks=checks)]

    return [
        build_action_tool(
            action,
            module_name=module.name,
            module_description=module.description,
            action_count=len(actions),
            approval_check=checks.get(action.id),
        )
        for action in actions
    ]


def _build_grouped_tool(
    module: ModuleDefinition,
    *,
    actions: list[ActionDefinition],
    action_checks: dict[str, Any | None],
) -> ToolDefinition:
    action_map = {action.id: action for action in actions}
    chat_actions = [action for action in actions if not action.voice_only]
    if not chat_actions:
        raise ValueError(f"Grouped module '{module.name}' needs at least one action for chats")
    schema = _build_grouped_parameters_schema(actions=chat_actions)
    voice_schema = (
        _build_grouped_parameters_schema(actions=actions)
        if len(chat_actions) < len(actions)
        else None
    )

    async def _execute(payload: dict[str, Any], runtime: ToolRuntimeContext) -> Any:
        action = _resolve_grouped_action(
            payload=payload,
            action_map=action_map,
        )
        if action.voice_only and str(runtime.agent_mode or "") != "voice":
            raise ToolValidationError(f"Action '{action.id}' is available to the Voice agent only")
        forwarded = dict(payload)
        forwarded.pop(_GROUPED_ACTION_FIELD, None)
        validate_payload(
            action.get_parameters_schema() or {},
            {key: value for key, value in forwarded.items() if not str(key).startswith("__")},
        )
        return await _invoke_action_handler(
            handler=action.handler,
            payload=forwarded,
            runtime=runtime if action.requires_runtime_context else None,
        )

    approval_check = _build_grouped_approval_check(
        module_name=module.name,
        action_map=action_map,
        action_checks=action_checks,
    )

    return ToolDefinition(
        name=module.name,
        description=module.description or module.label,
        parameters_schema=schema,
        execute=_execute,
        approval_check=approval_check,
        voice_parameters_schema=voice_schema,
    )


def _resolve_action_approval_check(
    *,
    module_name: str,
    action: ActionDefinition,
    session_factory: "async_sessionmaker[AsyncSession] | None",
) -> Any | None:
    default_level = _default_permission_level(
        approval=action.approval,
        permission_default=action.permission_default,
    )
    action_key = f"{module_name}.{action.id}"
    description = (action.description or action.label or action_key).strip()

    async def _evaluate() -> ToolApprovalEvaluation:
        level = default_level
        if session_factory is not None:
            level = await _load_permission_level(
                session_factory=session_factory,
                action_key=action_key,
                default_level=default_level,
            )
        if level == "deny":
            return ToolApprovalEvaluation.deny(
                f"Execution denied by module permission for action '{action_key}'."
            )
        if level == "approval":
            return ToolApprovalEvaluation.require(
                ToolApprovalRequirement(
                    action=action_key,
                    description=description,
                )
            )
        return ToolApprovalEvaluation.allow()

    if session_factory is None and default_level == "allow":
        return None
    return _evaluate


async def _load_permission_level(
    *,
    session_factory: "async_sessionmaker[AsyncSession]",
    action_key: str,
    default_level: str,
) -> str:
    async with session_factory() as db:
        result = await db.execute(
            select(ModulePermission).where(ModulePermission.action == action_key)
        )
        permission = result.scalars().first()
    level = str(getattr(permission, "level", "") or "").strip().lower()
    if level in {"allow", "approval", "deny"}:
        return level
    return default_level


def _default_permission_level(*, approval: bool, permission_default: str | None = None) -> str:
    normalized = _normalize_permission_default_value(permission_default)
    if normalized is not None:
        return normalized
    return "approval" if approval else "allow"


def _resolve_grouped_action(
    *,
    payload: dict[str, Any],
    action_map: dict[str, ActionDefinition],
) -> ActionDefinition:
    raw = payload.get(_GROUPED_ACTION_FIELD)
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError(f"Field '{_GROUPED_ACTION_FIELD}' must be a non-empty string")
    normalized = raw.strip().lower()
    action = action_map.get(normalized)
    if action is None:
        raise ToolValidationError(
            f"Field '{_GROUPED_ACTION_FIELD}' must be one of: "
            + ", ".join(sorted(action_map.keys()))
        )
    return action


def _build_grouped_parameters_schema(
    *,
    actions: list[ActionDefinition],
) -> dict[str, Any]:
    merged_properties: dict[str, Any] = {}
    shared_required: set[str] | None = None
    action_contracts: list[str] = []

    for action in actions:
        schema = action.get_parameters_schema() or {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        if _GROUPED_ACTION_FIELD in properties or _GROUPED_ACTION_FIELD in required:
            raise ValueError(
                f"Grouped action '{action.id}' may not define reserved field '{_GROUPED_ACTION_FIELD}'"
            )
        for key, value in properties.items():
            existing = merged_properties.get(key)
            if existing is not None and existing != value:
                raise ValueError(
                    f"Grouped module property conflict for '{key}' across action '{action.id}'"
                )
            merged_properties[key] = value
        shared_required = required if shared_required is None else shared_required & required
        optional = set(properties) - required
        action_contracts.append(
            f"{action.id}: {action.description or action.label or action.id}\n"
            f"  Required arguments: {', '.join(sorted(required)) or 'none'}.\n"
            f"  Optional arguments: {', '.join(sorted(optional)) or 'none'}."
        )

    shared_required_set = shared_required or set()
    required_fields = sorted(shared_required_set | {_GROUPED_ACTION_FIELD})
    properties = {
        _GROUPED_ACTION_FIELD: {
            "type": "string",
            "enum": sorted(action.id for action in actions),
            "description": (
                "Select an action using the action field. Supply only arguments listed for that action. "
                "Optional arguments may be needed under conditions described below.\n\n"
                + "\n\n".join(action_contracts)
            ),
        },
        **merged_properties,
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": required_fields,
        "properties": properties,
    }


def _build_grouped_approval_check(
    *,
    module_name: str,
    action_map: dict[str, ActionDefinition],
    action_checks: dict[str, Any | None],
) -> Any | None:
    if not any(action_checks.get(action_id) for action_id in action_map):
        return None

    async def _evaluate(
        payload: dict[str, Any],
        runtime: ToolRuntimeContext,
    ) -> ToolApprovalEvaluation:
        action = _resolve_grouped_action(
            payload=payload,
            action_map=action_map,
        )
        approval_check = action_checks.get(action.id)
        if approval_check is None:
            return ToolApprovalEvaluation.allow()

        forwarded = dict(payload)
        forwarded.pop(_GROUPED_ACTION_FIELD, None)
        return await evaluate_approval_check(
            approval_check=approval_check,
            payload=forwarded,
            runtime=runtime,
            tool_name=f"{module_name}.{action.id}",
        )

    return _evaluate


async def _invoke_action_handler(
    *,
    handler: Any,
    payload: dict[str, Any],
    runtime: ToolRuntimeContext | None,
) -> Any:
    handler_signature = inspect.signature(handler)
    positional_params = [
        parameter
        for parameter in handler_signature.parameters.values()
        if parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if "runtime" in handler_signature.parameters:
        result = handler(payload, runtime=runtime or ToolRuntimeContext())
    elif len(positional_params) >= 2:
        result = handler(payload, runtime or ToolRuntimeContext())
    else:
        result = handler(payload)
    if inspect.isawaitable(result):
        return await result
    return result
