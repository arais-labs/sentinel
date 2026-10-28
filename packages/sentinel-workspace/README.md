# sentinel-workspace

One workspace's virtual machine on macOS: the process sentineld starts for every running workspace
(`packages/sentineld/DESIGN.md` §15–22). It runs the VM on libkrun over Hypervisor.framework, which
allows one VM per process.

```
GUEST (stock distribution kernel and Mesa)       HOST: sentinel-workspace (one per running VM)
app → OpenGL → Zink → Venus (Vulkan)              libkrun: virtio-gpu, -blk, -net, -fs, -vsock, -input
        │ virtio-gpu (Venus capset)                 │ virglrenderer (Venus; vrend and Neptune for Windows)
virtio_gpu kernel driver ─────────────────────────▶ │   → KosmicKrisp (Vulkan on Metal) → Mac GPU
KMS scanout ──────────────────────────────────────▶ display.m: frame copy → VideoToolbox H.264 → fd 4
virtio-input ◀───────────────────────────────────── input.c: viewer and computer-use input
virtio-snd (playback) ─────────────────────────────▶ audio.m: PCM → AudioToolbox Opus → fd 4
virtio-net ◀──────────────────────────────────────▶ net.c: libslirp (user-mode NAT)
vsock 1 (agent), 1101 (computer use) ─────────────▶ unix sockets for sentineld, service.m
```

The guest needs nothing of ours for graphics: its stock `virtio_gpu` driver and Mesa's Venus driver
see a standard device. EFI firmware boots the image's own GRUB and kernel, so any distribution boots.

## Layout

| Path | What |
|---|---|
| `macos/workspace/main.m` | Reads the VM configuration from sentineld (fd 3), builds the VM, sandboxes itself, runs it. |
| `macos/workspace/display.m` | The display backend libkrun calls: scanouts into a CVPixelBuffer pool, the encoder, the cursor image, snapshots. |
| `macos/workspace/input.c` | Virtual keyboard, pointer and wheel; the computer-use lease. |
| `macos/workspace/audio.m` | The guest's sound card output: 10 ms Opus packets (AudioToolbox) to the viewers. |
| `macos/workspace/net.c` | libslirp on its own thread behind a virtio-net datagram socket. |
| `macos/workspace/service.m` | Computer use over vsock port 1101 (DESIGN.md §22). |
| `macos/video/` | The VideoToolbox encoder (and its backpressure test in `macos/tests/`). |
| `macos/scripts/` | `build.py`: fetches the pinned sources (`sources.json`), applies `macos/patches/`, builds with the pinned tools (`toolchain.lock.json`) and bundles `lib/`. |
| `macos/patches/` | Patches against libkrun, virglrenderer and Mesa (below). |

## Build

```
make            # build/: bin/sentinel-workspace, lib/, share/firmware/  (sentineld --workspace build)
make test       # the encoder's backpressure test and the renderers' smoke test (no VM)
make release    # build/release/sentinel-workspace-<sha16>.tar.gz + sentinel-workspace.json
```

The first build fetches and builds libkrun (private Rust toolchain), virglrenderer, Mesa, ANGLE, LLVM 15
and DXMT; later builds are incremental. No Xcode component beyond the SDK is needed (no Metal compiler). Nothing is installed on the host. CI: `.github/workflows/sentinel-workspace.yml`.

## Patches

Each patch applies with no fuzz; `fetch` fails on drift.

