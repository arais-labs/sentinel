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
| `scripts/packaging/` | Desktop, payload, native runtime, and graphics build tools |
| `scripts/dev/`, `scripts/publishing/` | Development setup, release/signature tools |
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

Development sets the stable application identity **Sentinel Dev** before Electron
is ready. This isolates both its data directory and its macOS Keychain service
(`Sentinel Dev Safe Storage`) from the packaged application. Changing only
`userData` does not isolate Keychain encryption. Do not rename either application
identity or delete its Safe Storage entry during app cleanup: existing encrypted
data depends on that key. Startup never replaces an unreadable encryption key.

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
through `scripts/publishing/publish-release.mjs`.

Pull requests build an ad-hoc signed macOS installer and its matching payload.
Download `sentinel-macos-arm64` from the workflow artifacts, install the DMG, then
choose **Developer → Developer Mode**, then **Developer → Install PR app bundle…** and select the included
payload tarball. These builds do not publish releases or change update channels.

CI compiles both Linux graphics variants in containers on an ARM64 Linux runner,
using the workspace's pinned images and graphics build script. The macOS 26 job
uses Xcode 26.3 and receives those artifacts through `SENTINEL_GUEST_GRAPHICS_DIR`;
their checksums and build inputs must match before packaging. Local Mac builds
without that variable continue to use a disposable build VM.

CI caches checksum-verified guest graphics, input-stamped native runtimes, and
package downloads. Native caches use exact source/build-input and toolchain keys;
download caches may be reused across dependency changes. Application builds,
signing, and DMG verification always run. GitHub cache scope means the first run
on a release branch may still be cold; subsequent PRs can reuse their base branch's
cache. Superseded PR runs are cancelled, while release runs finish publishing.
