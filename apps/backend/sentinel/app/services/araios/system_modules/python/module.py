from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_run


MODULE = ModuleDefinition(
    name="python",
    label="Python",
    description=(
        "Run Python code in a persistent virtualenv inside the session's runtime container. "
        "Assign to `result` to return a value. Use venv_name to manage separate named envs."
    ),
    icon="code",
    pinned=True,
    system=True,
    actions=[
        ActionDefinition(
            id="run",
            label="Run Code",
            description=(
                "Run Python code in a persistent virtualenv inside the session's runtime container. "
                "Assign to `result` to return a value. Use venv_name to manage separate named envs."
            ),
            handler=handle_run,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["code"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute. Assign to `result` to return a value.",
                    },
                    "requirements": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "pip packages to install in the session venv before running",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Execution timeout in seconds (default 60, max 600)",
                    },
                    "venv_name": {
                        "type": "string",
                        "description": (
                            "Named venv to use (default: workspace/.venvs/default). "
                            "Pass a name to use workspace/.venvs/<name> instead."
                        ),
                    },
                },
            },
        )
    ],
)
