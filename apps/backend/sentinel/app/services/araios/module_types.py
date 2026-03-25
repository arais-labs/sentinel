"""Typed definitions for the unified module system.

Used by both system modules (code-defined) and user modules (DB-defined).
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.araios import AraiosPermission
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRuntimeContext,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_GROUPED_ACTION_FIELD = "command"


@dataclass
class FieldDefinition:
    """A field in a module's record schema."""
    key: str
    label: str
    type: str = "text"  # text|textarea|email|url|number|date|select|badge|tags|readonly
    required: bool = False
    options: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type}
        if self.required:
            d["required"] = True
        if self.options:
            d["options"] = self.options
        return d


@dataclass
class FieldsConfig:
    """Controls how records display in the UI."""
    titleField: str | None = None
    subtitleField: str | None = None
    badgeField: str | None = None
    filterField: str | None = None
    metaField: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "titleField": self.titleField,
            "subtitleField": self.subtitleField,
            "badgeField": self.badgeField,
            "filterField": self.filterField,
            "metaField": self.metaField,
        }.items() if v is not None}


@dataclass
class ParamDefinition:
    """A parameter for an action (flat format — legacy compat)."""
    key: str
    label: str
    type: str = "text"  # text|textarea|number
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "label": self.label, "type": self.type}
        if self.required:
            d["required"] = True
        return d


@dataclass
class ActionDefinition:
    """An executable action within a module."""
    id: str
    label: str
    description: str = ""
    type: str = "standalone"  # standalone|record
    parameters_schema: dict[str, Any] | None = None
    params: list[ParamDefinition] | None = None  # flat format — auto-converted to parameters_schema
    code: str | None = None       # user modules — Python executed in runtime
    handler: Any | None = None    # system modules — the actual async handler function
    streaming: bool = False
    approval: bool = False
    permission_default: str | None = None
    requires_runtime_context: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
        }
        if self.description:
            d["description"] = self.description
        if self.type != "standalone":
            d["type"] = self.type
        if self.parameters_schema:
            d["parameters_schema"] = self.parameters_schema
        if self.params:
            d["params"] = [p.to_dict() for p in self.params]
        if self.code:
            d["code"] = self.code
        if self.handler and not callable(self.handler):
            d["handler"] = self.handler
        if self.streaming:
            d["streaming"] = True
        if self.approval:
            d["approval"] = True
        return d

    def to_tool_definition(
        self,
        *,
        module_name: str,
        module_description: str,
        action_count: int,
        approval_check: Any | None = None,
    ) -> ToolDefinition:
        """Convert this action into a ToolDefinition for the agent loop."""
        if not self.handler:
            raise ValueError(f"Action {module_name}.{self.id} has no handler")

        if action_count == 1:
            name = module_name
            description = module_description or self.description or self.label
        else:
            name = f"{module_name}_{self.id}"
            description = self.description or self.label

        async def _execute(payload: dict[str, Any], runtime: ToolRuntimeContext) -> Any:
            return await _invoke_action_handler(
                handler=self.handler,
                payload=payload,
                runtime=runtime if self.requires_runtime_context else None,
            )

        return ToolDefinition(
            name=name,
            description=description,
            parameters_schema=self.get_parameters_schema() or {},
            execute=_execute,
            approval_check=approval_check,
        )

    def get_parameters_schema(self) -> dict[str, Any] | None:
        """Return parameters_schema, auto-converting flat params if needed."""
        if self.parameters_schema:
            return self.parameters_schema
        if not self.params:
            return None
        # Convert flat params to JSON Schema
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            prop: dict[str, Any] = {"type": "string"}
            if p.type == "number":
                prop["type"] = "number"
            if p.type == "textarea":
                prop["type"] = "string"
            properties[p.key] = prop
            if p.required:
                required.append(p.key)
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

@dataclass
class SecretDefinition:
    """A runtime-configurable secret for a module."""
    key: str
    label: str
    required: bool = False
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.required:
            d["required"] = True
        if self.hint:
            d["hint"] = self.hint
        return d


