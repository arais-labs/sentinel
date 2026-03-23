"""Typed definitions for the unified module system.

Used by both system modules (code-defined) and user modules (DB-defined).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
class ApprovalDefinition:
    """Approval configuration for a system-module action."""

    mode: str
    evaluator: Any | None = None
    waiter: Any | None = None
    action: str | None = None
    description: str | None = None
    timeout_seconds: int | None = None
    metadata: dict[str, Any] | None = None
    requested_by: str | None = None
    match_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"mode": self.mode}
        if self.action:
            data["action"] = self.action
        if self.description:
            data["description"] = self.description
        if self.timeout_seconds is not None:
            data["timeout_seconds"] = self.timeout_seconds
        if self.metadata:
            data["metadata"] = self.metadata
        if self.requested_by:
            data["requested_by"] = self.requested_by
        if self.match_key:
            data["match_key"] = self.match_key
        return data

    @staticmethod
    def from_value(value: Any) -> "ApprovalDefinition" | dict[str, Any] | None:
        if value is None or isinstance(value, ApprovalDefinition):
            return value
        if not isinstance(value, dict):
            return None
        evaluator = value.get("evaluator")
        if callable(evaluator):
            return ApprovalDefinition(
                mode=str(value.get("mode", "none")),
                evaluator=evaluator,
                waiter=value.get("waiter") if callable(value.get("waiter")) else None,
                action=value.get("action"),
                description=value.get("description"),
                timeout_seconds=value.get("timeout_seconds"),
                metadata=value.get("metadata"),
                requested_by=value.get("requested_by"),
                match_key=value.get("match_key"),
            )
        return value


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
    approval: ApprovalDefinition | dict[str, Any] | None = None

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
            if isinstance(self.approval, ApprovalDefinition):
                d["approval"] = self.approval.to_dict()
            elif isinstance(self.approval, dict):
                d["approval"] = {k: v for k, v in self.approval.items() if not callable(v)}
        return d

    def to_tool_definition(
        self,
        *,
        module_name: str,
        module_description: str,
        action_count: int,
        approval_gate: Any = None,
    ) -> Any:
        """Convert this action into a ToolDefinition for the agent loop."""
        from app.services.tools.registry import ToolDefinition

        if not self.handler:
            raise ValueError(f"Action {module_name}.{self.id} has no handler")

        if action_count == 1:
            name = module_name
            description = module_description or self.description or self.label
        else:
            name = f"{module_name}_{self.id}"
            description = self.description or self.label

        return ToolDefinition(
            name=name,
            description=description,
            parameters_schema=self.get_parameters_schema() or {},
            execute=self.handler,
            approval_gate=approval_gate,
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
                    approval=ApprovalDefinition.from_value(a.get("approval")),
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
        )
