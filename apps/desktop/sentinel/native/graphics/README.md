# Workspace desktop contract

The worker owns the GPU and desktop. A viewer owns only its video/audio stream
and input connection. Closing Sentinel's Desktop tab must not stop Linux apps.

```
Apple Containerization / Virtualization.framework
  Linux kernel: virtio-loopback + virtio_gpu + evdev/uinput
  OCI root filesystem: Mesa virgl + selected Linux desktop
       ↕ private guest/worker GPU channel
  Worker: VirGL → Mesa Zink → KosmicKrisp → Metal → VideoToolbox H.264
       ↕ authenticated local socket or SSH tunnel
  Sentinel Desktop: WebCodecs → canvas, Opus → AudioWorklet
```

No VNC, QEMU, host package installation, or software-rendering fallback. This
provides a virtual Linux DRM/OpenGL GPU, not PCI passthrough or Vulkan support.
120 Hz is a target; actual delivery depends on the application, resolution,
display, and connection.

GPU command/resource channels share one private VM socket with per-channel
receive credits. The same C transport runs in the host renderer and guest bridge:
each channel reserves 64 KiB of receive storage, and returns credit only after its
consumer accepts those bytes. The physical connection keeps draining when a
logical consumer pauses. This avoids a command/readback deadlock on VM transports
where a blocked connection also prevents unrelated connections from progressing.
GPU data never travels through guest process stdin/stdout. The guest bridge accepts
only the owning host; EOF or malformed transport data closes the coupled GPU
session. Viewers still use the separate encoded video/control connection. This
does not claim to eliminate backpressure hazards in unrelated VM connections.

The kernel exposes its native primary and cursor planes. The guest proxy carries
virtio cursor image, hotspot, position and hide commands to the owning Mac.
The renderer composites the guest cursor over a retained cursor-free scanout
before capture and video encoding; cursor-only updates present even when the
desktop is idle. Compositors use their normal cursor path, and viewers hide the
host pointer over the desktop image. The desktop session owner is a
Linux child subreaper and waits for its detached descendants to exit before
reporting a successful stop; no recurring cleanup job is involved.

`node tests/integration/desktop-cursor.mjs` qualifies the native cursor plane in
a disposable Ubuntu GNOME VM. It checks stock atomic mode setting and captures
an asymmetric guest cursor through the production screenshot path, exercising
hotspots, idle motion/shape changes, clipped edges and hiding on both Wayland
and Xwayland. Host pixel/ring tests additionally check premultiplied alpha,
bounded uploads and coalesced cursor delivery under backpressure.

## Source ownership

| Directory | Responsibility |
| --- | --- |
| `guest/driver/` | Linux mapped-buffer tracking, GPU/CPU publication, bounded allocation ownership. |
| `guest/` | Driver installation, display/session lifecycle, input, clipboard and audio. |
| `transport/` | Shared bounded GPU framing, receive credits and fair channel scheduling. |
| `renderer/` | Worker GPU command transport and scanout ownership. |
| `video/` | Ordered Metal texture copy, video encoding and viewer connections. |
| `patches/guest/` | Linux Mesa integration with the owned driver modules. |
| `patches/host/` | Host Mesa, VirGL and EGL dispatch changes against pinned upstream sources. |
| `packaging/gpu-2404/` | Strict Snap graphics-provider contract, pinned runtime dependencies and wrapper. |
| `packaging/mesa/` | Native Alpine Mesa runtime/development APK family, signature verification and source provenance; no global musl loader override. |
| `packaging/klipper/` | Distro-native clipboard-library packaging, regression gate, and provenance; independent of Mesa. Final integrated matrix qualification is pending. |
| `scripts/packaging/graphics/` (app root) | Source preparation, build tools, host/guest builds and packaging. |
| `tests/graphics/` (app root) | Focused driver and transport regressions. |

`sources.lock.json` pins host source revisions. `toolchain.lock.json` pins
checksum-verified, privately extracted build tools. Neither the application nor
its build depends on a prototype directory or a system-installed graphics SDK.
There is one graphics backend; source control owns historical implementations.

