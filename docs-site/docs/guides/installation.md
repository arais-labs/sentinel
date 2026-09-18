---
sidebar_position: 1
title: Installation
---

# Installation

## macOS desktop app

The desktop app is an Electron shell for running Sentinel locally on macOS. The current
build targets **Apple Silicon (arm64)**. Use the DMG and installation instructions
provided with the release you are testing.

It bundles Python and runs FastAPI as a managed local child process. SQLite and
sqlite-vec provide storage and memory search without a database service.
Pinned runtime versions live in `runtime.lock.json`. The app support directory
holds the application state and per-instance SQLite databases.

Use the DMG provided with your release. The version is defined in
`apps/desktop/sentinel/package.json`. Internal builds may be unsigned or
ad-hoc signed and can trigger Gatekeeper on first launch; follow the instructions
provided with that build.

To build the DMG yourself from `apps/desktop/sentinel/`:

```bash
npm run desktop:build -- --target macos-arm64
```

If `--target` is omitted, the build uses the current platform. The final DMG is written
to `apps/desktop/sentinel/release/`. See the desktop app README
and release instructions for verification and distribution details.

:::warning Desktop platform support
The desktop build is macOS Apple Silicon only — there is no x86_64, Linux, or Windows
build today. The machine runtime can be updated through Machines using the runtime shipped with the app. Signing and notarization status depends on the distributed build; a successful
local build does not establish that macOS will trust a downloaded copy.
:::

## Updating

Sentinel updates in two layers.

- **The service**, the backend and the web frontend, ships as a payload and updates in place from **Settings → Updates**. Choose a channel, check for updates, and install. Most releases only need this.
- **The app itself**, the desktop shell, only changes when a release adds something the shell must provide, such as new windows or new desktop integrations. Those releases need the new DMG.

Each release declares the oldest app version that can run its service. When the latest service on your channel needs a newer app than the one running, Updates shows a **Download Sentinel** button that opens the release page instead of installing, and the app posts one notification on launch. Install the DMG, and it brings its own matching service.

An older app keeps updating its service normally until a release raises that requirement, and anything it receives in the meantime runs without the newer desktop features. Apps older than 2.3.0 predate this check, so once they have the 2.3.0 service their next update is always offered as a DMG.

## Current limitations

- LLM provider credentials must be set per instance via the UI/API after install;
  environment variables are not supported.

## Local development

Run `make setup` and `make dev` from the repository root. The app opens with live
frontend and backend reload. Choose or create an instance on first launch.
Sentinel has no local user account or sign-in step.
