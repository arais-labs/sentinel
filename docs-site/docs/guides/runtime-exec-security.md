---
sidebar_position: 7
title: Runtime Exec Security
---

# Runtime Exec Security

The agent runs shell commands through the **`runtime`** system module. Every
command executes on the instance's managed **SSH/tmux runtime target**, inside an
OS-level sandbox: **Bubblewrap** on Linux runtimes and **macOS Seatbelt**
(`sandbox-exec`) on macOS runtimes. There is no unconfined or "root" execution
mode — all `runtime` commands run confined.

Each Sentinel instance has its own runtime target and its own per-session
workspace, so command execution is isolated per instance. See
[Multi-instance](./multi-instance.md) for how instances are scoped.

## The `runtime` module

`runtime` is a grouped system tool with four actions:

| Action | What it does |
|---|---|
| `runtime.user` | Run a shell command in the session workspace |
| `runtime.terminal_list` | List active tmux-backed terminals for the session |
| `runtime.terminal_read` | Read recent (ANSI-stripped) output from terminals |
| `runtime.terminal_close` | Close one or more terminals |

Commands run inside the session's tmux-backed sandbox on the runtime target. If
no runtime SSH target is configured for the instance, the tool fails with
`Runtime SSH target is not configured.`

### `runtime.user` parameters

| Field | Required | Notes |
|---|---|---|
| `shell_command` | yes | The command to run |
| `cwd` | no | Working directory inside the sandbox, e.g. `/workspace` |
| `terminal_id` | no | Defaults to `0`, the prioritized main terminal |
| `timeout_seconds` | no | Defaults to `300`, capped at `3600` |
| `background` | no | Run in a non-primary terminal and return immediately |
| `env` | no | Extra environment variables for this command |

## Sandbox contract

All `runtime.user` commands run confined. The sandbox is chosen automatically
from the detected runtime OS:

- **Linux runtime** → Bubblewrap (`bwrap`)
- **macOS runtime** → Seatbelt (`sandbox-exec`)

If the runtime is neither a Linux host with Bubblewrap nor a macOS host with
`sandbox-exec`, the runtime refuses to execute and reports
`runtime_sandbox_unavailable` rather than running unconfined.

### Filesystem confinement

The filesystem is read-only by default; only the session's own paths are
writable. Writes outside the session workspace (for example `/app`, `/etc`,
`/root`, system directories) are blocked.

**Linux (Bubblewrap).** System roots such as `/usr` and `/etc` are mounted
read-only, `/tmp` is a fresh tmpfs, and only the session paths are bound
read-write:

| Path inside the sandbox | Writable |
|---|---|
| `/workspace` (session workspace) | yes |
| `/state` (session state, incl. `HOME`) | yes |
| `/tmp/sentinel` (`TMPDIR`) | yes |
| everything else | no |

**macOS (Seatbelt).** The profile is `(deny default)` with explicit allows.
File writes are permitted only under the session's workspace tree; system roots
(`/usr/lib`, `/usr/share`, the frameworks, and tool roots like Homebrew or the
Command Line Tools) are read-only.

### What is allowed

- `process-exec` / `process-fork` so the shell can spawn child processes.
- Outbound network access (the sandbox does **not** block network egress).
- Read-only access to system libraries and resolved tool roots (`bash`, `tmux`,
  `git`, `gh`, `ssh`).

## Background commands

Pass `background=true` to run a long-lived command in a non-primary terminal and
return immediately; the call yields a `job_id` and `status: "running"`, and a
runtime job-completed event is broadcast when it finishes. Background commands
cannot use terminal `0` (the main terminal) — supply a `terminal_id` or one is
generated. Use `runtime.terminal_read` to inspect progress (not to busy-poll for
completion) and `runtime.terminal_close` to clean up.

## Timeout behavior

Foreground executions enforce `timeout_seconds` (default `300`, max `3600`). For
work that may exceed the timeout, prefer `background=true` rather than a long
inline timeout, then read the terminal or wait for the completion event.

## Approvals

The `runtime` module's actions are not approval-gated by default — there is no
per-command approval prompt for `runtime.user`. Approvals in Sentinel are a
separate, module-action-level mechanism (the three-level `allow` / `approval` /
`deny` system) and apply to whichever module actions are configured to require
them. See [Permissions](./permissions.md) for how levels are resolved and how
approval-gated actions return `202 Accepted`.

:::warning Current limitations

- **No privileged / unconfined mode.** Earlier builds exposed a `privilege=root`
  unconfined path on a `runtime_exec` tool; that no longer exists. The current
  `runtime` module only offers the sandboxed `runtime.user` action. Commands that
  genuinely need elevated host access are not supported through the agent
  runtime.
- **Sandbox protects the filesystem, not the network.** Outbound network access
  is allowed inside the sandbox. Treat anything the agent can reach over the
  network as in scope.
- **macOS Seatbelt is applied at the runtime, not at the SSH transport.** The
  sandbox confines the executed command on the target host; SSH key/password auth
  to the runtime is a separate security boundary.
- **No RBAC.** All users of an instance share the same module permission levels;
  per-user runtime restrictions are not available.

:::

## Example payloads

Run a command in the session workspace (foreground):

```json
{
  "shell_command": "pytest -q",
  "cwd": "/workspace",
  "timeout_seconds": 300
}
```

Run a long-lived command in the background:

```json
{
  "shell_command": "npm run dev",
  "terminal_id": "dev",
  "background": true
}
```
