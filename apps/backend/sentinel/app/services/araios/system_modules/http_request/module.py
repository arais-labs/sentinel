from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_request


MODULE = ModuleDefinition(
    name="http_request",
    label="HTTP Request",
    description="Make outbound HTTP requests to external endpoints.",
    icon="globe",
    system=True,
    actions=[
        ActionDefinition(
            id="request",
            label="Send Request",
            description="Make outbound HTTP requests to external endpoints.",
            handler=handle_request,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["url"],
                "properties": {
                    "url": {"type": "string"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    },
                    "headers": {"type": "object"},
                    "body": {"type": "object"},
                    "timeout_seconds": {"type": "integer"},
                },
            },
        )
    ],
)