Linux persistent mappings use tracked CPU memory and ordered GPU transfers,
not cross-VM shared Metal memory. Bounded CPU captures compare against a private
baseline; they do not require userfaultfd or privileged device access. Guest
startup verifies capture and acceptance before starting the GPU. Normal fence
waits publish completed GPU writes before returning success. Native fence-FD
export drains pending mapped-memory readbacks before exporting the kernel fence,
because an external FD wait cannot perform that publication. Ordinary draws and
upload-only submissions still export asynchronous fences without a GPU wait.
Ordinary submissions also reclaim completed readbacks. When the bounded journal
fills, submission waits for reclaimable GPU work instead of discarding commands;
publication cannot interrupt an active CPU upload snapshot.

Upload-only persistent buffers do not need repeated host-to-guest snapshots.
The driver conservatively remembers possible host writers (including shader,
copy, query and shared-context writes) for the lifetime of a tracked buffer.
Once a host writer is possible, normal readback and CPU/GPU merge rules remain
in force, including for write-only CPU mappings. This avoids full-buffer
readbacks of compositor upload buffers without discarding GPU modifications.

The guest exposes OpenGL through VirGL. Vulkan is currently a **host-side**
implementation detail between Zink and Metal, not a guest Vulkan device.
Advertised API versions are driver-derived, not version-string overrides;
passing regression tests is not a Khronos conformance claim.

Pre-fragment storage and atomics are implemented by the host Metal driver.
Geometry shaders with external-memory side effects execute once into a bounded
vertex-record buffer; rasterization consumes those records instead of replaying
the shader. Transform feedback keeps its complete records while the raster
interface matches the linked fragment shader. This preserves side effects and
captured outputs without advertising unsupported capabilities through overrides.

## Linux customization

Desktop choice (`none`, `xfce`, `lxqt`, `gnome`, `plasma`) is independent of development tools.
Explicit workspace setup installs packages and the prebuilt driver/runtime.
The launcher never reinstalls packages or resets saved preferences on reconnect.

`/etc/sentinel/desktop.json` selects `protocol` (`x11` or `wayland`), a `command`
array, `session_manager: "native"`, optional `environment`, and `audio`. Agents have root access and can edit
this file or install a different desktop. Distro init owns DBus, udev and logind
(elogind on Alpine). greetd opens a real PAM login as the regular `sentinel` user,
whose stock desktop session owns Xorg or its Wayland compositor. An XDG autostart
entry publishes the actual display socket, authentication and session bus;
Sentinel validates them against the live login before attaching. Changing the UI desktop
choice backs up the previous configuration as `desktop.previous.json`.

XFCE supplies an applications menu and running-window taskbar. GNOME and Plasma
use their stock Wayland sessions. Weston is an internal graphics-test profile,
not a normal desktop choice. Profile definitions alone are not qualification:
each distro/desktop combination must pass the disposable integration test before
being exposed as supported in the UI.

LXQt supplies a full Wayland desktop using the maintained labwc compositor and
stock LXQt panel, application menu, running-window taskbar, file manager and
terminal. The compositor owns the session lifetime with `labwc -S lxqt-session`;
Sentinel does not replace its panel or maintain a custom shell. LXQt session and
panel packages must be at least 2.1. Sentinel does not add third-party repositories
or upgrade the distribution. The shared output adapter selects an advertised
mode using each desktop's native configuration interface, then reports the
actual resolution and refresh rate. PipeWire and its Pulse-compatible service
belong to the user session; Sentinel captures its default playback monitor.

Alpine's labwc 0.20 needs one pointer-focus correction after mapping a window.
Its pinned source and explicit patch are compiled into the musl graphics bundle
as `/opt/sentinel/graphics/bin/labwc`; the distribution still owns wlroots,
configuration, translations and other runtime dependencies. Packaging forbids
Meson fallback subprojects and verifies the binary's native musl/AArch64 and
dynamic wlroots 0.20 ABI. The archive includes complete corresponding upstream
source, patch, build recipe and license. Ubuntu and Debian keep their stock
labwc: their older map implementations do not contain this regression.

