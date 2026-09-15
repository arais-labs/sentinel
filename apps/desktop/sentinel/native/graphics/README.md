# Desktop graphics

Apple Containerization remains the workspace runtime. Desktop applications use
Mesa virpipe in the guest, a workspace-owned byte channel, and the bundled native
renderer with Electron's ANGLE Metal backend. There is no software fallback.
The X/VNC display and frame transport still use CPU work; this is accelerated
application rendering, not GPU passthrough or hardware video encoding.

For every workspace, the native VM owner starts the renderer on the owning Mac.
The renderer and its libraries ship in the same verified runtime release as the
VM helper. The guest graphics stream uses bounded binary I/O within that host;
graphics commands and pixel readbacks do not travel to the viewing Mac. Closing
a viewer leaves graphics running until the desktop or workspace stops. Local and
remote workspaces use the same `WorkspaceGraphics` controller and native
`HostGraphics` implementation; there is no separate TypeScript rendering bridge.

All VNC traffic bypasses the terminal JSON/base64 channel. The shared native
`GuestPortForward` exposes guest loopback TCP through a private mode-0600 Unix
socket on the VM host. The backend uses one binary forwarding loop for both
connection types: local sockets connect directly, remote sockets through pinned
SSH. Long runtime paths use a private, root-hashed socket directory under `/tmp`
to stay within macOS's Unix socket address limit.

## Build and installation

`npm run dev` and the macOS release build both run `scripts/packaging/graphics/build.py` before launching or
packaging the app. It bundles the host renderer and runs the adjacent `build-guest.py` to
precompile Mesa in an owned build VM. The guest archive is cached by source and
runtime-lock inputs. Build VMs are deleted on success or failure; only the build
cache and pinned base images remain. Nothing is installed into host Homebrew.

The DMG includes `mesa-linux-arm64.tar.xz` for musl and
`mesa-linux-arm64-glibc.tar.xz` for glibc, their checksum manifests, and the host
libraries. Both use the same pinned Mesa source and rendering patches; the glibc
variant is built on Ubuntu 24.04 for Ubuntu and Debian guests. Workspace installation streams this archive through the existing VM
process channel, verifies it, and installs it atomically. It never downloads
Mesa sources or starts a compiler. The ordinary selected desktop packages still
come from the workspace's package repository.

Mesa's `LIBGL_ALWAYS_SOFTWARE=1` selects its socket-based winsys; with
`GALLIUM_DRIVER=virpipe`, rendering executes on host Metal. The guest loader also
registers the bundled libraries because Chromium's GPU child removes
`LD_LIBRARY_PATH`. Host paths, libraries, and shell configuration are unchanged.

## Compatibility patches

The pinned virglrenderer fork needs explicit ANGLE Metal selection and an EGL
path without GBM. Vtest protocol 0 carries bytes without sharing Linux file
descriptors with macOS. Mesa must consume inline image data before the subsequent
busy-wait reply, otherwise presentation shifts three pixels.

ANGLE's `GL_ANGLE_texture_multisample` supplies real multisample texture storage
on Metal's ES 3.0 backend. Use it for format/capability validation and allocation;
do not apply the fork's legacy single-sample clamp. Implicit multisample resolve
is not advertised because its guest attachment layouts fail on this backend.
Chromium uses explicit GLES/EGL rendering with software rasterization disabled.

## Verification

After compiling TypeScript, run `node tests/integration/workspace-graphics.mjs`
from the desktop directory. It owns and removes its test VM and checks archive
installation, reuse, and absence of compiler tools. `graphics-render.c` compares
every displayed pixel with GPU readback; run it inside a prepared disposable
desktop with the graphics environment above. Unit tests cover ownership and failed
provisioning. Build artifacts are checked by the packaging requirements.

`node tests/integration/host-desktop.mjs` exercises native host rendering in a
disposable VM, verifies displayed pixels against GPU readback, checks rendering
survives viewer cleanup, and checks binary forwarding, half-close, and teardown.
