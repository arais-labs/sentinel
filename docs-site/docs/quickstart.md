---
sidebar_position: 2
title: Quickstart
---

# Quickstart

On macOS, install Xcode Command Line Tools and Homebrew. From the repository root:

```bash
make setup
make dev
```

Sentinel opens in Electron. On first launch:

1. Create or choose an instance from the instance picker.
2. Open Settings and configure a model connection.
3. In Workspaces, create a workspace on this computer or connect an SSH machine.
4. Open Sessions, start a conversation, and attach your workspace when needed.

The app manages its local services. Quit Electron or press Ctrl+C in the development
terminal to stop them. Your data is preserved. Development data and logs live under
`~/Library/Application Support/Sentinel Dev/`, separately from the installed app.

See [Installation](./guides/installation.md) for desktop packaging.
