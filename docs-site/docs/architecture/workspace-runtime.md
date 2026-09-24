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

## Linux boot and desktop sessions

```text
Apple Virtualization.framework / Containerization
  → Linux kernel + selected OCI root filesystem
    → distro init (systemd on Ubuntu/Debian; OpenRC on Alpine)
      → D-Bus, udev, login/seat services
        → greetd / PAM → ordinary-user desktop session
```

OCI describes how the root filesystem is packaged; it does not imply a Docker
daemon inside the workspace. Docker and k3s are optional development tools.
Compiler containers used to build runtime assets are separate from workspace
boot dependencies.

The graphical session and its applications run as the `sentinel` user, with
sudo available. Sentinel's administrative guest operations retain root access.
On Alpine/XFCE, the distribution's privileged Xorg wrapper starts the X server
with the required device permissions; this does not run desktop applications as
root. Other sessions use their native compositor/login contracts.

Desktop installation runs inside a live Linux system. Distribution package
scripts must be allowed to reload services and register new service accounts;
the offline image builder's service-start suppression does not apply here.
Stopping a desktop ends its owned graphical session, not the workspace or all
of that user's background services.

Existing Linux disks are not converted in place. Reinstall explicitly rebuilds
the Linux system disk, erasing guest-only files, packages and settings after
confirmation. The mounted host project is preserved. Runtime asset updates and
Linux system reinstallation are distinct operations.

### Known desktop limitation

In the tested Ubuntu 26.04 KDE Plasma session, Klipper can asynchronously restore
clipboard history over a new application selection immediately after login or
desktop restart. Protocol traces reproduced the fresh selection being cancelled
and replaced by persisted text. Sentinel keeps the distribution's clipboard
manager and history behavior; it does not disable history, add startup delays,
or ship a downstream KDE patch for this race. Clipboard qualification reports
this failure separately rather than treating a retry as a passing first copy.

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
| `linux/browser` | Selected Chromium-compatible browser process control |
| `linux/desktop` | Desktop control and screenshots through the runtime display contract |

Browser selection is independent of desktop and development stacks. Chromium is
the default; Firefox is available on every distro, and Google Chrome on Ubuntu
and Debian. Firefox selection also installs Chromium for CDP automation. The
desktop launcher and agent launcher share the selected installation for
Chromium/Chrome but never share browsing profiles. Agent execution resolves
`sentinel-automation-browser`; the desktop menu uses `sentinel-browser`.

Changing the selection installs additively and sets the desktop default without
removing previous browsers or profiles. Defaults are written on selection
changes, not every start, preserving later Linux-side customization. Ubuntu
Firefox uses Mozilla's signed native APT repository rather than the Snap
transition package; Debian uses Firefox ESR. Remote catalogs persist the
selection under the `workspace-browser-v1` capability.

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
