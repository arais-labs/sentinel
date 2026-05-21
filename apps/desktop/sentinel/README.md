# Sentinel Desktop

Electron management shell for local Sentinel instances.

## Current Scope

- macOS Apple Silicon first.
- Unsigned development DMG.
- No Docker dependency for the desktop app.
- FastAPI backend runs as a managed local child process.
- Postgres + pgvector are bundled into packaged builds.
- Agent runtime execution is SSH-first and configured outside the desktop package for now.
- `sentinel-cli.sh` remains supported and unchanged for terminal workflows.

## Verification

From this directory:

```bash
npm run desktop:verify
```

This type-checks the Electron main, preload, renderer, and shared IPC code.

## Desktop Distribution Build

Desktop packaging is target-driven. The product is the DMG; native runtime files
are internal build inputs.

Runtime versions are pinned in `runtime.lock.json`. Build the desktop
distribution with:

```bash
npm run desktop:build -- --target macos-arm64
```

If `--target` is omitted, the build uses the current platform.

The build writes disposable intermediate files under:

```text
apps/desktop/sentinel/build/<target>/
```

The final DMG is written under:

```text
apps/desktop/sentinel/dist/
```

Clean generated desktop build outputs with:

```bash
npm run desktop:clean
```

The packaged app stores mutable data under Electron's app support directory:

- instance env/config
- Postgres data
- runtime workspaces
- backups

Nothing mutable should be stored inside the `.app` bundle.
