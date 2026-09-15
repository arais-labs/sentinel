---
title: Workspace runtime
---

# Workspace runtime

Sentinel embeds Apple's Containerization library in its native macOS helper.
The packaged app owns that helper and its assets; it does not manage Docker
Desktop, Colima, or Apple's shared `container` CLI. Workspace execution currently
requires a supported Apple Silicon Mac, locally or reached through SSH.

## Ownership and transport

```text
Electron → authenticated local service socket → workspace lifecycle
                                                  ↓
                                     native helper / remote SSH runtime
                                                  ↓
                                       persistent Linux workspace
```

The frontend requests workspace operations through application APIs. It does not
own VM lifecycle policy. Backend reloads and pane switches are separate from the
workspace service. Status checks observe state rather than booting a stopped VM.

Local and remote machines use the same guest setup and tool recipes. The
macOS-specific native implementation lives under
`apps/desktop/sentinel/native/macos`; shared orchestration lives in the desktop
and backend runtime services.

## Storage

- The selected project directory is the host folder shared into the guest.
- Each workspace has a private persistent Linux filesystem for its home, packages,
  caches, and nested Docker data.
- Kernel and image caches are machine runtime assets shared across workspaces.
- Instance databases, provider settings, and conversation attachments belong to
  Sentinel's application storage, not the guest disk.

The native helper holds an exclusive storage ownership lock. Two helpers cannot
open the same runtime store concurrently. Stopping or replacing a helper does not
justify deleting workspace files.

## Runtime update transaction

The updater stages a unique release, verifies its assets and executable signatures,
and acquires a separate update lock. A durable journal records activation intent,
previous/target releases, affected workspaces, and errors.

A responsive service stops approved workspaces through maintenance requests.
Activation switches the manifest atomically and hands the storage lock to the new
service without an unlocked ownership interval. Successful handshake, readiness,
and status checks precede workspace resumption.

An unresponsive lock owner blocks replacement. Compatible rollback is limited to
the supported update protocol and storage version before workspace resumption;
Sentinel does not perform an automatic force-kill or cross-version storage migration.
Interrupted commands and provisioning are not replayed blindly.

## Guest commands

`app/services/runtime/guest_commands` contains code executed inside the workspace:

| Directory | Responsibility |
| --- | --- |
| `common/files` | Paths, browsing/editing, bounded reads and uploads |
| `common/git` | Repository inspection and diffs |
| `common/workspace` | Workspace preparation and session state cleanup |
| `linux/browser` | Chromium process control |
| `linux/desktop` | X11 desktop control and screenshots |

Python resources use `.py`; shell scripts contain shell. A small loader supplies
shared Python helpers to the existing process transport without installing a
separate guest service. The `linux` directory describes the guest OS, not whether
the machine connection is local or remote.

## File content

One authenticated file endpoint serves downloads and native media previews. It
supports GET, HEAD, byte ranges, and validators for seeking. Guest reads wait for
acknowledgement between bounded blocks; directory ZIPs spool to temporary guest
disk instead of collecting their contents in backend memory. Cancellation closes
the reader. Electron forwards the existing application protocol and credentials;
credentials do not appear in file URLs.

The file boundary is the workspace container, not the selected project directory.
Absolute guest paths and symlinks can refer to other locations within that container.
