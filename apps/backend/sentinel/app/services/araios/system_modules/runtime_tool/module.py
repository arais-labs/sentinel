from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_run_root,
    handle_run_user,
    handle_terminal_close,
    handle_terminal_list,
    handle_terminal_read,
)


# Shared property definition: the grouped-tool builder requires identical
# shape across actions for any property name they have in common. Using a
# single canonical dict guarantees `runtime.user`, `runtime.root`, and
# `runtime.terminal_read` all advertise `terminal_id` with the same schema.
_TERMINAL_ID_PROPERTY: dict = {
    "type": "string",
    "pattern": "^[a-zA-Z0-9_-]{1,32}$",
    "description": (
        "Persistent terminal name. The id IS the user-visible label, so "
        "prefer short descriptive identifiers (e.g. 'build', 'tests', "
        "'dev-server', 'logs'). Each terminal is its own persistent bash "
        "session — `cd`/`export` in this terminal don't affect other "
        "terminals. For `runtime.user`: omit to use the default 'main' "
        "terminal (id '0'), the user's primary shared shell. Prefer omitting "
        "`terminal_id` for normal sequential work; only pick a custom id when "
        "you intentionally need a separate line of work, such as parallel tasks "
        "or a long-running command. `runtime.user` cannot use terminal_id='root'. "
        "For `runtime.root`: terminal_id is "
        "ignored; all elevated commands run through the single terminal 'root'. "
        "For `runtime.terminal_read`: required, must match an existing terminal."
    ),
}


def _run_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["shell_command"],
        "properties": {
            "shell_command": {
                "type": "string",
                "description": (
                    "Shell command to execute. Runs in a PERSISTENT bash session: "
                    "your `cd` and `export` are kept across calls in the same terminal, "
                    "just like a real shell. The user can see this line appear in the "
                    "tmux pane exactly as you typed it."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Optional working directory applied to THIS command only "
                    "(wrapped as `(cd <path> && <command>)`). Does NOT change the "
                    "shell's persistent cwd. To persistently move, run `cd <path>` "
                    "as a real `shell_command` instead."
                ),
            },
            "env": {
                "type": "object",
                "description": (
                    "Optional environment variables applied to THIS command only "
                    "(wrapped as `(export K=V; <command>)`). Do NOT persist across "
                    "calls. To set a sticky variable, run `export K=V` as a real "
                    "`shell_command`."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Execution timeout in seconds (default 300, max 1800).",
            },
            "background": {
                "type": "boolean",
                "description": (
                    "Run this command in the background and RETURN IMMEDIATELY with a "
                    "terminal handle. The command keeps running in its own persistent "
                    "tmux terminal (visible as a pill in the chat UI). You will receive "
                    "a NEW agent turn carrying the completed stdout/stderr/exit code "
                    "once it finishes — that notification IS the result, you do not "
                    "need to poll for it.\n"
                    "\n"
                    "How to behave after setting background=true:\n"
                    "  • Move on to other useful work in the SAME turn if there is any. "
                    "Background does NOT end your turn.\n"
                    "  • If you have nothing else useful to do, end the turn cleanly. "
                    "The completion notification will wake you back up.\n"
                    "  • Polling `runtime.terminal_read` just to check if it finished "
                    "is FORBIDDEN — the notification system handles that.\n"
                    "  • Only call `runtime.terminal_read` when intermediate progress "
                    "is itself the goal: e.g. you need to confirm a dev server printed "
                    "'Ready' before you start hitting it in this same turn.\n"
                    "\n"
                    "Cannot be combined with terminal_id='0' (the user's main shell). "
                    "Pick a descriptive named id ('build', 'tests', 'server'...) or "
                    "omit terminal_id to auto-allocate (`bg-<token>`)."
                ),
            },
            "terminal_id": _TERMINAL_ID_PROPERTY,
        },
    }


def _terminal_list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {},
    }


def _terminal_read_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["terminal_id"],
        "properties": {
            "terminal_id": _TERMINAL_ID_PROPERTY,
            "tail_bytes": {
                "type": "integer",
                "description": (
                    "How many trailing bytes of pane history to return "
                    "(min 256, max ~200000, default 8000)."
                ),
            },
        },
    }


def _terminal_close_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["terminal_id"],
        "properties": {
            "terminal_id": _TERMINAL_ID_PROPERTY,
        },
    }


