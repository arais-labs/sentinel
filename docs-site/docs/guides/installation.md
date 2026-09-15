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

## Current limitations

- LLM provider credentials must be set per instance via the UI/API after install;
  environment variables are not supported.

## Local development

Run `make setup` and `make dev` from the repository root. The app opens with live
frontend and backend reload. Choose or create an instance on first launch.
Sentinel has no local user account or sign-in step.