@dataclass
class ModuleDefinition:
    """A unified module definition — used by both system and user modules."""
    name: str
    label: str
    description: str = ""
    icon: str = "box"
    fields: list[FieldDefinition] | None = None
    fields_config: FieldsConfig | None = None
    actions: list[ActionDefinition] | None = None
    secrets: list[SecretDefinition] | None = None
    page_title: str | None = None
    page_content: str | None = None
    pinned: bool = False
    system: bool = False
    order: int = 100
    grouped_tool: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the same format as DB modules / API responses."""
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
            "fields": [f.to_dict() for f in self.fields] if self.fields else [],
            "fields_config": self.fields_config.to_dict() if self.fields_config else {},
            "actions": [a.to_dict() for a in self.actions] if self.actions else [],
            "secrets": [s.to_dict() for s in self.secrets] if self.secrets else [],
            "page_title": self.page_title,
            "page_content": self.page_content,
            "pinned": self.pinned,
            "system": self.system,
            "order": self.order,
            "grouped_tool": self.grouped_tool,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ModuleDefinition:
        """Deserialize from a dict (DB row or API payload)."""
        fields = None
        raw_fields = data.get("fields")
        if isinstance(raw_fields, list):
            fields = [
                FieldDefinition(
                    key=f["key"],
                    label=f.get("label", f["key"]),
                    type=f.get("type", "text"),
                    required=f.get("required", False),
                    options=f.get("options"),
                )
                for f in raw_fields
                if isinstance(f, dict) and "key" in f
            ]

        fields_config = None
        raw_fc = data.get("fields_config")
        if isinstance(raw_fc, dict):
            fields_config = FieldsConfig(
                titleField=raw_fc.get("titleField"),
                subtitleField=raw_fc.get("subtitleField"),
                badgeField=raw_fc.get("badgeField"),
                filterField=raw_fc.get("filterField"),
                metaField=raw_fc.get("metaField"),
            )

        actions = None
        raw_actions = data.get("actions")
        if isinstance(raw_actions, list):
            actions = [
                ActionDefinition(
                    id=a["id"],
                    label=a.get("label", a["id"]),
                    description=a.get("description", ""),
                    type=a.get("type", a.get("placement", "standalone")),
                    parameters_schema=a.get("parameters_schema"),
                    params=[
                        ParamDefinition(
                            key=p["key"],
                            label=p.get("label", p["key"]),
                            type=p.get("type", "text"),
                            required=p.get("required", False),
                        )
                        for p in a.get("params", [])
                        if isinstance(p, dict) and "key" in p
                    ] or None,
                    code=a.get("code"),
                    handler=a.get("handler"),
                    streaming=a.get("streaming", False),
                    approval=_normalize_approval_value(a.get("approval")),
                    permission_default=_normalize_permission_default_value(a.get("permission_default")),
                )
                for a in raw_actions
                if isinstance(a, dict) and "id" in a
            ]

        secrets = None
        raw_secrets = data.get("secrets")
        if isinstance(raw_secrets, list):
            secrets = [
                SecretDefinition(
                    key=s["key"],
                    label=s.get("label", s["key"]),
                    required=s.get("required", False),
                    hint=s.get("hint", ""),
                )
                for s in raw_secrets
                if isinstance(s, dict) and "key" in s
            ]

        return ModuleDefinition(
            name=data["name"],
            label=data.get("label", data["name"]),
            description=data.get("description", ""),
            icon=data.get("icon", "box"),
            fields=fields,
            fields_config=fields_config,
            actions=actions,
            secrets=secrets,
            page_title=data.get("page_title"),
            page_content=data.get("page_content"),
            pinned=data.get("pinned", False),
            system=data.get("system", False),
            order=data.get("order", 100),
            grouped_tool=data.get("grouped_tool", False),
        )

    def to_tool_definitions(
        self,
        *,
        session_factory: "async_sessionmaker[AsyncSession] | None" = None,
    ) -> list[ToolDefinition]:
        actions = [action for action in (self.actions or []) if action.handler]
        if not actions:
            return []

        checks = {
            action.id: _resolve_action_approval_check(
                module_name=self.name,
                action=action,
                session_factory=session_factory,
            )
            for action in actions
        }
        if self.grouped_tool:
            return [self._to_grouped_tool_definition(actions=actions, action_checks=checks)]

        return [
            action.to_tool_definition(
                module_name=self.name,
                module_description=self.description,
                action_count=len(actions),
                approval_check=checks.get(action.id),
            )
            for action in actions
        ]

    def _to_grouped_tool_definition(
        self,
        *,
        actions: list[ActionDefinition],
        action_checks: dict[str, Any | None],
    ) -> ToolDefinition:
        action_map = {action.id: action for action in actions}
        schema = _build_grouped_parameters_schema(
            actions=actions,
        )

        async def _execute(payload: dict[str, Any], runtime: ToolRuntimeContext) -> Any:
            action = _resolve_grouped_action(
                payload=payload,
                action_map=action_map,
            )
            forwarded = dict(payload)
            forwarded.pop(_GROUPED_ACTION_FIELD, None)
            _validate_payload_against_schema(forwarded, action.get_parameters_schema() or {})
            return await _invoke_action_handler(
                handler=action.handler,
                payload=forwarded,
                runtime=runtime if action.requires_runtime_context else None,
            )

        approval_check = _build_grouped_approval_check(
            module_name=self.name,
            action_map=action_map,
            action_checks=action_checks,
        )

        return ToolDefinition(
            name=self.name,
            description=self.description or self.label,
            parameters_schema=schema,
            execute=_execute,
            approval_check=approval_check,
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
                f"Execution denied by AraiOS permission for action '{action_key}'."
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
            select(AraiosPermission).where(AraiosPermission.action == action_key)
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


def _normalize_approval_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return value.get("required") is not False
    return False


def _normalize_permission_default_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"allow", "approval", "deny"}:
        return normalized
    return None


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
            f"Field '{_GROUPED_ACTION_FIELD}' must be one of: " + ", ".join(sorted(action_map.keys()))
        )
    return action


def _build_grouped_parameters_schema(
    *,
    actions: list[ActionDefinition],
) -> dict[str, Any]:
    merged_properties: dict[str, Any] = {}
    shared_required: set[str] | None = None
    action_required: dict[str, set[str]] = {}

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
        action_required[action.id] = required

    shared_required_set = shared_required or set()
    command_requirements: list[str] = []
    for action in actions:
        extra_required = sorted(action_required[action.id] - shared_required_set)
        required_text = ", ".join(extra_required) if extra_required else "no extra required fields"
        command_requirements.append(f"{action.id}: {required_text}")
    required_fields = sorted(shared_required_set | {_GROUPED_ACTION_FIELD})
    properties = {
        _GROUPED_ACTION_FIELD: {
            "type": "string",
            "enum": sorted(action.id for action in actions),
            "description": (
                "Select which internal action to execute. Options: "
                + ", ".join(sorted(action.id for action in actions))
                + ". Per-command required fields: "
                + "; ".join(command_requirements)
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
        return await _invoke_approval_check(
            approval_check=approval_check,
            payload=forwarded,
            runtime=runtime,
            error_context=f"Grouped action '{module_name}.{action.id}' approval check",
        )

    return _evaluate


async def _invoke_approval_check(
    *,
    approval_check: Any,
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
    error_context: str,
) -> ToolApprovalEvaluation:
    evaluator_signature = inspect.signature(approval_check)
    positional_params = [
        parameter
        for parameter in evaluator_signature.parameters.values()
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    ]
    if len(positional_params) >= 2:
        evaluated = approval_check(payload, runtime)
    elif positional_params:
        evaluated = approval_check(payload)
    else:
        evaluated = approval_check()
    if inspect.isawaitable(evaluated):
        evaluated = await evaluated
    if not isinstance(evaluated, ToolApprovalEvaluation):
        raise RuntimeError(f"{error_context} returned invalid response type.")
    return evaluated


def _validate_payload_against_schema(
    payload: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    additional_properties = schema.get("additionalProperties", True)
    user_payload = {k: v for k, v in payload.items() if not str(k).startswith("__")}

    missing = [field for field in required if field not in user_payload]
    if missing:
        raise ToolValidationError(f"Missing required field(s): {', '.join(missing)}")

    if not additional_properties:
        unknown = [field for field in user_payload.keys() if field not in properties]
        if unknown:
            raise ToolValidationError(f"Unknown field(s): {', '.join(unknown)}")

    for field_name, field_schema in properties.items():
        if field_name not in user_payload:
            continue
        _validate_field(field_name, user_payload[field_name], field_schema)


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
        if parameter.kind in {
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

def _validate_field(field_name: str, value: Any, field_schema: dict[str, Any]) -> None:
    expected_type = field_schema.get("type")
    if expected_type:
        if expected_type == "string" and not isinstance(value, str):
            raise ToolValidationError(f"Field '{field_name}' must be a string")
        if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            raise ToolValidationError(f"Field '{field_name}' must be an integer")
        if expected_type == "boolean" and not isinstance(value, bool):
            raise ToolValidationError(f"Field '{field_name}' must be a boolean")
        if expected_type == "object" and not isinstance(value, dict):
            raise ToolValidationError(f"Field '{field_name}' must be an object")
        if expected_type == "array" and not isinstance(value, list):
            raise ToolValidationError(f"Field '{field_name}' must be an array")

    enum = field_schema.get("enum")
    if enum and value not in enum:
        raise ToolValidationError(
            f"Field '{field_name}' must be one of: {', '.join(map(str, enum))}"
        )