MODULE = ModuleDefinition(
    name="runtime",
    label="Runtime",
    description=(
        "Execute shell commands inside the per-session runtime workspace via a "
        "PERSISTENT bash session — one tmux pane per `terminal_id`. Cwd, "
        "exported variables, and shell history are preserved across calls in "
        "the same terminal, just like a real shell: if you run `cd src` now, "
        "your next call already starts inside `src/`. The user sees every "
        "command appear in the same pane, including the output, and can type "
        "into the same shell in parallel — treat the default terminal "
        "('main') as the user's primary shared shell.\n"
        "\n"
        "Prefer the default terminal for normal sequential work by omitting "
        "`terminal_id`. Use a custom terminal only when you intentionally need "
        "a separate line of work, such as parallel tasks, a long-running "
        "process, or an isolated context.\n"
        "\n"
        "For git or GitHub operations, prefer the git tool instead of running "
        "`git` or `gh` commands in the runtime shell.\n"
        "\n"
        "Use `user` for the default runtime user. Use `root` for elevated "
        "execution; all root commands share one persistent root-owned terminal "
        "named 'root'.\n"
        "\n"
        "Scoped vs. persistent state: `cwd` and `env` on this tool apply ONLY "
        "to the single command (wrapped in a subshell). To persistently "
        "change directory or set a variable, run `cd` / `export` as real "
        "`shell_command`s — those changes carry into your next call.\n"
        "\n"
        "Long-running work: set `background=true` to fire-and-forget. The "
        "command runs in its own terminal; you keep working in the same turn "
        "if there's other useful work to do, and a completion notification "
        "fires a fresh agent turn with the final stdout/exit-code when it "
        "finishes. Do NOT poll `runtime.terminal_read` waiting for it — that "
        "is what the notification is for.\n"
        "\n"
        "Use a non-default `terminal_id` whenever you want isolation: a "
        "long-running process, parallel work in the same turn, or an isolated "
        "context. The name you pick is exactly what the user sees as the "
        "terminal pill label, so prefer short descriptive ids ('build', "
        "'tests', 'dev-server', 'logs')."
    ),
    icon="terminal",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="user",
            label="User Command",
            description="Run a shell command as the default runtime user inside the session workspace.",
            streaming=True,
            handler=handle_run_user,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="root",
            label="Root Command",
            description=(
                "Run a shell command as root inside the session runtime workspace. "
                "Always uses the single persistent root terminal named 'root'; "
                "any provided terminal_id is ignored."
            ),
            streaming=True,
            handler=handle_run_root,
            approval=True,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="terminal_list",
            label="List Terminals",
            description=(
                "List the active tmux-backed terminals for this chat session "
                "(id, label, busy flag, last command). Use sparingly — don't "
                "poll this; the chat UI shows the same info via pills."
            ),
            handler=handle_terminal_list,
            requires_runtime_context=True,
            parameters_schema=_terminal_list_parameters_schema(),
        ),
        ActionDefinition(
            id="terminal_read",
            label="Read Terminal",
            description=(
                "Return the recent ANSI-stripped pane output of a terminal. "
                "Call this ONLY when intermediate progress is itself "
                "load-bearing (e.g. confirm a server printed 'Ready'). Do "
                "not poll waiting for a background job to finish — the "
                "completion notification handles that."
            ),
            handler=handle_terminal_read,
            requires_runtime_context=True,
            parameters_schema=_terminal_read_parameters_schema(),
        ),
        ActionDefinition(
            id="terminal_close",
            label="Close Terminal",
            description=(
                "Permanently close a tmux-backed terminal: kills any process "
                "running in it, destroys the tmux session, and removes its "
                "pill from the chat UI. Use this when a terminal is no "
                "longer needed — finished build, stopped dev server, "
                "abandoned scratch shell — to keep the user's pill row "
                "uncluttered. Cannot close terminal '0' (the user's primary "
                "shared shell). PREFER reusing an existing idle terminal "
                "over closing-then-recreating: the named terminals you've "
                "already opened are persistent and meant to be reused "
                "across turns."
            ),
            handler=handle_terminal_close,
            requires_runtime_context=True,
            parameters_schema=_terminal_close_parameters_schema(),
        ),
    ],
)