Alpine GNOME uses the distribution's unmodified LocalSearch package. Graphics
libraries must be packaged in native loader locations without a Sentinel-global
musl loader override; the former override caused sandboxed library discovery to
fail and did not justify maintaining an application sandbox patch. No
LocalSearch source, patch, or replacement APK is bundled.
`desktop-localsearch.py` qualifies real sandboxed extraction and session-bus
content indexing as the regular desktop user, then restores indexing settings.

GNOME readiness is a session contract, not just an open Wayland socket. Shell
keeps an invisible input-blocking cover during startup. The small integration
in `guest/session-assets/gnome` publishes `startup-complete` to a private record
bound to the login token and live Shell process identity. The native publisher
waits for that record before exposing the session. There is no first-click retry
or fixed startup delay. `desktop_gnome.py` installs an inherited Shell mode
without replacing user extension preferences or recompiling Shell. Session 48
uses the stock launcher with that mode; session 50 selects the distro's existing
Shell service template through a named session target. Disabling the required
extension prevents readiness rather than silently accepting an unready desktop.
These interpreted assets share the session archive identity, not Mesa's build
identity. Qualification still requires each distribution's live startup,
relogin, application-input and shutdown tests; unit tests alone are insufficient.

Base images live in `native/workspace-images`, independently of graphics and
desktop packages. They boot OpenRC (Alpine) or systemd (Ubuntu/Debian), never
dockerd. Docker and Kubernetes are optional selected tools. Existing system disks
are not converted: explicit Reinstall adopts the native-init contract and erases
VM-only data while preserving the mounted host project.

New Ubuntu workspaces use the pinned Ubuntu 26.04 image. Existing Linux disks
keep their installed distribution. Reinstall requires explicit destructive
confirmation: it replaces only that workspace's private Linux disk, then installs
the selected desktop and tools. VM-only files, browser profiles and settings are
erased; the mounted host project, workspace configuration and conversations stay
intact. A failed or interrupted reinstall never authorizes another deletion on
Retry. There is no in-place distribution upgrade path.

Desktop applications run as the regular `sentinel` account in `/home/sentinel`,
with allocated UID/GID and passwordless sudo. The workspace agent and privileged
VM supervisor retain root access. Provisioning leaves root-owned profiles alone;
desktop preferences belong to the regular user's home. Graphics device access
uses the regular graphics groups; mapped-memory tracking needs no device permissions.

`/run/sentinel-desktop/session.json` publishes the active display environment.
Native login/session management owns the regular-user session bus; Sentinel
validates and publishes its address with that environment, so panel launchers
and agent-launched graphical applications share the same session. Reconnecting
does not create another bus or a second shell. The internal Weston diagnostic
profile directly owns its test session bus.
`display.sock` exposes capture and virtual input; it is independent of the
compositor. Clipboard/text adapters use the published X11/Wayland environment.
Disconnecting a computer-control request releases held keys and buttons.

Audio captures only a Pulse-compatible output monitor, never a microphone.
The guest sends bounded 10 ms stereo Opus packets on the existing display
connection. Playback starts after a user gesture and stops for hidden panes.
No always-running host audio daemon or additional network port is used.

## Build and verification

Third-party source changes are explicit diffs in [patches/](patches/README.md).
Build scripts fetch pinned sources and apply those diffs; they contain no inline
source rewrites.

The application packaging layer only invokes native builders and stages their
artifacts. `scripts/packaging/graphics/build.py` coordinates verified inputs,
component caches and atomic publication; it does not replace compiler dependency
tracking. `host.py` configures upstream Meson/Ninja builds, and this directory's
`meson.build` owns Sentinel's C/Objective-C renderer target. Linux Mesa also uses
Meson/Ninja, the kernel uses Kbuild, and downstream Alpine packages use abuild.
Keep compiler targets in those native build systems; do not add per-source
compilation loops to the JavaScript application packager. Component pins and
patches belong beside their native implementation; generated sources, build
trees, artifacts and test evidence belong under `build/`, never in source folders.

