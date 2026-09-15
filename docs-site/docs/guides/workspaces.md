---
title: Workspaces
---

# Workspaces

A workspace is a persistent Linux environment attached to a machine. Several chats
can use one workspace: project files, installed tools, and the desktop are shared.
Choose **Attach** in a chat to select its workspace.

## Create a workspace

1. Open **Workspaces → New workspace** and enter a name.
2. In **Location**, choose this computer or a configured SSH machine and its project folder.
3. In **OS**, select Alpine Linux, Ubuntu, or Debian.
4. Choose stacks and individual tools, then set CPU, memory, and disk resources.
5. Start the workspace and follow preparation progress on its card.

The OS selection is separate from location. The supported distributions expose the
same stack and tool catalog; installation uses each distribution's package recipes.
Changing the distribution requires a new workspace. The machine's runtime must
support the selected distribution. Arch is not currently offered.

## What persists

| Data | Location and lifetime |
| --- | --- |
| Project files | The selected machine folder, shared at the same absolute path in the guest. Removing the workspace does not remove this folder. |
| Installed tools, guest home, caches, Docker data | The workspace's private Linux disk. These survive a normal stop/start. |
| Conversations and attachments | The selected Sentinel instance. Closing a pane does not delete a conversation. |
| Desktop applications | Shared by chats attached to the workspace. Closing the viewer leaves them running; stopping the workspace stops its processes. |

A workspace is the execution boundary. A chat or sub-agent attached to it is not a
separate filesystem sandbox. Choose different workspaces when work must not share
files or processes.

## Desktop, terminals, and files

Open panes from the chat sidebar. Terminal sessions use tmux inside the guest;
reconnecting attaches to their existing state. Desktop input and the agent's
computer tools operate the same graphical session, so avoid simultaneous actions
that interfere with each other.

Drop files into the file pane or use its upload button. File previews support text,
PDF, images, audio, video, and sandboxed HTML. Media support depends on Chromium's
codecs; an unsupported format can still be downloaded. Large files use a streaming
transfer; HTML preview is limited to 5 MiB. The source viewer has a separate text
preview limit and reports truncation.

## When a workspace is unavailable

Read the error on its card. Check the machine connection first, then use the
available **Retry** or **Recover** action for that workspace. Recovery may stop
running commands; review the confirmation before proceeding. Do not delete its
project folder or private disk to repair a connection.

Machine runtime maintenance is a separate operation. See
[Runtime updates](./runtime-updates.md) and [execution security](./runtime-exec-security.md).
