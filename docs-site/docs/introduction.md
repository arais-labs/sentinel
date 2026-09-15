---
slug: /
sidebar_position: 1
title: Introduction
---

# Sentinel

Sentinel brings AI chat, a live desktop, terminals, files, and agent tools into one
application. Work in a persistent Linux workspace on this Mac or an SSH machine,
watch the agent's actions, and steer it while it runs.

## Explore the workspace

| Capability | Start here |
| --- | --- |
| Desktop, terminals, files, and development tools | [Workspaces](./guides/workspaces.md) |
| Model connections and instance credentials | [Providers](./guides/providers.md) |
| Action review and session permission grants | [Approvals](./concepts/approvals.md) |
| Built-in tools, custom actions, and structured records | [Modules](./concepts/modules-and-permissions.md) |
| Persistent knowledge across conversations | [Memory](./concepts/memory.md) |
| Scheduled and event-driven tasks | [Triggers](./concepts/triggers.md) |
| Chat and steering through a bot | [Telegram](./guides/telegram.md) |
| Local terminal chat without the desktop app | [Standalone TUI](./guides/standalone-tui.md) |

## Instances and machines

One desktop app can host several instances. Each instance has its own database,
conversations, memory, module configuration, and encrypted provider settings.
Machines and their connection settings are shared by the application. A workspace
can be attached to several conversations; they share its files and desktop.

SQLite runs in the managed local backend; no separate database service is needed.
The desktop bridges application requests over a private socket. Provider and
integration requests still use the network and the accounts you configure.

## Get started

Use the [installation guide](./guides/installation.md) for a packaged build, or
[quickstart](./quickstart.md) to run from source. The desktop currently targets
Apple Silicon macOS. Read the release instructions accompanying your build for
signing and first-launch requirements.

Sentinel is open source under AGPL-3.0, built by [ARAIS](https://arais.us).
