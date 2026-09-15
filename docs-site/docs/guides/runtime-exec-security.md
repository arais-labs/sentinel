---
sidebar_position: 7
title: Workspace Runtime
---

# Workspace Runtime

The `runtime` tool executes commands in the session's attached workspace: a
persistent Linux container managed by Sentinel. On Apple silicon Macs, Sentinel
uses Apple Containerization. It does not execute agent commands in the host shell.

## Project paths

The selected project folder is mounted at **the same absolute path** inside the
container. For example, `/Users/developer/Projects/app` is the project directory on both
the Mac and in the container. New terminals start there. There is no `/workspace`
alias or per-tool path translation.

Absolute symlinks and Git worktree metadata can resolve naturally when their
targets are within the shared folder. A worktree whose metadata or files are
outside that folder is not automatically shared. Select a common parent folder
when the repository and its worktrees need to be available together.

The parent directories of the mount are container directories. Preserving the
project's path does not expose the host's home directory or neighboring projects.

## Filesystem and tool installation

The project folder is shared read/write with the host. The rest of the container's
filesystem is private to that workspace:

| Location | Storage |
|---|---|
| Selected project path | Shared host project folder |
| `/root` (`HOME`) | Workspace's persistent container disk |
| `/tmp` (`TMPDIR`) | Inside the workspace container |
| Installed packages and caches | Workspace's persistent container disk |
| Docker images and volumes | Workspace's private Docker engine |

Agents can install missing packages with the distribution’s package manager
(`apk` on Alpine, `apt` on Ubuntu or Debian) or put local executables in
`$HOME/.local/bin`, which is on the terminal PATH. These are Linux programs; a
macOS executable in the shared project does not become a Linux executable.
Container root is not host root. No host Docker socket is shared.

## Commands and terminals

`runtime` is a grouped model tool. Select its operation with the `action` argument.

- `workspace`: inspect the attachment and available workspaces.
- `exec`: run a shell command in a pane.
- `terminal_list`: inspect the session's windows and panes.
- `window_create`, `window_rename`, `window_close`: manage windows.
- `pane_split`, `pane_rename`, `pane_close`: manage panes.
- `pane_input`, `pane_read`: interact with a program or read pane output.

Use the pane IDs returned by `terminal_list`; the UI's selected pane is independent
of the agent's target. When multiple panes exist, `exec` requires `pane_id`.
Omit `cwd` to retain the shell's current directory. An explicit `cwd` refers to a
path inside the container.

Several sessions may share the same workspace files and installed tools. Each
session has its own tmux session; a workspace is not a separate checkout for each
agent. Coordinate concurrent edits or use separate Git worktrees.

## Lifecycle

Stopping a workspace stops its container and terminals while retaining its disk.
Removing it deletes its private container data and detaches linked sessions when
requested; the selected host project folder is preserved. Active agents must stop
before their workspace can be removed.
