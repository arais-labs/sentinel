# Upstream graphics patches

All modifications to third-party graphics source live here as unified diffs.
Sentinel-owned code stays in the renderer, video, and guest directories.

| Patch | Dependency | Purpose |
| --- | --- | --- |
| `host/epoxy-bundled-libraries.patch` | libepoxy | Resolve GL through the bundled Mesa EGL context. |
| `host/virgl-metal.patch` | virglrenderer | Ordered buffer updates and host transfer support. |
| `host/remote-renderer.patch` | remote-virtio-gpu (host) | Desktop GL transport with checked submissions, readbacks and fence responses; complete cursor image/position/hotspot/hide callbacks. |
| `host/mesa-0001-kosmickrisp-metal.patch` | Mesa | Metal shader features, resource ownership and texture/timeline export. |
| `host/mesa-0002-zink-metal-interop.patch` | Mesa | EGL/Metal scanout interoperability and asynchronous fence ordering. |
| `host/mesa-0003-zink-buffer-ranges.patch` | Mesa | Exact sorted buffer-copy ranges, avoiding repeated linear scans for sparse uploads. |
| `guest/` | Mesa | Persistent/coherent mapped-buffer integration. |
| `labwc/0001-refresh-map-pointer-focus.patch` | labwc 0.20.0 (Alpine only) | Refresh stationary-pointer focus after mapped visibility/window rules, including an already-topmost Xwayland window. Uses the distribution's existing wlroots 0.20 library. |
| `remote-proxy-fences.patch` | remote-virtio-gpu (guest) | Safely remove completed commands while iterating fences; accept the protocol's resource-zero cursor hide command. |
| `kernel-virtio-vblank.patch` | Linux virtio-gpu | Advertise the generated display-mode contract on the no-EDID virtual monitor; provide a mode-driven vblank clock and synchronize flips without waiting again for same-buffer damage. |
| `kernel-namespace-order.patch` | Linux 6.18 nstree | Backport globally sequential namespace ID allocation from upstream 3760342fd631; prevent valid cross-CPU namespace bind mounts failing with EINVAL while retaining cycle detection. |

Build scripts explicitly select patches for pinned dependency revisions and
apply them with `patch --batch --forward --fuzz=0 -p1`. A mismatch fails the
build; there is no fallback to unpatched code or runtime patching.

Host source preparation is cached by revision and patch contents. Changing or
removing a patch rebuilds the source tree from its upstream archive, then
publishes it only after every patch succeeds. Guest builds use fresh source
trees, and their artifact cache includes the guest patch. Host-only patches do
not invalidate Linux kernel or guest userspace artifacts. The kernel patch is
independently hashed into the kernel artifact, along with its configuration.

The no-EDID connector advertises all seven resolutions in `../display/modes.json`
at the contract's 120 Hz target, using reduced-blanking CVT timing. The proxy's
requested dimensions select the preferred mode; they do not restrict the mode
list. Exact duplicate timings are removed, existing fallback modes remain
available, and a real EDID remains authoritative. The build generates the kernel
header from this shared contract and includes it in the artifact identity.
This describes the virtual monitor's timing, not fabricated GPU features or an
application-specific frame-rate override. The source-derived
mode test applies the full patch to a pristine pinned Linux tree and compiles
the actual mode-selection function with DRM test doubles:

```sh
python3 tests/graphics/kernel/mode-advertisement.py /path/to/linux-6.18.35
```

Actual KMS mode enumeration and presentation cadence still require guest tests.

VirGL's host feature-check version describes the renderer protocol, not the
OpenGL version. Keep the upstream value so Linux Mesa honors capability flags.
