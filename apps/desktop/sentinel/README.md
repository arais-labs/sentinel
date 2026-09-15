# Sentinel desktop

Electron owns application windows, local backend transport, and workspace runtime
lifecycle. The web interface lives in `apps/frontend/sentinel`.

## Source layout

| Directory | Responsibility |
| --- | --- |
| `src/main/app/` | Windows, notifications, backend supervision, payload updates |
| `src/main/transport/` | Renderer, backend, and desktop stream connections |
| `src/main/workspace/` | Workspace lifecycle, tools, distributions, graphics |
| `src/preload/`, `src/shared/` | Renderer bridge and shared IPC contracts |
| `native/macos/` | Swift runtime with standard `Sources/` and `Tests/` |
| `native/graphics/guest/` | Graphics code installed into Linux workspaces |
| `native/graphics/patches/` | Graphics compatibility source |
| `scripts/build/` | Desktop, payload, native runtime, and graphics build tools |
| `scripts/dev/`, `scripts/release/` | Development setup, release/signature tools |
| `packaging/macos/` | Signing entitlements |
| `tests/unit/`, `tests/integration/`, `tests/fixtures/` | Checks and fixtures |
| `assets/` | Application icons |

Generated files belong in `build/` (intermediates and native dependencies),
`dist/` (compiled application), and `release/` (app bundles, DMGs, payloads,
release indexes). These directories are not source.

## Development and checks

From this directory on an Apple silicon Mac:

```sh
npm ci
npm run dev
```

Development prepares the pinned native runtime before starting Electron and the
renderer. The Swift runtime requires macOS 26 or newer.

```sh
npm test
npm run test:graphics-build
npm run build
npm run desktop:verify
```

Integration checks describe their requirements in their source. Some create
disposable VMs. Run the media check with:

```sh
npx electron tests/integration/pdf-preview.mjs
```

## Packaging

```sh
npm run desktop:build -- --target macos-arm64
npm run payload:build
```

`runtime.lock.json` pins bundled inputs. Builds verify checksums, stage private
dependencies, and check signing. The desktop build creates the shell app and DMG;
the payload build creates the separately updatable backend and frontend. Both
write distributable artifacts to `release/`.

For internal ad-hoc signing, set `SENTINEL_INTERNAL_BUILD=1` for the desktop build.
Public distribution requires the appropriate signing setup. Publishing is explicit
through `scripts/release/publish-release.mjs`.
