# Sentinel Desktop

Electron management shell for local Sentinel instances.

## Current Scope

- macOS Apple Silicon first.
- Unsigned development DMG.
- No Docker dependency for the desktop app.
- FastAPI backend runs as a managed local child process.
- Postgres + pgvector are expected as bundled resources in packaged builds.
- QEMU is detected from the host and guided through Homebrew installation.
- QEMU runtime image is built locally for now.
- `sentinel-cli.sh` remains supported and unchanged for terminal workflows.

## Development

From this directory:

```bash
npm install
npm run build
npm run dev
```

The dev app expects:

- existing frontend build at `apps/frontend/sentinel/dist`
- backend Python dependencies available to `python3`
- Postgres binaries available on `PATH`
- QEMU installed through Homebrew for runtime features

Build the frontend first if needed:

```bash
cd ../../frontend/sentinel
npm install
npm run build
```

## Runtime Artifacts

Desktop packaging is artifact-driven. The DMG build does not copy random local
Homebrew or virtualenv state. It only consumes tarballs listed in a manifest and
verified by SHA256.

For local development, build those tarballs explicitly:

```bash
npm run artifacts:local
```

This writes artifacts under `.desktop-artifacts/` and writes
`resources/runtime-manifest.local.json` with file URLs and checksums.
The local artifact builder requires `uv`, Postgres, and the Postgres `pgvector`
server extension. It normalizes archive ownership and rejects artifacts that
contain local home/repo/user strings.

Release builds should provide `SENTINEL_DESKTOP_RUNTIME_MANIFEST` pointing to a
hosted manifest with HTTPS artifact URLs and checksums.

## Packaging

```bash
npm run dist:mac
```

For a shell-only dev DMG without runtime resources:

```bash
npm run dist:mac:shell
```

The packaged app stores mutable data under Electron's app support directory:

- instance env/config
- Postgres data
- QEMU runtime images
- QEMU run overlays/logs
- workspaces
- backups

Nothing mutable should be stored inside the `.app` bundle.
