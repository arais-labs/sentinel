from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ApprovalDefinition, ModuleDefinition

from .handlers import (
    ALLOWED_MODULE_DISCOVERY_COMMANDS,
    _modules_discovery_approval_evaluator,
    handle_run,
)


def _modules_discovery_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {"type": "string", "enum": list(ALLOWED_MODULE_DISCOVERY_COMMANDS)},
            "name": {"type": "string"},
            "label": {"type": "string"},
            "description": {"type": "string"},
            "icon": {"type": "string"},
            "fields": {"type": "array"},
            "fields_config": {"type": "object"},
            "actions": {"type": "array"},
            "secrets": {"type": "array"},
            "page_title": {"type": "string"},
            "module": {"type": "string"},
            "record_id": {"type": "string"},
            "data": {"type": "object"},
            "action_id": {"type": "string"},
            "params": {"type": "object"},
            "session_id": {"type": "string"},
        },
    }


MODULE = ModuleDefinition(
    name="modules_discovery",
    label="Modules & Records",
    description="Unified araiOS module engine for module CRUD, record CRUD, and action execution.",
    icon="boxes",
    pinned=False,
    system=True,
    actions=[
        ActionDefinition(
            id="run",
            label="Modules & Records",
            description="Unified araiOS module engine entry point.",
            handler=handle_run,
            approval=ApprovalDefinition(
                mode="conditional",
                evaluator=_modules_discovery_approval_evaluator,
            ),
            parameters_schema=_modules_discovery_parameters_schema(),
        )
    ],
)
