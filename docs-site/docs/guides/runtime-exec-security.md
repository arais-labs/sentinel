---
sidebar_position: 7
title: Runtime Exec Security
---

# Runtime Exec Security

`runtime_exec` now has two explicit execution modes:

- `privilege=user` (default): confined mode
- `privilege=root`: unconfined mode, approval-gated

## Contract

### `privilege=user`

- Command runs inside a confined Bubblewrap sandbox.
- Filesystem is mounted read-only except for session runtime writable mounts.
- Writable locations are limited to:
  - the session workspace
  - `/tmp`
  - `/var/tmp`
  - `/dev/shm`
  - `/run/lock`
- Writes outside those mounts (for example `/app`, `/etc`, `/root`) are blocked.

### `privilege=root`

- Command runs unconfined (current runtime behavior).
- A tool approval is required before execution.
- If rejected, the tool fails with `User rejected action.`
- Approval matching uses `runtime_exec:root:<normalized command>`.

## Timeout behavior

Inline executions still enforce `timeout_seconds`.
On timeout, the result includes a hint to re-run with `detached=true` for long-running commands.

## Approval UX resiliency

If a pending tool call cannot be linked to a pending approval, Sentinel now surfaces an explicit linkage warning instead of silently spinning.
Operator action should be: stop/retry the run.

## Example payloads

User-confined (default):

```json
{
  "command": "pytest -q",
  "privilege": "user"
}
```

Root with approval:

```json
{
  "command": "apt-get update",
  "privilege": "root",
  "approval_timeout_seconds": 600
}
```