Kernel, musl Mesa, and glibc Mesa artifacts have independent content hashes.
The host bundle has a separate identity covering its renderer, transport, video,
upstream pins, patches, build tools, and selected Apple SDK/compiler versions.
Guest session scripts and distro packages do not invalidate host compilation.
Even on a host cache hit, packaging checks the independently keyed guest/kernel
artifacts and republishes the current session archive. SDK/compiler changes also
invalidate the inner Meson object caches; unchanged environments retain Ninja's
incremental objects. Keep these ownership boundaries in `host.bundle_identity`
and its unit tests when adding build inputs.
The virtual monitor's finite capability contract lives in `display/modes.json`.
`generate-display-modes.py` generates the kernel C header at build time; its
Python and TypeScript outputs are checked in for the guest, backend and frontend.
`tests/unit/test_display_modes.py` rejects drift from that contract. To change
supported resolutions, update the JSON and regenerate those consumers with
`--language python` / `--language typescript`; do not add another resolution list.
The kernel advertises every declared geometry at the target refresh rate even
when it differs from the initial preferred size. EDID-backed modes remain
authoritative. The native mode regression checks advertisement, not actual GPU
throughput or display presentation; those require a rebuilt-kernel guest run.

Local macOS builds reuse a stopped builder VM identified by its pinned base
image, boot kernel, and init image. Its disk lives under
`build/graphics-sources/guest/runtime`; normal completion and failures stop the
VM without deleting its disk. Source edits do not change the VM identity.
The builder retains installed dependencies, package downloads, source trees,
and compiler outputs under `/var/cache/sentinel-build`. Components have their
own input hashes and successful-output markers: a labwc patch does not rebuild
Mesa or the kernel. Failed component builds resume with their existing
objects; native APK builds use abuild's keep mode and a shared compiler cache.
Guest Mesa and Kbuild use a persistent `ccache` directory across component keys, with
compiler-content checks and paths normalized relative to each work tree. New
source keys still receive isolated output directories; unchanged compilation
units can be reused without disabling header or compiler checks. Build logs
include cache statistics. The first cache-populating build still compiles normally.
Kbuild uses the cache for both target C and host C/C++ tools, including the
out-of-tree loopback module; linking and other non-compilation work still run.
The builder uses all available host cores and a sparse 64 GiB disk. Cache and
builder disks are retained until explicitly removed while no build is running.
Changing the pinned base image/kernel/init selects a different builder.

Native Linux ARM64 builds use disposable Docker containers with the same stable
builder identity. The host directory
`build/graphics-sources/guest/linux-builders/<builder-id>` is mounted at
`/var/cache/sentinel-build`, retaining component work trees and ccache across
container exits, including failures. Container OS packages are still installed
on each cold container; this is not a persistent VM. CI caches verified output
archives separately; runner-local intermediate directories are not uploaded.

KDE uses distro-provided KWin, not a downstream build. Its session sets
`KWIN_FORCE_SW_CURSOR=1` to composite the cursor on the GPU and avoid the GLES
hardware-cursor readback path. This does not enable software desktop rendering.

Ubuntu's confined Chromium cannot use the workspace's ordinary driver prefix
directly. `packaging/gpu-2404/` defines a strict content-provider Snap assembled
from the same verified glibc Mesa artifact and a checksum-locked runtime-library
closure. It is a separate cached packaging target on the existing glibc builder,
not a second Mesa compilation or a Snapcraft build. The native workspace worker
installs it after browser provisioning, including workspaces without a desktop.
`guest/install-browser-graphics.py` verifies the transferred package and checks
both the installed version and Chromium's actual content connection before
accepting reuse. Local unsigned-package installation does not disable Snap
confinement; qualification checks the live browser's enforcing AppArmor profile,
seccomp, mapped driver hashes and DRM device. This provider supplies OpenGL/EGL,
not a guest Vulkan driver.

