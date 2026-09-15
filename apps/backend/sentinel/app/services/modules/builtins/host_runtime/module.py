import platform

from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from sentral.tools.host_processes import default_shell
from app.services.host_runtime import host_processes
from sentral.tools.host_runtime import ACTIONS, execute_host_action
from app.services.tools.runtime_context import require_session_id


def handler(action):
    async def execute(payload, runtime):
        owner = (runtime.instance_name or "", str(require_session_id(runtime)))
        return await execute_host_action(host_processes, owner, action, payload)

    return execute


MODULE = ModuleDefinition(
    name="host_runtime",
    label="Host Runtime",
    icon="terminal",
    system=True,
    grouped_tool=True,
    description=f"Host OS: {platform.system()}; default shell: {default_shell()}. Execute commands directly on the machine running Sentinel, as its OS user, with the full inherited environment. No attached workspace or container is needed. Uses the host's default shell (PowerShell on Windows); commands must match that shell and OS. Exec requires approval by default. Wait expiry leaves commands running and returns a process_id; poll later rather than repeatedly polling. Output is a cumulative bounded tail, not incremental. Handles belong to this session, are retained until capacity eviction after completion, and do not survive backend restarts. Processes are stopped on backend shutdown. No persistent terminal, interactive stdin, or automatic execution deadline. Use terminate explicitly to stop a process.",
    actions=[
        ActionDefinition(
            id=action,
            label=label,
            description=description,
            handler=handler(action),
            requires_runtime_context=True,
            approval=action in {"exec", "terminate"},
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            },
        )
        for action, label, description, required, properties in ACTIONS
    ],
)
