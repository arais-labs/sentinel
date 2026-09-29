# Optional packaged application parity smoke

This expensive gate runs inside an already-started **disposable** desktop
workspace. It never starts or reconfigures the desktop, updates graphics
libraries, or touches an existing application profile. Keep it separate from
ordinary unit/CI tests. Run after transport/backpressure regressions pass.

Provision the desired browser through normal workspace setup. Chromium is the
automation browser when Chromium or Firefox is selected; selecting Chrome makes
Chrome the automation browser. Chrome is supported on Ubuntu/Debian, not Alpine.
The fixture does not install browsers or change this selection. Test Chrome in
a separately provisioned Chrome workspace, not by overriding the launch command.

On a disposable Alpine guest, test-only dependencies can be installed with:

```sh
apk add --no-cache blender firefox python3 py3-pillow
```

Deliver these fixtures together into a private test directory:
`app-parity.py`, `blender-parity.py`, and `browser-parity.html`. For Chromium or
Chrome also stage the **unchanged production sources** from
`apps/backend/sentinel/app/services/runtime/guest_commands/linux/browser/start.py`
and `stop.py`. Do not copy flags into this fixture or use alternative workers.
Run as the guest root supervisor so native process maps can be inspected; the
browser and Blender themselves run as the published unprivileged desktop user.
Run applications sequentially, with an outer deadline as a safeguard:

```sh
timeout 180 python3 app-parity.py blender x11
timeout 180 python3 app-parity.py firefox x11
timeout 180 python3 app-parity.py chromium x11 --browser-start ./start.py --browser-stop ./stop.py
# In a disposable workspace provisioned with Chrome selected:
timeout 180 python3 app-parity.py chrome wayland --browser-start ./start.py --browser-stop ./stop.py
```

Repeat with `wayland` in a separate native Wayland desktop workspace. The runner
reads `/run/sentinel-desktop/session.json` and never guesses display names.
Direct Firefox/Blender launches remove the alternate display variable; Firefox
explicitly chooses its matching native backend and resolves `firefox` or
`firefox-esr`, never the workspace's potentially different `sentinel-browser`.
Chromium/Chrome use the production worker's `display: native` and backend policy,
including native-session Snap launching where applicable. CDP opens and activates
the test page, then real production F11 input enters fullscreen. Headless is not
used. The fullscreen viewport must match the desktop screenshot dimensions.

No sandbox bypass, forced GPU capability, or frame-rate preference is introduced.
The fixture rejects version/extension/software and Firefox sandbox overrides.
Native Wayland cannot be satisfied by a successful Xwayland launch.

The printed JSON identifies a unique `/tmp/sentinel-app-parity-*` directory with
application logs, screenshots and `result.json`. `--output` may select a new
test-owned directory. Firefox gets a fresh profile in the desktop user's HOME.
Automation workers get unique `/var/lib/sentinel/control/qualification-pixels-*`
state and their normal private profile location, with no existing profile reuse.
The production stop worker stops the owned browser; its logs are copied to the
result directory. Profiles and control evidence remain in this disposable VM
until teardown. Preserve failed evidence before deleting the workspace. Cleanup
failures fail the gate without replacing the original test error.

Blender must pass its exact 3844-pixel GPU triangle oracle, two nonuniform
Workbench renders with changed decoded pixels, live viewport selection, and
two production display screenshots with changed viewport-center pixels. The
small viewport marker gates screenshot freshness and is excluded from the
rotation comparison. Blender then exits cleanly.

Every browser must draw a WebGL shader with exact red/green readback pixels, present
matching screenshot regions, and acknowledge actual virtual click/key input by
changing the GPU canvas and displayed colors. Missing WebGL, a software renderer,
context loss, a crash, or a deadline fails. The loopback report endpoint uses a
unique run token; stale reports cannot pass a new run.

Browser identity and GPU evidence are collected from live process executable,
maps, and DRM descriptors, not solely the JavaScript renderer string. Loaded
Mesa's EGL or GLX implementation and virgl-only Gallium must match the packaged
hashes under `/opt/sentinel/graphics/lib`. GLVND dispatch libraries alone do not
count as a Mesa implementation. Snap mappings are resolved through the process's
mount namespace, with mapped inode identity checked before hashing. A live
`virtio_gpu` descriptor is required and software renderer libraries are rejected.
The result records UIDs, seccomp, and AppArmor identity. Chromium/Chrome's matching
GPU process must have seccomp filtering on every observed thread; Firefox instead requires a sandboxed
content process because its graphics may live in the unsandboxed parent. Snap
processes additionally require their browser's enforcing AppArmor profile.

The capture includes the compositor's cursor. Pointer movement and click targets
must stay away from sampled pixels; otherwise a correct frame can fail because
the oracle reads the cursor instead of the application's colors. Failed pixel
gates preserve the last screenshot, sampled values and application report.

These are bounded application regressions, not full OpenGL conformance or a
physical display FPS benchmark. The browser canvas intentionally isolates GPU
presentation/input; normal-page browsing remains a separate user exercise.