| Patch | Why |
|---|---|
| `libkrun-macos-display` | libkrun's macOS GPU path for a desktop: 2D resources backed by guest pages (no virgl), shared-memory blobs mapped into the guest, `SET_SCANOUT_BLOB`, the guest cursor handed to the display (a new optional `update_cursor` callback), the VMM worker started on macOS, a 4 KiB guest page granule (16 KiB host pages would misplace 4 KiB blob mappings), the cursor queue serviced (a full one froze page flips), a unique id per input device (the keyboard was lost behind the pointer), a virtio-snd device whose playback PCM goes to a host fd (`krun_sound_device_new`), guest RAM as owned 2 MiB chunks (charged once; the balloon's free-page reporting replaces the chunks the guest frees, returning them to macOS), and freed blobs unmapped (their host memory leaked). |
| `libkrun-pci-3-shm` | virtio-pci shared memory regions: a 64-bit prefetchable BAR with its `VIRTIO_PCI_CAP_SHARED_MEMORY_CFG` capability, the host bridge's 64-bit window, a display device's registers in BAR 2, so a 3D GPU works on PCI (Windows). |
| `libkrun-virtio-gpu-3d` | The `3d` GPU (Windows' viogpu3d): virgl (vrend on ANGLE) and Neptune (Direct3D 11 on DXMT) contexts beside Venus, the renderer polled while vrend fences are pending, a device reset that frees what the guest made. |
| `rutabaga_gfx-01-neptune` | rutabaga (libkrun's GPU glue, a pinned crate): capset 7 is virglrenderer's Neptune, not Magma. |
| `virglrenderer-01-upstream-macos` | Upstream MR !1685 (blobs imported as host memory on Metal). |
| `virglrenderer-03-vrend-angle` | vrend on ANGLE (OpenGL ES on Metal), from UTM's fork, with its own EGL winsys (no GL callbacks), scanouts as Metal textures. |
| `virglrenderer-04-neptune` | Neptune (Direct3D 11/DXGI contexts, osy's viogpu3d protocol) from UTM's fork, DXMT dlopened from the bundle, the fence pipe for proxy contexts, render workers as threads each bringing up only its own backend. |
| `angle-01-runtime-internal-shaders` | ANGLE builds without Xcode's Metal Toolchain: its internal shaders compile from embedded source at the first display. |
| `libepoxy-01-macos-bundled-angle` | libepoxy loads ANGLE's libEGL/libGLESv2 from its own directory. |
| `dxmt-01-runtime-shaders` | DXMT builds without a Metal compiler: its internal Metal library compiles from embedded source at run time (`newLibraryWithSource`, cached by Metal); airconv's AIR helpers are LLVM IR text assembled by the pinned LLVM 15's `llvm-as` (each names the sha256 of its `.metal` source; `dxmt.py` refuses a stale one). |
| `virglrenderer-02-dma-buf-emulation` | The guest's Venus needs dma_buf export and DRM modifiers for its window system: advertised to the guest, emulated with linear shareable images and host memory import. |
| `mesa-0001-kosmickrisp-metal` | The Vulkan features Zink needs above OpenGL 2.1: geometry shaders, transform feedback and its queries, polygon fill modes, custom border colors (upstream KosmicKrisp has not shipped them yet). With it the guest gets OpenGL 4.6 and ES 3.2; without it GL 2.1 / ES 2.0, and Chromium has no WebGL. |
| `mesa-0004-queue-finish-barrier-lifetime` | Barrier lifetime on queue finish. |
| `mesa-0005-kosmickrisp-disk-cache` | KosmicKrisp never created its shader disk cache: every start recompiled every shader (upstream bug). |
| `mesa-0006-kosmickrisp-venus` | A GPU-only memory type so tiled images stay out of host-visible memory; linear color attachments without 2D-array promotion (Metal rejects linear arrays). |
| `mesa-0007-kosmickrisp-dxvk-features` | `fillModeNonSolid` and `geometryStreams`, both required by DXVK: polygons drawn as points go through the emulated geometry stage as 1-pixel points (Metal has no point fill mode; face culling is not applied to them), and geometry shaders may write up to 4 streams (stream 0 rasterized, transform feedback queries per stream). |

Compiled shaders persist across workspace starts in the per-user cache directory.

## Known limits

- Viewer input: absolute pointer (0-65535), five buttons, wheels and keys 1-255; no relative mouse.
- One display, fixed at 1920×1200, 120 Hz.