Guest GL dispatch follows each distribution's native ABI. Ubuntu/Debian use
their packaged GLVND public libraries (`libEGL`, `libOpenGL`, `libGL`, `libGLES`)
and standard Mesa vendor registration; Sentinel supplies `libEGL_mesa` and
`libGLX_mesa`; the glibc loader configuration selects those bundled vendors even
for clients that remove `LD_LIBRARY_PATH`. Component installation replaces the
complete graphics prefix so obsolete public GLVND libraries cannot remain and
split EGL/OpenGL context dispatch. Alpine instead installs its native monolithic
Mesa ABI as seven signed, exact-version-linked APKs into `/usr/lib`, with real
headers/pkg-config files in `mesa-dev`. Normal package ownership and library
discovery select this driver: no Sentinel-global musl loader configuration or
desktop-session Mesa path override is used. Private transport helpers remain
under `/opt/sentinel/graphics`.
The build verifies the expected ABI and rejects bundled public GLVND dispatchers.
`tests/fixtures/desktop-gl-dispatch.py` compares GL strings through both EGL's
function lookup and the distribution's public GL library using one live context;
the desktop integration test runs this before application qualification.
CI builds them on Linux ARM; macOS packages verified artifacts and the Metal
renderer. A runtime update ships all assets as one worker release. The worker
migration converts legacy Desktop tool selections without replacing VM disks.

Quick checks: `npm test`, `npm run test:graphics-build`, and
`python3 -m unittest discover -s tests/native -p 'test_*.py'` (macOS).
Portable GPU transport checks:
`python3 -m unittest discover -s tests/graphics/transport -p 'test_*.py'`.
These verify exact bytes under independent channel backpressure, bounded credits,
fair scheduling, malformed traffic and cancellation; they do not require a VM.
Host GPU checks: `python3 -m unittest discover -s tests/graphics/host -p 'test_*.py'`
and `python3 -m unittest discover -s tests/graphics/host/mesa -p 'test_*.py'`.
These exercise actual Metal pixels, exported texture ordering, buffer storage
and capability limits. Set `SENTINEL_TEST_GRAPHICS_DIR` to test another built
bundle. `sh tests/graphics/guest/run.sh` checks publication and packet encoding;
on a Linux guest it also verifies the mapped-memory lifecycle contract.
Renderer endpoint ownership and streaming readiness are checked by the native
display tests and `swift test --package-path native/macos`. Readiness must arrive
while the child keeps stdout open; shutdown drains ownership reports through EOF
before removing matching socket identities. Replacement paths are preserved.
`node tests/integration/renderer-crash.mjs` provisions a disposable Debian VM,
resolves only that VM's renderer, kills it, and requires both owned endpoints
to be absent after workspace stop. It accepts `SENTINEL_TEST_RUNTIME` like the
desktop matrix; it does not terminate a renderer belonging to another workspace.
After preparing the runtime, `npm run test:linux` compiles the adapter and runs
the Ubuntu/Debian/Alpine × XFCE/LXQt/GNOME/Plasma matrix. Filters, isolated
artifact selection and evidence reports are documented in
[`tests/qualification/README.md`](../../tests/qualification/README.md).
The underlying `node tests/integration/host-desktop.mjs` owns a
disposable Alpine VM and tests video, audio, reconnect, input, clipboard, resize,
and shutdown. Its scale-1 application oracle checks visible colors, fixed
fiducial positions/boundaries, and actual first-click coordinates and keyboard
events. Uniform fullscreen color alone is not readiness: a compositor can still
be transforming the window. The check changes no animation preferences and
does not accept a retried click. On a visual failure the exact sampled image is
saved to `build/<distribution>-<desktop>-oracle-failure.png`; stream timeouts
report video/audio packet counts separately. Select `SENTINEL_TEST_DESKTOP=xfce|lxqt|gnome|plasma`
and `SENTINEL_TEST_DISTRIBUTION=alpine|ubuntu|debian` to qualify the full matrix.
Wayland profiles test both native Wayland and Xwayland clients. Callback timing is reported separately from
physical display frame rate and is not a CI timing threshold.

