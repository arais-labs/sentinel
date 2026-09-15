---
title: Runtime updates
---

# Runtime updates

The runtime is the machine-side service that runs workspaces. Updating it is
separate from reinstalling a workspace's development tools.

1. Open **Machines** and the runtime settings for the target machine.
2. Review the available update and any workspaces that need to restart.
3. Choose **Update** or **Update & restart**, as offered by the dialog.
4. Wait for completion, then reopen affected panes. Retry interrupted setup if requested.

Workspace files and installed tools are retained. Running commands and terminal
processes can stop during a runtime restart and are not automatically replayed.
The dialog distinguishes checking the current installation from changing it.

## Interrupted or failed updates

Reopen runtime settings and read the reported state. Sentinel keeps an update
journal and verifies the active release before resuming workspaces. A lost SSH
connection does not authorize another process to seize a live runtime's storage.

An unresponsive service that still owns its storage can block the update. Follow
the machine-specific recovery instruction shown in the dialog; deleting lock files
or the runtime store is not a safe substitute. A failed update preserves workspace
data and staged releases for diagnosis.

For the locking and activation contract, see [Workspace runtime architecture](../architecture/workspace-runtime.md).
