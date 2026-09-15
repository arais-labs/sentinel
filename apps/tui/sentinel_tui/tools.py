"""Standalone ownership and approval around existing shared tools."""

import asyncio
from functools import partial
from uuid import uuid4

from jsonschema import validate
from sentral import ToolDefinition, ToolExecutionResult
from sentral.tools.host_processes import HostProcessManager
from sentral.tools.host_runtime import ACTIONS, execute_host_action
from sentral.tools.http_request import PARAMETERS_SCHEMA, handle_request


class Tools:
    def __init__(self, approve):
        self.processes = HostProcessManager()
        self.owner = uuid4().hex
        self.approve = approve
        self.definitions = {}
        for action, title, description, required, properties in ACTIONS:
            name = f"host_runtime_{action}"
            schema = {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            }
            handler = partial(execute_host_action, self.processes, self.owner, action)
            self.add(name, description, schema, handler, action in {"exec", "terminate"})
        self.add(
            "http_request",
            "Make an HTTP request",
            PARAMETERS_SCHEMA,
            handle_request,
            True,
        )

    def add(self, name, description, schema, handler, approval):
        async def execute(payload):
            try:
                validate(payload, schema)
                if approval and not await self.approve(name, payload):
                    return ToolExecutionResult(status="error", error="User denied this action")
                return ToolExecutionResult(status="ok", content=await handler(payload))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return ToolExecutionResult(status="error", error=str(exc))

        self.definitions[name] = ToolDefinition(name, description, schema, execute)

    def list_tools(self):
        return list(self.definitions.values())

    def get_tool(self, name):
        return self.definitions.get(name)

    async def close(self):
        await self.processes.close()