The default qualification matrix has 32 supported distro/desktop/browser rows.
Every row checks the selected production automation launch/reuse/stop contract,
as a regular user, in both headless and native desktop modes. Chrome selection
uses Chrome; Firefox selection retains Chromium for automation and additionally
tests Firefox's native pixels/input/confinement. Chromium selection uses Chromium.
Native browser gates require presented pixels, real input, packaged driver
provenance, DRM ownership and GPU-process seccomp. Each row also repeats the
headless lifecycle after a same-disk cold boot without package reinstallation.
Alpine
ships the default headless renderer separately as `chromium-swiftshader`; setup
installs it without forcing software rendering for desktop sessions. A lifecycle
pass is not browser GPU or sandbox-conformance qualification.

Alpine uses the unmodified distro Chromium package. Its standard
`/etc/chromium/sentinel.conf` selects the existing shader disk-cache backend with
`--disable-features=GpuPersistentCache`: the newer GPU-process SQLite backend
uses a musl positioned-write syscall that the stock GPU sandbox rejects.
This preserves shader caching and sandboxing for desktop and automation; it
does not disable GPU acceleration or build a custom browser.

For refresh regressions, compile `tests/fixtures/graphics-timing.c` inside a
disposable X11 workspace (`cc graphics-timing.c -lGL -lX11 -o graphics-timing`)
and run it with the session's display environment: `./graphics-timing 120`.
It checks the advertised mode, DRM-derived clock, and actual swap cadence.
This hardware performance probe is opt-in, not a wall-clock CI assertion.
The native capability test runs without a VM and compares VirGL's advertised
timer-query support with the actual bundled Metal backend.

## Keyboard layout

On explicit desktop start, the local macOS backend supplies a matching XKB
keyboard default for recognized input sources. This is standard Linux layout
configuration, not character injection: XFCE uses xfconf and authenticated
setxkbmap; GNOME uses its input-source settings; Plasma uses kxkbrc; labwc consumes
XKB defaults. These run as the normal session user. The internal Weston test
profile uses its native keyboard configuration. No compositor module, polling
or per-keystroke process is involved. Reconnecting does not change
the running desktop's layout; stop/start the desktop after changing input source.
The compiled session keymap also supplies logical key chords for computer use,
so agent shortcuts do not keep assuming the previous keyboard layout.

The owning workspace can override the default in `/etc/sentinel/desktop.json`
with a `keyboard` object containing XKB `layout`, optional `variant`, `model`,
`rules` and `options`. Set `keyboard` to `null` to opt out of host matching.
Configuration is per workspace and is not written to the Linux system layout.
Unknown host sources keep guest settings; automatic matching currently covers
the explicitly listed macOS sources in backend `desktop_keyboard.py`. This does
not implement host IME forwarding or turn Linux application shortcuts into macOS
shortcuts. Custom compositors must consume the standard `XKB_DEFAULT_*` defaults
or use their own keyboard configuration. Changing the Linux layout after startup
does not currently refresh the computer-use logical-key cache; restart the
desktop after changing it.

## Clipboard

Each profile declares its clipboard protocol. XFCE and GNOME use authenticated
X11 selections (GNOME's native Xwayland selection bridge); LXQt and Plasma use
the bundled upstream wl-clipboard client with wlr/ext-data-control support.
There is no focus-stealing fallback. Commands run as the session user with its
published display credentials. The bundled GPL clipboard utilities remain
separate executables and include their exact upstream source and licenses.

Stock Plasma's Klipper can race a new copy while restoring clipboard history
after login. Packaging now includes distro-native library packages with the
fix described in [`packaging/klipper/`](packaging/klipper/README.md); final
assembled-runtime matrix qualification remains pending.
Sentinel does not disable history, add startup delays or retry a failed copy.
The disposable test normally fails on any
clipboard mismatch. `SENTINEL_DESKTOP_DIAGNOSE_CLIPBOARD=1` (Plasma only) records
that mismatch, continues independent checks and still fails the overall run;
it is a diagnostic mode, not a relaxed qualification gate.
