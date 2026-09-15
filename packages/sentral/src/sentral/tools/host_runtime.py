from pathlib import Path

from sentral.errors import ToolValidationError


async def execute_host_action(processes, owner, action, payload):
    wait_ms = payload.get("yield_time_ms", 10000 if action == "exec" else 0)
    if type(wait_ms) is not int or not 0 <= wait_ms <= 30000:
        raise ToolValidationError("yield_time_ms must be an integer from 0 to 30000")
    if action == "exec":
        command = payload.get("shell_command")
        cwd = payload.get("cwd") or str(Path.home())
        env = payload.get("env", {})
        if not isinstance(command, str) or not command.strip() or "\0" in command:
            raise ToolValidationError(
                "shell_command must be a nonempty string without NUL characters"
            )
        if not isinstance(cwd, str) or "\0" in cwd or not Path(cwd).is_absolute():
            raise ToolValidationError("cwd must be an absolute host path")
        if not isinstance(env, dict) or any(
            not isinstance(k, str)
            or not k
            or "=" in k
            or "\0" in k
            or not isinstance(v, str)
            or "\0" in v
            for k, v in env.items()
        ):
            raise ToolValidationError("env must map valid environment names to strings")
        login = payload.get("login", True)
        if type(login) is not bool:
            raise ToolValidationError("login must be a boolean")
        return await processes.launch(
            owner, command=command, cwd=cwd, env=env, login=login, wait_ms=wait_ms
        )
    if action == "list":
        return {
            "processes": [
                {k: v for k, v in job.result(key).items() if k not in {"stdout", "stderr"}}
                for key, job in processes.jobs.items()
                if job.owner == owner
            ]
        }
    process_id = payload.get("process_id")
    if not isinstance(process_id, str):
        raise ToolValidationError("process_id is required")
    if action == "terminate":
        force = payload.get("force", False)
        if type(force) is not bool:
            raise ToolValidationError("force must be a boolean")
        return await processes.terminate(owner, process_id, force)
    return await processes.poll(owner, process_id, wait_ms)


WAIT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 30000,
    "description": "Time to wait for completion, never a kill timeout. 0 returns immediately in the background. A running result includes process_id for poll or terminate.",
}
ID = {
    "type": "string",
    "description": "Host process handle returned by exec or list in this session.",
}
ACTIONS = [
    (
        "exec",
        "Run Host Command",
        "Run a command on the host with full environment inheritance. Wait expiry continues in the background; it never kills the command.",
        ["shell_command"],
        {
            "shell_command": {
                "type": "string",
                "minLength": 1,
                "description": "Command string interpreted by the host's default shell; supports pipes and redirects.",
            },
            "cwd": {
                "type": "string",
                "description": "Absolute host working directory; defaults to the user's home.",
            },
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional overrides merged into the entire inherited environment.",
            },
            "login": {
                "type": "boolean",
                "description": "Load shell login/profile configuration; defaults to true.",
            },
            "yield_time_ms": WAIT,
        },
    ),
    (
        "poll",
        "Read Host Output",
        "Read cumulative output and status. Optional wait yields without stopping execution.",
        ["process_id"],
        {"process_id": ID, "yield_time_ms": WAIT},
    ),
    (
        "list",
        "List Host Processes",
        "List this session's retained host process handles and status, including commands started before an interrupted tool wait.",
        [],
        {},
    ),
    (
        "terminate",
        "Stop Host Process",
        "Explicitly stop a host process tree. Set force=true to force termination if a normal stop is ignored.",
        ["process_id"],
        {"process_id": ID, "force": {"type": "boolean"}},
    ),
]
