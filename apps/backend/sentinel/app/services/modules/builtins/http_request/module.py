from __future__ import annotations

from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from sentral.tools.http_request import handle_request, PARAMETERS_SCHEMA

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
            parameters_schema=PARAMETERS_SCHEMA,
        )
    ],
)
