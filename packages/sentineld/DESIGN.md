# sentineld design

Status: Stage 1 accepted; Stages 2 (guest, revised for installed base disks) and 3 (macOS backend) draft for
review. Later stages: graphics, build/release.
No migration: this release is a clean break from existing workspaces.

# Stage 1: architecture and control API

## 1. Purpose

`sentineld` is the one process that owns Linux workspace VMs on a machine. Its client is the backend
on the user's machine. That client reaches a local sentineld directly and a remote one over SSH.

Non-goals:

- Not a container engine: every distribution is installed into VM disks (§7). No multi-tenant
  users.
- No network listener for control. Control is Unix socket only; remote access is SSH.
- No Linux or Windows backend in this work. Only the interface they would implement.
- No software-rendering, VNC or TCP media fallback.

## 2. Architecture

```
user's machine                                         local or remote host
backend (Python) ──control: Unix socket / SSH streamlocal──▶ sentineld
frontend viewer  ──media: WebTransport/QUIC (UDP)──────────▶   │ Platform (C++ interface)
                                                               ▼
                                                    macOS backend → sentineld vm
                                                               │ vsock
                                                               ▼
                                                    guest agent (service under distro init)
```

| Component | Language | Owns |
|---|---|---|
| `sentineld` core | C++20 | catalog, request handling, provisioning jobs, health |
| `Platform` interface | C++ header in core | VM create/start/power-off, vsock connect, disk clone/grow |
| macOS backend | C++ | spawns and supervises one `sentineld vm` per VM (§15) |
| `sentineld vm` | Objective-C, C | one VM on libkrun: disks, NAT (libslirp), virtiofs, vsock, GPU (Venus), video encoder, input |
| guest agent | C++ | the host's way into the guest: exec, process, forward, mount, shutdown (replaces Apple `vminitd`). Display, input and sound are VM devices (§16) |

### Tree

```
packages/sentineld/
  DESIGN.md
  core/                 C++ core + platform.hpp; no OS-specific code
  media/                sentineld-media (§6)
  guest/                host-independent guest parts (§7–9)
    agent/              C++ agents (Linux, Windows)
    distributions/      one folder per distribution: its install recipe and guest data (§8)
    desktop/            desktop setup, session and computer use
  macos/backend/        Platform implementation: spawns `sentineld vm`
  macos/vm/             `sentineld vm`, the VM process (libkrun, display, input, network, computer use)
  macos/video/          VideoToolbox encoder
packages/sentinel-workspace/
  macos/scripts/, macos/patches/   pinned build of libkrun, virglrenderer, Mesa (the VM's libraries)
```

## 3. Process model

### Client: the backend only

The backend already issues every workspace request. Today Electron sits between them as a
pass-through bridge with a second copy of the lifecycle (`workspaceRuntimeBridge.ts`,
`workspaceLifecycle.ts`). Both are deleted.

Electron keeps two jobs:

- Spawn local `sentineld` and give the backend its socket path.
- Serve `/v1/notifications` (`services/notifications.py:34`) on its own small socket. Those are
  desktop OS notifications, not workspace concerns.

`deployment` (`machines.py:204`) is deferred to the build/release stage.

### Lifetime: whoever starts it stops it

There's one binary and one API. Only the owner differs. One instance per state directory; an
exclusive lock on the directory makes a second instance fail fast.

| Mode | Started by | Stops when |
|---|---|---|
| Owned | Sentinel: `sentineld --state DIR --lifeline` (stdin is a pipe kept by Electron) | stdin EOF, i.e. Sentinel quits or crashes |
| Standalone | a person, a test, or the backend over SSH on a remote host (`nohup`) | SIGTERM or SIGINT |

Both stop paths run the same drain:

1. Cancel jobs.
2. Close process connections.
3. Stop every VM concurrently (as `stop`).
4. Exit.

stdin carries only the lifeline; all requests go through the socket.

## 4. Control plane

| Aspect | Design |
|---|---|
| Transport | Unix stream socket `<state>/sentineld.sock` in a 0700 dir. Remote: SSH `direct-streamlocal`. Never TCP. |
| Auth | Filesystem permissions locally, SSH remotely. No bearer tokens. |
| Framing | NDJSON. Request `{"id","op",...}`, reply `{"id",...}` or `{"id","error"}`, event `{"event",...}`. Request lines ≤1 MiB; reply lines ≤9 MiB (bounded by `exec`). |
| Concurrency | Requests on a connection run concurrently; replies match by `id` in any order. `status` may be pipelined with the first request, so a new connection costs no extra SSH round trip. A client may open many connections. |
| CLI | `sentineld ctl (--state DIR \| --socket PATH) OP [FIELDS]`: one request from a shell. It sends `status{api}`, then `OP` with `FIELDS` (one JSON object), prints the reply as one JSON line and exits 1 when it is an error. With `list` and `{"watch":true}`, it then prints each event line until sentineld closes the connection. `ctl --help` lists every op it sends (all but `process`) with its fields and an example, and refuses any other op before connecting; `sentineld view (--state DIR | --socket PATH) WORKSPACE` opens the workspace's desktop in a native window: the app's viewer (H.264 through AVSampleBufferDisplayLayer, cursor, input, clipboard, Opus sound, and the §6 refresh rate of the screen it is on), built into sentineld (`core/view/`). `sentineld help [distributions]` prints the daemon's usage or the guide to writing a distribution. Both texts are built into the binary (`core/help/`), so an agent learns sentineld from the build it runs. |
| Versioning | `status{api}` first on every connection; a connection whose first request isn't `status{api:1}` gets `unsupported` and is closed. `api` is one integer, bumped only for breaking changes. A remote sentineld can be a different build from the local backend, so a mismatch is checked up front: `unsupported` with `details.required`. If a pipelined `status` fails, every later request on that connection gets `unsupported`, then the connection closes. |
| Strictness | No capabilities. sentineld rejects any unknown op, field or enum value with `unsupported` and `details.field`. Clients ignore unknown reply fields. |
| Errors | `error = {code, message, details?}`. All ops may return `invalid`, `not_found`, `internal`, `unsupported`; the table lists only op-specific codes. `message` is user-facing. sentineld never retries a mutation, and clients must not replay one after an ambiguous failure. |
| Durability | Mutations are written to the catalog before they are acknowledged. On restart, an in-flight `operation` is cleared and recorded as an `error`, never replayed. |

`conflict` always carries `details.reason`:

| Reason | Meaning | Returned by |
|---|---|---|
| `revision` | stale `revision` | `configure`, `start` |
| `name` | name taken | `configure`, `start{erase,spec}` |
| `busy` | another operation in flight | `configure`, `start`, `recover`, `stop` (while `recovering`) |
| `stopped` | VM not running | `exec`, `process`, `forward`, `display` |
| `recovery_required` | a fault requires `recover` first | `start` (not with `erase`), `exec`, `process`, `forward`, `display`. Never `delete` |
| `not_required` | no fault to recover from | `recover` |

`guest` means the guest agent is unreachable while the VM is running. Only ops that talk to the agent
return it: `exec`, `process`, `forward`, `display`.

### Process connections

`process` takes over its connection. It first replies `{"id","started":true}` on its own line; then
the connection becomes the raw relay of binary frames `[u8 type][u32 BE len][bytes]`, with `len` ≤1 MiB. A larger frame closes the connection.
The client must not send frames before `started`.

| Type | Direction | Payload |
|---|---|---|
| 0 `stdin` | → | bytes; `len 0` = EOF |
| 1 `output` | ← | bytes; stderr is merged into stdout |
| 2 `resize` | → | `u16 cols, u16 rows` |
| 3 `exit` | ← | JSON `{"code":int?,"error":string?}`, then the server closes |

Closing the connection kills the process (SIGKILL). Ctrl-C travels as PTY input.

## 5. API

Workspaces are records in sentineld's catalog. Provisioning jobs for different workspaces run
concurrently; VM create and destroy are serialized inside sentineld.

### Types

`spec`, supplied by the client:

| Field | Type | Rule |
|---|---|---|
| `name` | string | 1–120 chars, unique per sentineld |
| `project` | string | absolute host path, no `..`, ≤4096 bytes, not under guest system dirs (§11) |
| `distribution` | a distribution the share describes (§8: `alpine`, `ubuntu`, `debian`, `windows-11`), with an `install` recipe (§7), else `unsupported` | fixed, except on `start{erase}` |
| `disk_sync` | `full`\|`fsync` | Stage 3 §16 |
| `desktop` | `none`, or a desktop the distribution offers (§8: `xfce`, `lxqt`, `gnome`, `plasma`) | |
| `tools` | string[] | catalog ids (§8) available on the distribution, unique; unknown ids are `unsupported` |
| `resources` | `{cpus 1–32, memory_gib 1–64, disk_gib 8–1024}` | disk only grows; ≥ the distribution's `minimum`; `disk_gib` is the chosen size of a sparse disk (no TRIM, no max-size semantics yet) |

The whole spec is ≤512 KiB.

`record`, produced by sentineld: `{spec, revision, state, operation, error, progress, screen,
recovery_backup, recovery_required}`.

- `screen`: the workspace has a desktop and its VM is up, so the `display` op answers: viewers can
  watch it from power on (firmware, boot, provisioning), before `state` is `running`.

- `progress`: while `operation` is `preparing`, what provisioning is doing, worded for people
  ("Installing your desktop — the first start takes a few minutes…"); `null` otherwise.

- `state` ∈ `running`, `stopped`, `failed`.
- `operation` ∈ `preparing`, `stopping`, `recovering`, or null.

### Ops

| Op | Params | Reply | Op-specific errors | Behaviour |
|---|---|---|---|---|
| `status` | `api` | `api`, `worker_id` | `unsupported` | Handshake: version check and identity. The backend checks `worker_id` against the machine it expects |
| `list` | `watch?` | `{id → record}` | — | Snapshot. With `watch`, the connection then receives `{"event":"workspace","id","record"}` per change; `record:null` means deleted |
| `catalog` | — | `distributions`, `desktops`, `tools`, `stacks`, `wallpapers` | — | What a spec may select (§8), read again from the distribution folders at every `catalog`, `configure` and `start`: the distributions (`id`, `name`, `detail`, `features`, `minimum`); the `problems` (`id`, `problem`: a folder whose `distribution.json` is missing or unreadable, or an id in two folders; a spec naming it gets that problem as its error); the desktops (`id`, `name`, `distributions`: where it is available); the tools not `hidden` (`id`, `name`, `detail`, `category`, `distributions`, `requires?`); the preset `stacks` (`id`, `name`, `description`, `tools`); and the `wallpapers` (`id`, `name`) |
| `distribution` | `distribution`, `files` | `distribution`, `folder`, `files` (`[{path, bytes}]`), `problem`, `writable` | `invalid`, `conflict` | Writes files of a distribution in sentineld's own folder (`<state>/distributions/<id>`, §8), creating it: `files` maps a path inside the folder to `{text, executable?}` (write) or `null` (remove); other files stay. `conflict`: the id is in a folder sentineld does not write |
| `distribution_read` | `distribution`, `path?` | as `distribution`, or `path`, `text`, `executable` | `not_found` | Any distribution's folder and files, or one file (at most 1 MiB) |
| `distribution_delete` | `distribution` | — | `not_found`, `conflict` | Removes a distribution sentineld wrote; `conflict` (with `workspaces`) while a workspace uses it |
| `help` | `topic?` | `text` | `not_found` | The texts `sentineld help` prints (`core/help/`), for clients that drive sentineld from elsewhere: the overview, or `distributions` (how to write one) |
| `source` | `url` | `url`, `state` (`downloading`\|`ready`), `bytes`, `sha256` when ready | `invalid`, `unavailable` | Downloads a file for a recipe to pin into `<cache>/sources/<sha256>` (through `<cache>/fetch/`) on a worker thread; asking again follows the same download, and its error is replied once. Kept from pruning while sentineld runs |
| `source` (browse) | `url`, `match` or `paths` (+ `offset`, `length` with one path), `limit?` | `members` `[{path, bytes, type, target?}]` and `matched`; or `files` `{path → {bytes, text \| binary, offset?}}` | `conflict` (not downloaded), `too_large`, `unsupported` | Inside a downloaded archive (libarchive, read once through on a worker thread): what a recipe can use from it. A reply over `limit` characters (default 50000) is refused, with the matches per top folder or each member's size |
| `log` | `workspace`, `lines?` | `console`, `build`, `failure` | `not_found` | The last lines of the workspace's newest console log and of the console of the install or layer it last waited for (running, or the last failed one, §7) |
| `usage` | — | `workspaces` `{id → bytes}`, `shared` (`systems`, `packages`, `sources`, `installs`, `distributions`), `total`, `free`, `capacity` | — | Disk space by what holds it (§18), a workspace by its files' own bytes (`Platform::ownBytes`: APFS's private size, so its clone of a base counts only what it changed): what deleting it frees; an installed base whole (its clones share its blocks), a layer by what it adds to its base |
| `configure` | `workspace`, `revision`, `spec` | `record` | `conflict` | Create or edit, with the spec rules above |
| `start` | `workspace`, `revision`, `erase?`, `spec?` | `record` (`operation:"preparing"`) | `conflict` | Async: boot and provision, with progress as events. `erase:true` reinstalls and may carry a new `spec`. Erasing cancels any job and powers off without the guest. An existing workspace disk boots without touching the base disk. `erase` first deletes the disk and EFI vars, so a failed reinstall never boots old data under a new spec |
| `stop` | `workspace` | `record` | `conflict` | Synchronous. Cancels a `preparing` job, asks the agent to shut down within the distribution's `shutdown_s` (15 s by default), then powers off regardless |
| `delete` | `workspace` | — | — | Synchronous. Cancels any job, powers off without the guest, removes the disk and the catalog entry. Never touches `project` |
| `recover` | `workspace` | `record` (`operation:"recovering"`) | `conflict` | Async. Rescue boot repairs the disk after an APFS-clone backup (§12), then re-provisions. `recovery_backup` arrives in a later event |
| `exec` | `workspace`, `argv` (each element ≤128 KiB, Linux `MAX_ARG_STRLEN`; request ≤1 MiB), `timeout` | `code` (null when `timed_out`), `stdout`, `stderr`, `truncated`, `timed_out` | `conflict`, `guest` | One-shot. `stdout` and `stderr` are each ≤4 MiB as encoded JSON strings, UTF-8 with invalid bytes replaced by U+FFFD. Binary data goes through `process`. It's one round trip and the backend's main command path. Allowed while running, and while preparing once the VM is up (to see what a long first start is doing) |
| `process` | `workspace`, `argv`, `tty?`, `cols?`, `rows?` | `started`, then binary frames (§4) | `conflict`, `guest` | Interactive and streaming |
| `forward` | `workspace`, `port` | `socket` (path on the sentineld host: `<state>/forwards/<id>-<port>.sock`, or `/tmp/sentinel-forward-<sha256(state)[:16]>/…` when that exceeds the socket path limit) | `conflict`, `guest` | Guest TCP port as a Unix socket. Lives until the VM stops. Remote clients forward the path over SSH |
| `keyboard` | `workspace`, `layout`, `variant?`, `model?`, `options?` (XKB, comma lists) | `applied` (`{layout, variant}` the guest kept, or `null` when the workspace is not running) | `unsupported` (no desktop), `guest` | Keeps the viewer's keyboard layouts for the workspace and applies them to its desktop, now when running, and after every desktop start (§8) |
| `clipboard` | `workspace`, `action` (`read`\|`write`), `text` (write, ≤256 KiB) | `text` (read) | `unsupported`, `conflict` (not running), `guest` | One clipboard transfer with the running desktop (§8) |
| `wallpaper` | `workspace`, `design` (a catalog wallpaper) | — | `unsupported`, `conflict`, `guest` | Sets one of the share's wallpapers on the running desktop (§8) |
| `computer` | `workspace`, `actions` (≤16), `viewport` (with actions) | `batch`: `ok`, `completed_actions`, `error?`, `viewport`, `cursor`, `screenshot` | `unsupported`, `conflict` | One computer-use batch on the running desktop (§22); a failed batch still reports what it completed and the screen |
| `computer_cancel` | `workspace` | `cancelled` | — | Stops the workspace's running batch after its current action |
| `display` | `workspace` | `port`, `cert_sha256`, `ticket`, `expires` (Unix seconds) | `conflict` | Issue a viewer ticket for the workspace's running desktop (§6) |
| `shutdown` | — | — | — | Acknowledged at once, then drains (§19) and exits. The connection's EOF means the state directory is free. A remote update uses it after stopping the approved workspaces (§23) |

### Platform interface (shape only; detail in the macOS backend stage)

```cpp
struct Platform {
  virtual ~Platform() = default;
  // Non-blocking: results and on_exit arrive on the core's event loop (§15).
  virtual Vm create(const VmSpec&, std::function<void(Vm&, Exit)> on_exit) = 0;  // unexpected exit too
  virtual void start(Vm&, Done<void>) = 0;
  virtual void power_off(Vm&, Done<void>) = 0;        // graceful shutdown is the core's job, via the agent
  virtual void connect(Vm&, uint32_t vsock_port, Done<Fd>) = 0;  // guest agent, forwards
  virtual void clone_disk(const Path& from, const Path& to) = 0;   // APFS clone
  virtual void grow_disk(const Path&, uint64_t bytes) = 0;        // extend raw file while stopped
  virtual Display* display(Vm&) { return nullptr; }   // optional; macOS implements it
};
```

## 6. Media plane

**WebTransport (HTTP/3 over QUIC). UDP is required; there is no fallback path.**

Why not WebRTC: the viewer already decodes H.264 with WebCodecs, and the host already encodes with
VideoToolbox. WebTransport carries those frames as they are. WebRTC would replace both ends with
libwebrtc's pipeline (SDP, ICE, DTLS-SRTP).

**Process:** `sentineld-media`, one per sentineld, started at launch. It is the only process that
faces viewers: the VM processes stay network-free, and
sentineld's control plane never parses QUIC. It holds no workspace state, opens no files (Seatbelt:
loopback UDP only), and exits when sentineld's control socket (its fd 3) closes; sentineld restarts
it if it dies. It speaks NDJSON on fd 3:

- to sentineld: `{"port","hash"}` at start and whenever it rotates the certificate;
- from sentineld: `{"op":"attach","workspace"}` with the VM process's media channel (SCM_RIGHTS, see
  §15), and `{"op":"ticket","workspace","ticket"}`.

**Stack:** ngtcp2 with OpenSSL 3.5 (static, pinned, `make deps`). HTTP/3 is our own minimal subset
(`media/server.cpp`): SETTINGS, one extended CONNECT, WebTransport stream framing. Requests are not
decoded: QPACK runs with no dynamic table, and authentication is the ticket, not the request.
Settings sent: `ENABLE_CONNECT_PROTOCOL`, `H3_DATAGRAM` (RFC and draft ids), WebTransport draft 02
(`0x2b603742`) and draft 07 (`0xc671706a`); the CONNECT response carries
`sec-webtransport-http3-draft: draft02`.

**Trust:** an ECDSA P-256 self-signed certificate valid for 13 days, with subjectAltName (Chromium's
parser requires extensions), as `serverCertificateHashes` requires. Rotated every 6 days;
established sessions are unaffected. `display` returns the current SHA-256 (hex) and a ticket: 128
random bits, single use, valid 30 s, bound to the workspace. On a certificate or ticket failure the
client calls `display` once more.

**Address:** the endpoint binds 127.0.0.1; `display` returns only `port`. Viewers on another host
are a later stage.

**Wire format:** every message is a packet with a 16-byte header (type u8, flags u8, 2 zero bytes,
payload length u32be, timestamp u64be µs) and its payload: 1 decoder config (width u32be, height
u32be, avcC), 2 frame (flag 1 = key frame, Annex-B-free AVCC samples), 3 input, 4 key frame
request (empty), 5 audio (Opus), 6 cursor (hotspot and size u16be each, then straight-alpha RGBA
rows, at most 128×128; empty when hidden), 7 refresh (u16be Hz, 24–240: the maximum refresh rate of
the screen the viewer is on, sent at connect and whenever its window moves to another screen),
8 watching (u8: 1 when a workspace gets its first viewer, before that viewer's key frame
request; 0 when its last viewer leaves), 16 hello (payload: the ticket).

**Watching:** the VM process encodes only while its workspace has viewers. Unwatched, it still keeps
its newest frame (computer use's screenshots read it) but skips the encoder, so a desktop animating
with nobody looking costs no video encoding. A viewer's arrival sends the current picture as a key
frame at once, even from a desktop that does not change. A key frame request also counts as being
watched, so an older sentineld-media that never sends `watching` still gets video.

**Refresh rate:** the workspace's display runs at its viewers' rate, as a monitor does at its own.
sentineld-media sends the VM process the highest rate among a workspace's viewers when it changes.
The VM process starts at the rate of the screen it booted on and sets its display's live rate
(libkrun `krun_display_refresh_set`): the display's EDID then says it, and VIRTIO_GPU_EVENT_DISPLAY
makes the guest read the display again, so desktops that apply a display's preferred mode
(Wayland compositors) take it live; X11 desktops keep the rate they started with. A guest kernel
without virtio-gpu vblank (before Linux 6.19) is paced by the device instead: each fenced
RESOURCE_FLUSH is answered at the display's next refresh, and the guest reports the frame shown
only then, as a real display paces its updates.

| Channel | WebTransport primitive | Contents |
|---|---|---|
| Control | the one bidirectional stream the viewer opens | Viewer to host: `hello` first (else the session is closed; 5 s limit), then input, key frame requests and refresh rates. Clipboard later |
| Video | one unidirectional stream per frame | One `frame` packet; a key frame's stream starts with the current `config`, so a viewer never waits on another stream to decode |
| Audio | the control stream, with cursor changes | One `audio` packet per 10 ms Opus frame (48 kHz stereo) of the guest's sound card (§16), in order |

**Pacing:** a viewer with 3 unsent frames has them reset and waits for the next key frame (a
request goes to the VM process), so it shows current video, never a backlog. A new viewer starts at a
key frame. Key frame requests reach the VM process at most once per 250 ms per workspace. QUIC
keep-alives every 2 s hold an idle desktop's session open.

# Stage 2: guest

## 7. Base disks

Every distribution, Linux and Windows alike, is built by sentineld from its folder's `install`
recipe (§8): no registry and no image builds, nothing about any one OS in sentineld, and a folder
sentineld can build alone (so a workspace can write one and iterate on it). Each base disk is made
once per recipe and cached; workspaces clone it.

1. **Cache:** `<cache>/images/install-<key>.boot.raw`, and `install-<key>.root.raw` when the recipe
   has a root disk; the key hashes the recipe and the folder's `install/` files, so unrelated edits
   to the distribution reuse the disks. The boot disk is stored last: with it, the base is complete.
2. **Sources:** each pinned by SHA-256 (`<cache>/sources/<sha256>`), downloaded over HTTP or HTTPS
   (the digest is the trust, and Microsoft serves its images over plain HTTP), hashed while
   streaming and renamed into place once verified. Removed once the install succeeded.
3. **Disks:** `disk` lays out the GPT (`gib`; `partitions`, each `esp`, `msr`, `basic` or `linux`
   with `mib`, the last taking the rest) in `<cache>/install/<key>/target.raw`, its disk and
   partition GUIDs derived from the recipe (stable across installs, so the recipe can name them).
   `root` (`gib`) adds `root.raw`, a file system on the whole disk with no partition table, so a
   workspace grows it from the host (5.).
4. **Steps** run in that work folder, each seeing `{work}`, `{install}` (the folder's `install/`),
   `{disk}` and `{disk.guid}`, `{root}`, each partition's `{N.guid}`, `{N.start}`, `{N.sectors}`,
   `{N.offset}`, `{N.size}`, `{N.kib}`, and each source by name: `run` (one of sentineld's own tools,
   `tools/{mke2fs,wimlib-imagex,mkntfs,hive}`, with an optional `env`, sandboxed: it reads its
   inputs and writes only the work folder), `extract` (archive members matching `include`, through
   libarchive), `copy` (a file or tree, cloned on APFS), `archive` (packs inputs, archives' members
   matching `include` as recorded and folders' entries as root's, into one `tar` for `mke2fs -d` or
   a `newc` initramfs; a file several inputs have is the last one's) and `fat32` (formats a
   partition and fills it from a work folder with FatFs, built in: nothing mounted). Paths a step
   writes must be inside the work folder.
   - **Linux** (Alpine, for one): the sources are the distribution's root tarball and packages. The
     host lays out an ESP with a prebuilt boot loader, a boot entry, the kernel and a first-boot
     initramfs (`archive`), and an ext4 root (`mke2fs -d`) from the root tarball plus the folder's
     files. The first boot installs the system with the distribution's own package manager, points
     the boot entry at its kernel, reports and powers off.
   - **Windows** is built entirely on the host, with no installer VM: `mkntfs` formats the Windows
     partition and `wimlib-imagex` applies the image into it (both through ntfs-3g, patched to
     address one partition of a disk file: `NTFS_OFFSET`, `NTFS_SIZE`); `hive` edits the image's
     offline registry (`viostor` registered as boot-critical in the SYSTEM hive's DriverDatabase)
     and turns its `BCD-Template` into the boot store; the EFI system partition holds the image's
     boot manager. The virtio-win drivers are those of the machine's devices (disk, network, vsock,
     input, rng) less the balloon: libkrun's serves free-page reporting only, and Windows' balloon
     driver waits at power-off for queues it never answers (5 minutes of "Shutting down", then
     `DRIVER_POWER_STATE_FAILURE`). The overlay (unattended answers, `SetupComplete.cmd`, the agent
     task) is added to the image before it is applied.
5. **Boot:** the real VM stack, like every VM's: the distribution's machine booting the target disk
   (and the root disk), a display, `network` (default on; Windows' is off so first boot skips its
   online checks), `cpus` and `memory_gib` (at most half the host's memory; it is the guest's file
   cache: too little paces every cached write). The firmware starts the ESP's boot loader and the
   guest finishes its own setup. sentineld starts the VM again on every guest reboot. The install
   ends when the guest powers off: `sentinel/result` on the target's EFI system partition says `ok`
   or why it failed (Windows' `SetupComplete.cmd` writes it, without a drive letter); a guest that
   powers off without one, a VM failure, or `timeout_min` fails it. A failed install keeps its log
   and disk under `<cache>/install/` for a look, until the next attempt or a success.
6. **Workspace disks:** `clone_disk` of the base's disks (the root first, the boot disk last, so a
   workspace with a boot disk has them all), then `grow_disk` to `disk_gib`. A base without a root
   disk (Windows) keeps everything on one disk, which the guest grows itself (its distribution's
   `grow`: Windows extends C: only when that gains at least 1 MiB: an ungrown disk's C: ends 1 MiB before
   the disk's end, 2014 sectors short of the GPT's last usable one, and `Resize-Partition` refuses
   to add less than 1 MiB, which failed the command at every start). A root disk is grown on the host instead: `grow_disk` of the file, then the `resize2fs`
   helper (built with `mke2fs`) before the guest boots, a no-op once full size. The root has no
   partition table so that growing is only this: a file system inside a partition needs offsets,
   which `resize2fs` honours for the file system but not for the file (it truncates the image to the
   file system's size, cutting off the disk's end), and the guest kernel's online resize races its
   own bitmap prefetch (`bad block bitmap checksum`, writes fail with EUCLEAN). A file system that
   cannot grow (not cleanly unmounted, or a journal to replay after a hard stop: its superblock says
   so, and growing it corrupts it) boots at its size and grows on a later start.

7. **Layers:** a workspace starts from its layer, `<cache>/images/layer-<key>.{boot,root}.raw`, the
   distribution's base with the workspace's software installed: its shell userland when the share
   holds it (§8 `userland`: Windows' MSYS2), its tools (the hidden base ones, then the spec's, in plan
   order) and its desktop (§8). The key hashes the base's name, the userland and those install
   scripts, so each combination is installed once, not in every new workspace. A layer is
   made like a workspace's first start: a copy of the base (its root grown to 8 GiB, the smallest
   workspace disk) boots with the share and the package cache, the agent runs the scripts in order
   and then the distribution's `poweroff`, so the root is clean for workspaces to grow; one that
   does not stop within 60 s is stopped and the layer fails. `<layer>.parent` names the base, which
   the cache keeps while the layer is. A distribution builds one layer at a time (they share its
   package cache, which two package managers at once race on). The workspace's own steps then
   only check the software.

A stop, delete or drain cancels an install no current boot waits for; the next start installs
again. **Open:** package versions a first boot fetches are pinned only for Debian (its build installs
from snapshot.debian.org at the recipe's date); Alpine keeps only its newest packages, and
snapshot.ubuntu.com serves at a tenth of the archive's speed (a 13-minute first install), so those two
install what their archives have that day.

## 8. Distributions and provisioning

sentineld and the agent know no distribution by name. Everything that differs between
distributions is data in one folder per distribution, `<id>/`, in the folders sentineld is given:
`--distributions DIR`, any number of times (default: `distributions/` beside its `bin/`, the ones it
ships, from `guest/distributions/`), and its own, `<state>/distributions/`, the one the
`distribution` op writes. Every folder is treated the same; sentineld writes only to its own and
reads them all again at every `catalog`, `configure`, `start` and distribution op. An id in two folders is a
problem, not a choice; a distribution a workspace uses stays as last read while its folder is broken
or gone. Supporting another OS is adding a folder:

| In the folder | What it is |
|---|---|
| `install/` | the files its `install` recipe (§7) uses |
| `distribution.json` | how the distribution does what sentineld and the agent need (below) |
| desktop service files | the session's service per display system, named by `desktop.session` |
| `desktops/<desktop>/` | that desktop on this distribution, run as the desktop user: `session` (starts it), `keyboard` (applies layouts, live, from `XKB_*`), `clipboard read\|write` (text on stdout/stdin), `wallpaper IMAGE`, `type` (types stdin's text, for computer use, where the desktop does not `paste`), `defaults/` (the default look: a home folder tree) |
| anything else | files those name (Alpine: `wayland-session.sh`, its seat handling for Wayland) |

`distribution.json`:

| Field | Used by | Meaning |
|---|---|---|
| `name`, `detail` | the `catalog` op | what clients show |
| `features` | the `catalog` op | the guest features clients may use: `shell` (POSIX shell commands through `exec`: agent tools, metrics, session cleanup), `terminal` (tmux terminals), `files` (file browsing). Every distribution has all three; Windows through its POSIX userland (§9) |
| `guide` | the `catalog` op | what agents working in the workspace's shell are told about it: its OS and shell, where the project is, how to install packages (the backend's runtime context) |
| `minimum` | `validate`, the `catalog` op | the least `resources` (`cpus`, `memory_gib`, `disk_gib`) the guest runs well with; a spec below it is rejected. Windows 11: 8 GiB of memory (below, its file cache starves: a 2 GB copy took 690 s at 4 GiB, 6 s at 8) and 32 GiB of disk (its own installer asks 64; this install does not run it, and 32 holds the installed system with room to update) |
| `machine` | the VM (§16) | `transport` (`pci`; default virtio-mmio) and `gpu` (`3d`: every renderer context, Venus, virgl and Neptune, for Windows' viogpu3d; `2d`: framebuffers and a cursor only, for a guest whose driver drives nothing more; `none`: no virtio GPU, the guest draws into the firmware's framebuffer; default Venus). The GPU is on the machine's transport |
| `install` | base disk (§7) | `sources`, `disk`, `root?`, `steps`, `cpus`, `memory_gib`, `network`, `timeout_min` |
| `share` | the VM (§17) | `disk`: the share as a read-only FAT32 disk sentineld writes at each start (guests without virtiofs); default virtiofs |
| `hostname` | provisioning | the command that sets the hostname, `{name}` the workspace's |
| `env` | the Windows agent | variables every command it runs gets (`%VARIABLE%` expanded): Windows 11 puts its POSIX userland first on `PATH`, `HOME=/root`, `TMPDIR=/tmp`, `TERM` (the Linux agent's `run` environment, §11), and `MSYS=noglob` (MSYS2 programs then read their command line as a shell's words, unglobbed: in glob mode they cut every quoted argument at 8186 characters) |
| `userland` | layers (§7) | for a guest whose shell userland comes from the share (Windows' MSYS2, §9): `run`, the command a layer runs first, before the tools' scripts (which run in it), and `key`, the share files it installs from, whose contents key the layer: a new userland makes new layers. A distribution with one always starts its workspaces from a layer |
| `grow` | provisioning | for a guest that grows its own system disk (an installed distribution): the command that extends its file system after sentineld grew the disk file; a no-op at full size |
| `ready` | agent `status` | a command that succeeds once init has finished booting |
| `poweroff` | agent `shutdown` | a command that powers the guest off |
| `shutdown_s` | `stop`, sentineld's exit | how long the guest may take to power itself off before it is powered off regardless (default 15). Windows 11: 120, as its shutdown takes 3–15 s and longer while it services itself, and a power off cut short is a crash to NTFS (after forced ones at 15 s, restarts slowed and one did not boot) |
| `packages` | `sentineld-agent install` | the package manager: `installed` (succeeds when every package given is installed), `prepare` (commands run first), `refresh` (the index, once per boot), `fetch` (run under the stall watchdog: must be safe to stop) and `install` (then, when there is one, uninterrupted); `env`; `cache`/`no_cache`, the arguments an argument `{cache}` stands for with and without the package cache, whose folder replaces `{cache}` inside arguments; `cache_dirs` it needs |
| `service` | the `service` action | commands that enable and start the service `{name}` |
| `desktop` | desktop start | `session` (`x11`, `wayland`: the service file), `install` (where it goes), `stop` and `restart` (commands), `failure` (a command whose output says why a session did not come up) |
| `desktops` | desktop install and start, computer use | per desktop id: `display` (`x11`, `wayland`), `paste?` (the keys that paste: computer use types by pasting from the clipboard), and actions, as for tools |
| `tools` | tool installs | per tool id: actions (below); a tool missing here is not offered on the distribution |

`guest/catalog.json` holds what is the same everywhere: each tool's `name`, `detail`, `category`,
`requires` and `hidden?`, the stacks, each desktop's `name`, and the wallpapers' names (their images:
`guest/wallpapers/<design>.png`). A desktop or tool id a distribution offers that the shared catalog does not
name is the distribution's own: its block's `name` (and a tool's `detail` and `category`) names it,
the first distribution to offer it naming it for all, so a new desktop or tool is data too. All of it is in the share; the `catalog` op lists the
distributions, desktops, tools and wallpapers with where each is available, and `configure` rejects
a distribution, desktop or tool the share does not offer.

First boot: boot → baked service starts the agent from the share → ready (no seed, no cloud-init).
Every start, provisioning runs:

1. The project mount (virtiofs, `nosuid,nodev`).
2. The tools: the hidden base tools every workspace gets, then the spec's `tools`, each after
   what it requires, one provisioning step each (`Installing <name>…`, up to 30 minutes).
3. When `desktop` is not `none`: the desktop's actions (its packages: up to 45 minutes on first use,
   downloaded from the distribution's mirrors), then its start: the `sentinel` user in whichever of
   `video render input seat audio` exist, `/etc/sentinel/desktop` (the desktop, its display system
   and its `session` script), the desktop's `defaults/` copied into the user's home once (marker
   `.config/sentinel/defaults-seeded`) so its first session starts with them, the distribution's
   session service installed and restarted, and a wait for the display server's socket (the
   `failure` output when it does not come). `desktop/session.sh`, the same everywhere, starts an X
   server for X11 desktops and runs the desktop's `session` script on the virtio GPU, whose KMS
   scanout is what viewers see:
   - `xfce`: X11. Xorg (modesetting + glamor) as root, the desktop as the `sentinel` user.
   - `lxqt` (labwc), `gnome`, `plasma`: Wayland; the compositor drives the GPU from a session that
     holds the seat, which the distribution's service provides: on systemd the service is the
     user's PAM login session on tty7 (logind seat); on Alpine seatd, or for GNOME `runuser -l`
     through a minimal PAM stack with `pam_elogind` on tty7 (Mutter takes the seat only from logind).
     The compositors put the pointer on the GPU's cursor plane (§16 Cursor), except KWin 6.4 to 6.6:
     on a virtual GPU they fill the plane by reading their rendering back with a pixel type OpenGL ES
     rejects (fixed upstream by kwin 5e7e8a7b), so the plane stays transparent; the Plasma sessions
     set `KWIN_FORCE_SW_CURSOR` for those versions and KWin draws the pointer into the frame (KWin
     6.3 fills it correctly).
   The GPU is Vulkan only (Venus): each desktop's packages include the Vulkan loader and the
   distribution's Mesa Venus driver, and the session runs OpenGL, the display server's included,
   through Zink (`MESA_LOADER_DRIVER_OVERRIDE`). They also include PipeWire for the sound card (§16).
   **Keyboard layouts:** the viewer sends physical keys, so the desktop types with the layouts of the
   Mac the viewer runs on. On each desktop connect the backend reads the Mac's enabled layouts
   (current first) and, when they changed, sends them with the `keyboard` op. sentineld keeps them
   for the workspace (`keyboard` in the catalog) and has the agent apply them now and after every
   desktop start (step "Setting your keyboard layout…", which never fails the start). The agent
   keeps the layouts the guest's XKB data compiles (libxkbcommon, linked into it), computes the key
   chords computer use types with, and runs the distribution's `desktops/<desktop>/keyboard` as the
   desktop user with `XKB_LAYOUT`, `XKB_VARIANT`, `XKB_MODEL` and `XKB_OPTIONS`; that script applies
   them live through the desktop's own settings (GNOME input sources, Plasma `kxkbrc`, labwc's XKB
   environment plus a reconfigure, Xfce's xfconf plus `setxkbmap`). The chords come back to
   sentineld, which keeps them for computer use (§22). They are applied every time: some of it
   (X11's live keymap) does not outlive the session.
   **Clipboard and wallpaper:** the `clipboard` op reads or writes the desktop's clipboard (text,
   ≤256 KiB) through the desktop's `clipboard` script; the `wallpaper` op puts one of the share's
   wallpapers in the user's home (`.local/share/backgrounds/sentinel/<design>-<bytes>.png`: a
   changed image gets a new path, so no desktop shows a cached one) and runs the desktop's
   `wallpaper` script on it. Windows' agent does both itself, in the user's session (§9): the clipboard's
   Unicode text (its `\r\n` line breaks are `\n` both ways), and the image copied to
   `%LOCALAPPDATA%\Sentinel\Wallpapers\<design>-<bytes>.png` and set filling the screen.
   The catalog remembers the desktop last provisioned on the disk (`desktop_ready`, cleared for a
   new disk) only to word the first install differently from later checks.
4. The hostname: the distribution's `hostname` command with `{name}` the workspace's name made a
   valid hostname (Linux: the share's `hostname.sh`, which also writes a `127.0.1.1 <host> # sentinel`
   line in `/etc/hosts`, adding localhost when the file is missing). None: no step.

Steps with nothing to do are left out: a desktop whose actions are empty and a distribution with no
`desktop` session (Windows' own desktop), the hidden tools a distribution does not offer, the project
mount where the distribution reads the share from a disk (below).

Each provisioning step is a shell script sentineld writes from the data, run through `run`, with
its own time limit and a `progress` message; a failure names the step and its reason.

**Packages** install through `<cache>/packages/<distribution>`, a read-write virtiofs share (tag
`sentinel-cache`) every workspace of the distribution mounts, so a package is fetched once. It is
only a cache: guests write it, and the package managers check each package against their signed
index before installing it. sentineld keeps it under 4 GiB per distribution, oldest files first.
A download with no progress for 90 s is stopped and retried (4 attempts); installing is never
interrupted.

**Actions** (tools and desktops), turned into one script, in this order:

| Action | Command |
|---|---|
| `packages` | `sentineld-agent install …`: the agent, run as a command in the guest, installs with the distribution's `packages` commands; a no-op for installed packages |
| `package_url` | a vendor's package file (`url`, or one per `uname -m`) downloaded and installed by the package manager, unless `version` matches |
| `release` | a pinned upstream release: downloaded, checked against its sha256/sha512 (per `uname -m` when the URL is per architecture), unpacked (`binary`, `tar` with `strip`, `zip`) `into` a folder or `install`ed file by file, unless `version` matches |
| `npm` | `npm install --global` of a pinned package, unless `version` matches |
| `links`, `files` | symlinks and small config files |
| `service` | the distribution's `service` commands: enabled and started at boot |
| `requires` | (in a distribution's block) more tools this one needs there |

The hidden `base` installs what Sentinel itself needs in every workspace (shells, tmux, git, curl,
Python, ripgrep, jq…); the hidden `browser` requires the default browser: Chromium where it is a
plain package (Alpine, Debian), Google Chrome on Ubuntu (whose Chromium is a Snap). The catalog
installs software; it creates nothing to run (no databases, no clusters): users and the agent do.

Boot failures: a `Kernel panic` on the guest console fails the start at once, with a ≤1 KiB
printable excerpt beginning at the panic line. The 10-minute readiness timeout's error includes the
last 1 KiB of console output.

## 9. Agent

- **Binary:** C++, cross-compiled on the host from `guest/agent/` with the pinned zig toolchain (its clang and
  musl; Alpine's libxkbcommon) as a static aarch64 binary: no container. It implements
  only kernel and socket protocols (vsock, `uinput`, Wayland/X11 through static libwayland and
  libxcb); anything needing distro libraries (PipeWire, PAM, logind) runs as a spawned process.
- **Delivery:** from the share, mounted at `/run/sentinel` by the service each install sets up (§8:
  systemd `Restart=always` + `ExecStartPre`, or OpenRC `supervise-daemon` + `start_pre`). A VM never
  outlives its sentineld, so the agent is always current: no update code, no version check. What it
  does differently per distribution (readiness, power-off, packages) comes from its
  `distribution.json` in the share.
- **Windows:** `guest/agent/windows/agent.cpp`, the same protocol on Win32, built static for ARM64
  with the pinned zig toolchain (its clang and mingw) into `share/windows/sentineld-agent.exe`. vsock through
  virtio-win's viosock Winsock provider (its address family asked of `\??\Viosock`). It runs from the
  share disk (§17), started at boot as SYSTEM by the `Sentinel agent` task the install registers
  (`install/overlay/ProgramData/Sentinel/agent-task.xml`, `agent.cmd`), so it too is always current. The install
  registers it from `SetupComplete.cmd` (Windows runs it once, as SYSTEM, when setup completes), which
  also keeps the machine on (no sleep or hibernation: either powers the VM off) and its clock in UTC. Today: `status`,
  `run` (pipes, no terminal), `shutdown`, and the desktop's `type`, `clipboard` and `wallpaper`. Those act on
  the signed-in user's desktop, which the agent in session 0 cannot reach (SendInput, the clipboard and
  `SystemParametersInfo` work only from a process in that session, on its interactive desktop), so the
  agent starts its own executable there for each, `sentineld-agent.exe desktop type|clipboard read|clipboard
  write|wallpaper <design>`: with the console session's user token (`WTSQueryUserToken`), elevated through
  UAC's linked token (the user is an administrator: input then also reaches elevated windows), on
  `winsta0\default`, text on its stdin and stdout, why it failed on its stderr, stopped after the op's
  time limit. None of it needs a user process running beforehand; before the first sign-in the ops fail
  ("no one is signed in"). Keyboard layouts are not applied (`keyboard` is unsupported): Windows types
  with its own (en-US), which computer use's US codes match.
- **Windows' POSIX userland:** the backend's shell commands, terminals, file browser and agent tools
  run in Bash with tmux, Python 3, Git and GNU tools, as on Linux, so on Windows they run in MSYS2, not
  in Windows-specific variants (PowerShell has no tmux, which terminals and the agents' panes are).
  `guest/msys2/sources.txt` pins its base system and the packages Linux's base tool has (tmux, Python,
  Git, jq, unzip; ripgrep and GitHub's CLI as native ARM64 builds, the rest x64 under Windows'
  emulation: MSYS2 has no ARM64 runtime); `make share` puts them in `share/windows/msys2` with a
  `version`. The distribution's `userland`, `userland.cmd`, installs them into `C:\msys64` once per
  layer (§7), before the tools: the base with Windows' `tar`, a first login (its pacman keyring), the
  packages with MSYS2's own pacman, offline, and the folder's `tmux` in front of MSYS2's (about 4
  minutes, once per base and userland; workspaces clone the layer and start with it). Like the Vulkan
  driver it comes from the share, so a new userland needs no reinstall of the base, only new layers;
  workspaces made before it keep theirs. A layer of a guest that reads its share from a disk boots the
  installer's machine (its display, so devices keep their addresses) with the share disk, and at least
  the distribution's `minimum` memory; it powers off with the distribution's `poweroff`. The agent
  reads its distribution's `env` at start and sets `SENTINEL_SHARE`, the share's root. Terminals need
  no terminal from the agent: tmux owns the panes' terminals (MSYS2's ptys, which give Windows
  programs in them a pseudo console) and the app drives tmux in control mode over pipes. tmux's server
  reads and writes a control client's stdin and stdout itself, through descriptors passed over its
  socket, which MSYS2 cannot pass for pipes; a terminal it opens by name, so the folder's `tmux` runs a
  control client on pipes on a pty of its own (`script`), raw and without echo, which passes the
  protocol unchanged. Windows has no project folder yet (§17): a workspace's directory is in the VM.
- **Owns:** exec, process, forward, the project mount, shutdown, the hostname (set from the
  workspace name on start) and readiness. Desktop work (session, clipboard, keyboard layouts,
  computer use) runs as scripts from the share through `run`; display, input and sound are VM
  devices (§16), not the agent's.

## 10. Transport

vsock port 1, host → agent, one operation per connection (per-connection flow control, no
multiplexer). Stage 1 binary frames (§4, ≤1 MiB) plus 4 `request` (JSON, first), 5 `started` and 6
`stderr` (`split` only); after `started`, sentineld relays frames verbatim, validating headers.
Every agent wait is bounded (60 s default).

## 11. Agent API

| Op | Params | Then | Purpose |
|---|---|---|---|
| `status` | — | one `output` (JSON `{ready}`), `exit` | Readiness |
| `mount` | `path` | `exit` | virtiofs `project` at the host path |
| `run` | `argv`, `cwd`, `env[]`, `tty`, `cols?`, `rows?`, `stderr` `merge`\|`split` | `started`, `output`/`stderr`…, `exit`; accepts `stdin`, `resize` | Stage 1 `exec` (`split`), `process` (`merge`), provisioning |
| `forward` | `port` | `started`, then raw bytes | Guest TCP port |
| `keyboard` | `layout`, `variant`, `model`, `options` | one `output` (JSON `{layout, variant, keys}`: what was kept, and the key chords), `exit` | Keyboard layouts (§8) |
| `clipboard` | `action`, `text?` | one `output` (JSON `{text}` for read), `exit` | Clipboard (§8) |
| `wallpaper` | `design` | `exit` | Wallpaper from the share (§8) |
| `type` | `text` (≤4000 bytes) | `exit` | Computer use typing on desktops that do not paste (§22) |
| `shutdown` | — | `exit`, then power-off | Stage 1 `stop` |

- **`ready`:** systemd reports `running`/`degraded` or OpenRC has reached `default`, **and** an IPv4
  default route exists (`/proc/net/route`; Alpine reports networking up before DHCP binds). Only then
  is the workspace `running`.
- **`run`:** root, new session (`openpty` + `TIOCSCTTY` for `tty`). Closing the connection SIGKILLs
  the process group; exit code 128+signo when killed by a signal. `cwd` is the project; `env` is
  exact: `PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`,
  `HOME=/root`, `TMPDIR=/tmp`, `TERM=xterm-256color`. Windows: SYSTEM, pipes only (`tty` is not
  honoured; nothing asks for it, §9), the agent's environment (its distribution's `env`), and `argv[0]`
  found on `PATH` alone (Windows' own search takes System32 first: its `tar`, `curl` or WSL's `bash`),
  its arguments quoted for the program's runtime: the C runtime's rules, or a shell's for an MSYS2
  program (one with `msys-2.0.dll` beside it, under `MSYS=noglob`). Receives wait at most a second
  each: viosock never completes a pending receive when the host resets the connection, which the next
  receive sees.
  The process and what it starts are a job, killed when the connection closes before the process
  exits; what it leaves running after a normal exit stays (Windows has no sessions to leave: this is
  how tmux's server outlives the command that started it).
- **Stage 1 `exec`:** `run{split}`, stdin EOF at once. Past 4 MiB, sentineld drains, discards and
  sets `truncated`. It times out by closing the connection.
- **`mount`:** a project at or under `/proc`, `/sys`, `/dev`, `/run`, `/boot`, `/etc`, `/usr`,
  `/bin`, `/sbin`, `/lib` or `/var` is rejected by `configure`, and again by the agent.
- **`forward`:** one vsock connection per TCP connection. One `poll` loop with a 256 KiB buffer per
  direction runs until both sides half-close.

## 12. Shutdown and recover

- **Shutdown:** the agent replies, then signals PID 1 (systemd `SIGRTMIN+4`; openrc-init's poweroff
  signal); Windows' agent replies once `InitiateShutdownW` accepted the power off (an error if not).
  After the distribution's `shutdown_s` (15 s; Windows 120 s), if unreachable or on an error, `power_off`. `delete`/`start{erase}` skip the agent.
- **Recover:** rescue boot, no new agent op. Power off; APFS-clone the faulted disk as
  `recovery_backup`; boot a fresh clone of the same base disk with the faulted disk's directory as a
  read-write virtiofs share (not a block device: identical labels would confuse boot); `run`
  `losetup -fP` and `e2fsck -f -p` on its root; discard the clone; re-provision.

## 13. Trust: the guest is untrusted

- An unknown frame type, `len` over 1 MiB, or a frame invalid in the current state closes the
  connection. JSON is ≤4 KiB with a strict schema; `code` is 0–255. All guest-sourced
  text in errors is valid UTF-8 without control characters; console excerpts are ≤1 KiB.
- Host timers, vsock flow control and sentineld's caps bound every wait and all memory. Forwards
  reach only guest-side TCP; the share is read-only. The only guest-initiated vsock connection the host
  accepts is computer use on port 1101 (§22), which drives and reads only that guest's own display.

## 14. Open items

- **To verify:** Zink on Xorg (xfce) and on Alpine's Mesa, openrc-init's poweroff signal, Alpine's GRUB kernel hook;
  GNOME clipboard (Mutter lacks
  Wayland data-control).

# Stage 3: macOS backend

`Platform` is implemented in `macos/backend/workspace.cpp` and linked into sentineld. Each VM runs
in its own child process, the same binary as `sentineld vm` (`macos/vm`): libkrun on
Hypervisor.framework, which runs one VM per process. The daemon itself never creates a VM. Base disk installs are portable core code (§7), not part of `Platform`.
Every API used is available on macOS 26, which stays supported (the 4 KiB guest page granule,
`hv_vm_config_set_ipa_granule`, is public from macOS 26).

Why not Virtualization.framework: it has no 3D virtio-gpu, so desktops needed a GPU transport of our
own beside it (a guest kernel module, a guest device and a host renderer). libkrun gives the guest a
real virtio-gpu (Venus: Vulkan forwarded to the host's Metal through KosmicKrisp), so stock
distribution kernels and Mesa drive it, as on any KVM host.

## 15. Process model

- **One `sentineld vm` per running VM**, spawned by `start` with `POSIX_SPAWN_CLOEXEC_DEFAULT`
  and only these fds: 0 `/dev/null`, 1 the console (a pipe sentineld reads), 2 the VM log
  (`workspaces/<id>/workspace.log`, appended), 3 the control socket, 4 the media channel (§6;
  desktops only).
- **Control socket (fd 3):** sentineld writes one JSON line, the VM configuration (§16); the process
  answers `{"event":"running"}` once the VM is built and sandboxed, then runs it. EOF on fd 3 means
  the process ended; sentineld reaps it with `waitpid(WNOHANG)` on its event loop, never blocking.
- **Exactly one `on_exit` per run**, including after `power_off`. An exit status other than 0 or
  SIGTERM is an error carrying the last line of the VM log.
- **Generation tags:** each run carries a generation; events for a replaced run are dropped.
- **Disk calls are synchronous:** `clone_disk` (`clonefile(2)`) and `grow_disk` (`truncate`, sparse)
  run only on stopped VMs. A non-APFS state dir is an error.
- **Sandbox:** the process applies its own Seatbelt profile before the VM runs: no exec or fork, and
  writes only to its disks, read-write shares, its socket directory and the per-user cache and temp
  directories (the Mesa shader cache).

## 16. VM configuration

The config line sentineld writes on fd 3:

| Field | Value |
|---|---|
| `boot_disk`, `root_disk` | virtio-blk `boot.raw` then `root.raw` (both rw, raw); no `root_disk` for an installed distribution |
| `transport`, `gpu` | the distribution's `machine` (§8), when not the defaults |
| `share_disk` | a distribution whose `share` is `disk`: `<workspace>/share.raw`, attached read-only after the system disk (§17) |
| `sync` | `spec.disk_sync`: `full` (a flush is `F_FULLFSYNC`) or `fsync` (relaxed: can lose the last acknowledged writes on host power loss only) |
| `cpus`, `memory_mib` | `spec.resources` |
| `mac` | `02:` + the first 5 bytes of SHA-256(workspace id): stable across boots and erase |
| `shares` | `[{tag, path, read_only}]` (§17) |
| `agent_socket` | the unix socket libkrun maps to guest vsock port 1 (§17) |
| `display` | desktops only: `{width, height, hz, service_socket}`; 1920×1200 at 120 Hz |

What the process builds from it:

| Item | Value |
|---|---|
| Boot | Sentinel's EFI firmware (`share/firmware/SENTINEL_EFI.fd`, edk2 `ArmVirtSentinel`, `patches/edk2-*.patch`) boots the disk's own boot loader (§7): any OS, no kernel or command line from the host. It finds virtio-mmio and virtio-pci devices, gives the OS a device tree and ACPI tables built from it, shows the Sentinel logo (ACPI BGRT), and needs a legacy serial port, which gets one whose input never sends |
| Reboot | a guest reboot (PSCI `SYSTEM_RESET`) ends the process with status 3, and sentineld starts the machine again from the same disks |
| Network | virtio-net over a datagram socketpair to an in-process libslirp: user-mode NAT, no root, no vmnet, portable. Guest 10.0.2.15 (DHCP), gateway 10.0.2.2, DNS 10.0.2.3. The gateway does not reach the host's loopback (the guest is untrusted, §13) |
| Entropy | virtio-rng (required; without it a ~4 s systemd-random-seed stall, measured) |
| Memory | guest RAM is memory the VM process owns, in 2 MiB chunks, so macOS charges it once (plain anonymous memory was charged twice once the host wrote into it, e.g. on disk reads). A virtio-balloon with free-page reporting returns what the guest frees: the reported chunks are replaced with fresh ones, so the footprint follows the guest's use (measured: 2.5 GB page cache dropped, 2.8 GB → 0.3 GB) up to its RAM size |
| Console | virtio console port 0 on a pty, so the guest sees `hvc0`; its output goes to fd 1. sentineld keeps the newest 1 MiB in memory (the source of failure tails) and writes the whole run to `<state>/logs/<id>/console-<UTC time>.log` (up to 16 MiB a run; the newest 5 runs kept) |
| Boot screen | with a display, a boot framebuffer (QEMU's ramfb model) the firmware sets up and the OS keeps drawing into until its GPU driver shows a frame: viewers see the machine from power on (Windows draws on it until its display driver starts). Without a virtio GPU (`gpu: none`) it is the screen. A Linux guest keeps it as a second framebuffer device beside the GPU; the Linux distributions release it before the desktop starts (`desktop.prepare` runs `desktop/boot-screen.sh`) so a compositor that drives every GPU sees one monitor, the one shown |
| GPU | desktops only: virtio-gpu on the machine's transport, with host-visible memory up to 8 GiB (a virtio-mmio shared memory region, or on PCI a 64-bit prefetchable BAR the host bridge's 64-bit window holds exactly). By default Venus only (virglrenderer in a render thread, no virgl): Vulkan through KosmicKrisp on Metal; a Linux guest runs OpenGL (4.6, ES 3.2) on it through Zink. A machine whose `gpu` is `3d` (Windows) gets every context the renderer has: Venus, virgl (vrend on ANGLE, OpenGL ES on Metal; its fences retire when the device polls the renderer) and Neptune (Direct3D 11 on DXMT), which Windows' viogpu3d drives: its desktop is composited through them, Edge/Chrome are GPU-accelerated. `2d` is a 2D virtio-gpu instead: framebuffers and cursor images in guest pages, read on flush, with no host-visible window, renderer or capsets (`libkrun-virtio-gpu-2d`), for viogpudo (with which Windows keeps the boot framebuffer as a second monitor). On PCI a display device keeps BAR 0 free (registers in BAR 2, the window in BAR 4, as QEMU's): Windows' Basic Display driver, which drives it until its own driver starts, takes BAR 0 for a framebuffer. The display's size: while the guest scans out at another size than the display's, the device raises a display event (`VIRTIO_GPU_EVENT_DISPLAY`, at most every 3 s) so the guest reads it again; viogpu3d boots at 1280x1024 and takes the display's size only on that event, through its display agent (`viogpuap`, which its service starts in the signed-in user's session and which needs the user to be an administrator) |
| Display | the scanout is copied into a pool of BGRA buffers and encoded with VideoToolbox (H.264, 24 Mbit/s), written to fd 4 as §6 packets. Never blocks: past 8 MiB unsent it drops frames until the next key frame |
| Cursor | the guest's cursor plane (`UPDATE_CURSOR`) goes to viewers as a §6 `cursor` packet (type 6: hotspot, size, straight-alpha RGBA; empty when hidden), shown as their native pointer: moving it costs no frame. The image is read wherever the guest's resource keeps it: its backing pages, a blob's host mapping, or the renderer (vrend's 2D textures, which some guests' GBM makes cursors of). The GPU is a VGA-class PCI display (`0x0300`, as virtio-vga): Linux's virtio-gpu driver then retires the firmware framebuffer (simpledrm) when it starts, which it does only for a VGA device; kept, it is a second monitor to compositors, and Mutter turns off the hardware cursor for both. Windows' display driver viogpu3d (v0.3) has no cursor plane (it reports no pointer capabilities, whatever its `HWCursor` parameter says): Windows draws the pointer into the frame, so viewers see a software cursor. Device-instance settings are keyed to the PCI address, so the desktop's devices come first on the transport and their addresses are the same in the installer's machine and the workspace's, whatever disks and network either has |
| Vulkan (Windows) | Windows applications' Vulkan is Venus, through Sentinel's build of Mesa's Venus ICD: `guest/vulkan/build.py` cross-compiles osy/virtio-win-mesa at a pinned commit plus `guest/vulkan/patches/` with the pinned zig toolchain (clang, mingw-w64; the few D3DKMT/DDI headers mingw lacks come from the Windows SDK and WDK NuGet packages, read at build time; every source pinned by SHA-256 in `sources.json`) into `share/windows/vulkan/`: `vulkan_virtio.dll` (ARM64) and `vulkan_virtio_x64.dll` (`x86_64-windows-gnu`, for x64 applications under emulation), from one tree, and their manifests `venus_icd.json` and `venus_icd_x64.json`. A user-mode driver needs no signature. The ICD viogpu3d v0.3 ships does not work: its transport predates the KMD's WDDM2 (GpuMmu) mode, where `D3DKMTLock` maps staging pages rather than the BAR, so the ring's shared memory never reaches the host and `vkCreateInstance` fails (`virtio-win-mesa-01-wddm2` moves it to what Neptune's transport does: the KMD's own windows from `RES_INFO`, lookup cookies, a virtual context and a monitored fence); and its manifest names a bare `vulkan_virtio.dll` the loader cannot open from the driver store (error 87, which the loader mistakes for out of memory, failing every application). `agent.cmd` copies the share's driver to `Program Files\Sentinel\Vulkan` (writable by administrators only, as every process loads it) at each start and names both manifests as the display adapter's `VulkanDriverName` (a `REG_MULTI_SZ`), so a new driver needs no reinstall. An x64 process reads that same value, not `VulkanDriverNameWow` (the loader takes the Wow value only for `IsWow64Process`, which x64 emulation is not), and viogpu3d's INF registers no other Vulkan value (only an ARM64 ICD); each process's loader skips the manifest whose library it cannot load (error 193, logged as the wrong bit type). `-03-sleep`: Mesa's short waits on Windows slept a whole 15.6 ms timer tick (19 ms per submitted-and-waited frame, 0.6 ms with a high-resolution timer). 32-bit (x86, WOW64) applications have no Vulkan: they read `VulkanDriverNameWow`, which nothing sets. Direct3D and Vulkan share a process: each driver has its own D3DKMT device (so its own virtio context), but the KMD resolves a blob's open and `RES_INFO` by the lookup cookie its creator wrote, in one adapter-wide map whose lookup takes the first match, and Neptune's UMD mints `pid << 32 | counter` from 1, as `-01-wddm2` first did too: a process that had used Direct3D 11 got Neptune's allocations back for its own (its ring's shared memory became Venus's ring, `vkCreateInstance` never returned and Neptune's host ring read a command of impossible size). Venus's cookies set bit 31 of the counter, which Neptune's reaches only after 2^31 allocations in one process |
| Sound | desktops only: virtio-snd with one stereo S16 48 kHz playback stream; buffers complete at their playback time (the guest's audio clock is real). The PCM is encoded to Opus on the host (AudioToolbox, 10 ms packets) and sent as §6 `audio` packets. The guest needs only its stock driver and PipeWire (desktop setup installs it) |
| Input | virtio-input: a keyboard and an absolute pointer (0-32767, QEMU's tablet range: Windows reads it through HID, whose logical maximum is signed, so 65535 would be -1) with five buttons and wheels, fed from fd 4's `input` packets (0-65535, halved). Absolute axes with mouse buttons and no relative X/Y make Windows' vioinput expose a HID mouse with absolute axes, not a digitizer |

## 17. Shares and vsock

| virtiofs tag | Host directory | Mode | When |
|---|---|---|---|
| `project` | `spec.project` | rw | always |
| `sentinel` | sentineld's release `share/` (agent, provisioning scripts, wallpapers), mounted at `/run/sentinel` | ro | always |
| `distribution` | the workspace's distribution folder (§8), mounted by the agent at `/run/sentinel/distribution` (the share holds the empty folder) | ro | always |
| `sentinel-cache` | the distribution's package cache (§18) | rw | always |
| `rescue` | the faulted workspace's directory | rw | rescue VM only (§12) |

A distribution whose `share` is `disk` (Windows: no virtiofs driver yet) gets none of these: the share,
with its distribution folder as `\distribution`, is written at each start into `<workspace>/share.raw`
(FAT32, label `SENTINEL`, FatFs as for install media) and attached read-only after its system disk; it
has no project folder yet. A userland `key` path (§7 layers) is in the share, or with `distribution/`
in the distribution folder, as the guest sees them.

| vsock port | Direction | Host end |
|---|---|---|
| 1 | host connects in | `agent.sock`: libkrun listens and forwards each connection to the guest agent (§10). `connect` opens it |
| 1101 | guest connects out | `display.sock`, served by `sentineld vm`: computer use (§22), which sentineld also reaches directly |

Both sockets live in a directory of their own for each VM launch under the user's temp directory
(`sentineld-XXXXXXXX` from `mkdtemp`, mode 0700), short enough for `sun_path`: no other sentineld
or run of the same VM (an install VM's id is its recipe's hash) shares it. No other vsock port reaches the
host.

## 18. State layout

```
<state>/{catalog.json, worker-id, sentineld.sock, forwards/}
<state>/distributions/<id>/   distributions written with the `distribution` op (§8)
<state>/workspaces/<id>/{boot.raw, root.raw, share.raw, workspace.log}   share.raw: `share` `disk` only
<state>/logs/<id>/console-<UTC time>.log   the guest console of each of the last 5 boots
<cache>/images/install-<key>.{boot,root}.raw   a distribution's base disks (§7; <key>: its install recipe's hash; root: when the recipe has one)
<cache>/sources/<sha256>                       install sources, removed once installed
<cache>/install/<key>/                         an install's work folder (target disk, step outputs, console.log), removed when it ends
<cache>/install/<key>.console.log              a failed install's console, kept for the next look
<cache>/install/<key>.failed.raw               the newest failed install's disk, until the recipe's next install ends, another fails or any succeeds
<cache>/install/<key>.failed.root.raw          its root disk when the recipe has one (the installer's own logs), likewise
                                               (sentineld logs where each failure's files are, and each removal)
<cache>/packages/<distribution>/{apk,apt}     package downloads shared by the distribution's workspaces
```

- `--cache DIR` defaults to `~/Library/Caches/Sentinel` on macOS, or `$XDG_CACHE_HOME/sentinel`
  (`~/.cache/sentinel`) on Linux; never `/tmp`. It's locked with `owner.lock` like the state
  directory, may be wiped at any time, and must be on the same volume as the state for APFS clones.
- Each workspace records its `base` in the catalog, cleared when its spec changes. A base disk is
  kept while some workspace references it (and the newest one until the next install), and pruned
  at startup, after a delete and after an install.

## 19. Stop and exit

- **Guest power off:** libkrun ends the process with status 0: `on_exit(clean)`.
- **Guest reboot:** status 3 (PSCI `SYSTEM_RESET`): sentineld starts the VM again with the same
  disks. A workspace that reboots 5 times before reaching `running` fails instead; an install VM
  restarts until its installer powers off.
- **`power_off`:** SIGTERM, which the process turns into an immediate exit (a hard stop); SIGKILL
  after 5 s bounds a stuck one. Graceful shutdown is the agent's `shutdown` (§12, `shutdown_s`).
- **Drain:** both modes (Stage 1 §3) power off every VM concurrently.

## 20. Signing

- `sentineld` carries `com.apple.security.hypervisor` (for `sentineld vm`); nothing else needs an
  entitlement (user-mode networking needs none).
- Local development signs ad-hoc (`Makefile`).
- Release signing and notarization belong to the build/release stage.

## 21. The VM's libraries

`packages/sentinel-workspace` builds the libraries `sentineld vm` links from pinned sources
(`macos/scripts/sources.json`) and pinned tools (`toolchain.lock.json`: Meson, Ninja, a private
Rust toolchain, Homebrew bottles for libslirp and glib); nothing is installed on the host. Patches
(`macos/patches/`) apply with no fuzz:

| Source | Patch |
|---|---|
| libkrun (`libkrun-pci-1-upstream`) | upstream's virtio-pci transport and device-manager API, backported |
| libkrun (`libkrun-pci-2-hvf`) | virtio-pci on Hypervisor.framework: an ECAM host bridge on bus 0, a 32-bit BAR window, level-triggered INTx as GIC SPIs, described in the device tree |
| libkrun (`libkrun-ramfb`) | a boot framebuffer (QEMU's ramfb model) the firmware programs through a register page, shown on the first scanout |
| libkrun (`libkrun-screen-handoff`) | the boot framebuffer stops being shown once the guest draws on the virtio GPU |
| libkrun (`libkrun-sysreg`) | PMU and debug registers read as zero, PMUv3 advertised (Windows requires one), unimplemented registers raise Undefined Instruction in the guest |
| libkrun (`libkrun-vcpu-psci`) | PSCI in the VMM (`CPU_ON`, `AFFINITY_INFO`, `CPU_SUSPEND`, `FEATURES`, …) so any vCPU starts another; `SYSTEM_RESET` ends the VM as a reboot (§19) |
| libkrun (`libkrun-viostor`) | virtio-pci keeps the MSI-X configuration vector a driver writes (Windows' viostor reads it back) while delivering INTx |
| libkrun (`libkrun-pci-3-shm`) | virtio-pci shared memory regions: the virtio-gpu window as a 64-bit prefetchable BAR with its `VIRTIO_PCI_CAP_SHARED_MEMORY_CFG` capability, a power-of-two size at a multiple of it, the host bridge's 64-bit window in the device tree (the firmware assigns the BAR there, ACPI describes it to Windows); display devices' registers in BAR 2 |
| libkrun (`libkrun-virtio-gpu-3d`) | the `3d` GPU: virgl and Neptune contexts beside Venus (rutabaga built with Neptune as capset 7), the renderer polled while fences are pending, and a device reset that frees what the guest made (scanouts, mapped blobs, resources, contexts, fences) and keeps the renderer, which starts once per process (Windows resets the GPU on driver restart and power-down) |
| rutabaga_gfx (`rutabaga_gfx-01-neptune`) | capset 7 is virglrenderer's Neptune, not Magma; virglrenderer's Neptune and native-texture flags |
| libkrun (`libkrun-virtio-gpu-2d`) | a virtio GPU with no shared memory window is a 2D one: EDID and nothing more offered, no capsets, rutabaga's 2D component (fences complete at once), every resource in guest pages; it goes on virtio-pci (Windows' viogpudo) and completes a driver's reset (Windows resets it on a driver restart and at power-down) |
| libkrun (`libkrun-virtio-indirect`) | indirect descriptor tables, accepted even when not offered (Windows' viosock uses them) |
| libkrun (`libkrun-net-reset`, `libkrun-vsock-reset`) | net and vsock complete a driver's reset (Windows' netkvm and viosock reset them on every power-down and wait: without it, every shutdown or reboot hangs 5 minutes and ends in `DRIVER_POWER_STATE_FAILURE`). Net parks its worker until a driver activates it again; vsock's muxer threads see empty queues until then |
| libkrun (`libkrun-virtio-balloon-reset`) | the balloon completes a driver's reset (Linux resets every virtio device at power-off and waits: without it, `virtio_balloon`'s shutdown spins and every Linux power-off hangs after "Requesting system poweroff" until sentineld kills the VM). Reset leaves the queues and guest memory alone until a driver activates the device again |
| libkrun (`libkrun-macos-display`) | the macOS display path: 2D resources from guest pages, shared-memory blobs, blob scanouts, the 4 KiB guest granule, the guest cursor to the display; virtio-snd; guest RAM as owned 2 MiB chunks the balloon's free-page reporting frees; freed blobs unmapped |
| virglrenderer | upstream MRs !1688 (fences without eventfd) and !1685 (host memory import on Metal); dma_buf emulated for the guest's Venus (linear shareable images, host memory import) |
| edk2 (`edk2-sentinel-platform`) | the `ArmVirtSentinel` platform: ACPI tables generated from the device tree (DynamicTablesPkg) beside the device tree itself, a RAM-emulated variable store (libkrun has no flash), and a Graphics Output Protocol on the ramfb register page the device tree names |
| Mesa (KosmicKrisp, a `main` snapshot) | geometry shaders, transform feedback, fill modes and custom border colors (the guest's Zink needs them for OpenGL 4.6 / ES 3.2), a GPU-only memory type for tiled images, linear color attachments, the shader disk cache, a queue barrier lifetime fix, non-solid fill modes and geometry shader streams (DXVK requires both) |

Its `build/` holds `lib/` (libkrun, virglrenderer, KosmicKrisp and its ICD, the Vulkan loader,
libslirp and glib), `include/` and `share/firmware/`; sentineld's `make` builds it and copies `lib/`
and `share/firmware/` into its own `build/`, linking `sentineld` against `lib/` (`@executable_path/../lib`).

## 22. Computer use

The `computer` op runs one action batch (`move`, `click`, `double_click`, `drag`, `scroll`,
`keypress`, `type`, `wait`; at most 16, checked against the viewport the client last saw) on a
worker thread, against the VM process's display service: `display.sock`, the socket the guest also
reaches as vsock port 1101. It speaks NDJSON: one connection is one action batch,
`{"action": acquire|status|input|release|snapshot}` answered in order with `{"ok": ...}`.
`acquire` takes the lease: while a batch holds it, viewer input is dropped. `input` carries a §6
`input` payload (base64); `snapshot` returns the current frame as a PNG or JPEG data URL of at most
~1.4 MB. Closing the connection releases its keys, buttons and lease. One batch runs per workspace
at a time; `computer_cancel` stops it after its current action.

Key presses use the chords the agent returned with the keyboard layouts (§8), US codes without
them. Typing text is the only part in the guest: through the desktop's `type` script, or, where the
distribution's desktop data names `paste` keys, through the `clipboard` script and those keys. On
Windows the agent's `type` sends the text as Unicode key events (`KEYEVENTF_UNICODE`, whatever the
layout; line breaks and tabs as Return and Tab) from its helper in the user's session (§9), in slices
of 32 characters.

Input devices in `input` payloads: 0 wheel, 1 pointer (absolute 0–65535, buttons left to extra),
2 keyboard (Linux key codes 1–255). sentineld-media releases a viewer's pressed keys and buttons
when it disconnects.

## 23. Remote Macs

A remote Mac runs the same sentineld, installed and updated by the backend over pinned SSH
(`apps/backend/sentinel/app/services/workspaces/machines/remote/worker.py`). There is no relay process and no
login service: the SSH server opens sentineld's Unix socket itself (`direct-streamlocal`), for the
backend's update-time requests and for the desktop app's forwarded control connection.

**Layout** (`~/.sentinel/sentineld`):

```
manifest.json    the active release: executable, files, links, version, updateProtocol 1 (and
                 workspace, read only by releases before `sentineld vm`)
update.json      the update journal (remote_update.py)
update.lock      the update lease
current ->       releases/<version>-<id>, the active release
releases/<version>-<id>/sentineld/      bin/, lib/, tools/{mke2fs,resize2fs,wimlib-imagex,mkntfs,hive}, share/
state/  cache/   sentineld's --state and --cache (§18)
service.log
```

- **Release:** the build tree the desktop app runs, uploaded file by file, each checked
  with `shasum` before it's renamed into place. Library aliases in the guest Mesa trees are
  same-directory symlinks, recorded in the manifest. The version hashes every name, byte and alias.
  The three Mach-O binaries are verified with `codesign --verify --strict` before activation.
- **Start:** when nothing listens, the backend starts `sentineld --state --cache` with
  `nohup`; it's ready once the socket answers `status{api}`. sentineld's `owner.lock` rejects a
  second instance.
- **Update coordinator:** the new release's `sentineld --update-session ROOT`, one JSON request per
  line on stdin. It holds `update.lock` (one updater; released when SSH drops, or after 60 s
  without a request) and serves `inspect` (store owner free?, manifest, journal), `journal`,
  `activate` and `heartbeat`. `activate` takes `state/owner.lock`, writes the manifest, switches
  `current` and starts the release's sentineld with the held lock (`--owner-fd`), so nothing else
  can start in between. Store migrations: none yet (`migration_status` reports none).
- **Maintenance** (stopping the old release): the backend checks the running workspaces against the
  user's approval, `stop`s each, then sends `shutdown` and waits for EOF.

## 24. Release components

The desktop app bundles no runtime. Only the app is signed and notarized; it downloads the runtime
as release components and verifies each against the release index first.

- **sentineld**, the one component: `make release` packs `bin/sentineld`, `bin/sentineld-media`, `lib/`, `tools/mke2fs`, `tools/resize2fs`, `tools/wimlib-imagex`, `tools/mkntfs`, `tools/hive` and
  `share/` and `distributions/` into `build/release/sentineld-<sha256[:16]>.tar.gz` with `sentineld.json`
  (`{name, file, sha256}`).
- **Distributions** are not components: the shipped ones ride in `distributions/`, and sentineld installs each
  when a workspace first uses it (§7).
- **One release train:** the candidate CI calls `sentineld.yml`, which reuses its last output when
  its sources (and the VM libraries') are unchanged (tarballs cached by a source hash). The payload's
  `latest-<channel>.json` lists them under `components`; promotion reuses the tarballs byte for
  byte and publishing attaches them to the versioned release.
- **On the Mac** (`apps/desktop/sentinel/src/main/app/components.ts`): each version is extracted into
  `<app state>/components/<name>/<sha256>` and checked; `installed.json` names the active ones. Only
  components whose sha256 changed are downloaded. sentineld starts from the active ones when the app
  starts it: an update never swaps files under running VMs. Remote Macs (§23) receive the same trees.
- **Development:** `make dev` runs `packages/*/build` directly, nothing downloaded.

## 25. End-to-end tests

`make e2e [DISTROS=…] [DESKTOPS=…] [JOBS=…]` (`tests/e2e/e2e.py`, standard-library Python) proves every
distribution × desktop, plus a desktop-less workspace per distribution, on a private sentineld
(`--lifeline`, so it and its VMs go with the harness however it ends): state in `$TMPDIR`, cache in
`build/e2e/cache` (package downloads kept between runs; bases and layers are pruned at start, so every
run installs them), and `build/e2e/sources`, a mirror of the pinned install sources (sentineld removes a
source once installed; the harness clones each verified download first and seeds the next run's cache
with it, `--seed DIR` adds other folders). At the end it keeps only those two: bases, layers, failed
install disks and the cache's source clones are deleted (sentineld prunes unused bases at start anyway),
and the newest 5 runs' evidence. The share is cloned for the run. `JOBS` defaults to
what free memory (≈2.5 GiB a workspace, by `memory_pressure`) and disk allow; below 25 GiB of free disk
(`--min-disk`) no VM is built: those cases are reported skipped, not failed. Each case is created,
started and checked:

| Check | How |
|---|---|
| `start` | install, layer, boot and total times, from the `progress` events and the workspace disk appearing |
| `exec`, `session` | `exec` of a command; the desktop's processes (`/proc/*/comm`, `tasklist` on Windows), also after the restart and in the second workspace |
| `shell`, `shell_stream`, `terminal` | where the distribution has `shell`: a `bash -c` command with arguments runs Python, makes a Git commit and finds tmux, rg, jq and gh; 3 MiB streamed into a file through `process` read back with the same SHA-256 (uploads); a tmux server started with `nohup … &` outlives that command, and `tmux -C attach` through `process` (the app's terminal view) types a command into the pane, whose output and terminal (`tty`) come back as `%output` |
| `network` | the guest resolves `example.com` and downloads it over HTTPS (`curl` or `wget`; `Resolve-DnsName` and `Invoke-WebRequest` on Windows) |
| `video`, `screenshot` | `tests/e2e/viewer.cpp` (`build/tests/e2e-viewer`) joins as the app's viewer does (§6: `display` ticket, pinned certificate, hello): a 1920×1200 key frame arrives; a `computer` snapshot is not blank |
| `gpu` (Windows) | `tests/e2e/d3d11_probe.c` (`build/tests/e2e-d3d11.exe`, built with the pinned zig, put in the run's share) renders a Direct3D 11 triangle offscreen on the first adapter, which must be the virtio GPU (vendor 1af4), and reads it back; `Win32_VideoController` shows the VirtIO adapter OK with a display mode (it drives the desktop). The probe runs where applications do, in the signed-in user's session (a scheduled task for INTERACTIVE): the agent's session 0 has no display, and viogpu3d's Direct3D driver finds its adapter through the session's display devices |
| `vulkan` (Windows) | the same probe with `--vulkan`, in a process of its own: Vulkan through the loader (`vulkan-1.dll`), `vkCreateInstance` and the physical devices, one of which must be Venus; a call that does not return in 30 s fails the check |
| `d3d_vulkan` (Windows) | the probe with `--d3d-vulkan`: the triangle, then, in the same process with its Direct3D device alive, the Vulkan listing (§16: both drivers' transports in one process) |
| `typing`, `clipboard`, `wallpaper` (Windows) | `computer` batches open Notepad (Win+R, `notepad`, Return: keys from the host's keyboard device), type text with characters no US key types, a line break and one beyond the BMP, and copy it (Escape for Notepad's first-run tip, which takes the focus; Control+A, Control+C): the `clipboard` op reads back exactly what was typed. A fresh install's first sign-in keeps Windows busy (Notepad, a Store app, may take a minute to draw its window, and text typed before then is lost), so that round is tried every 5 s for up to 2 minutes. Text the `clipboard` op writes reads back and pastes into Notepad (Control+V). The `wallpaper` op sets `satin`: the signed-in user's `Control Panel\Desktop` `Wallpaper` is its copy, and a screenshot of the desktop (Win+D) is kept |
| `cursor`, `cursor_moving` | the pointer glides through the centre, the four corners and edge midpoints with viewer `input` packets: a hardware cursor must arrive as a non-empty `cursor` packet and never turn hidden or transparent while moving; with no cursor packet, the cursor must be drawn in the frame at each point (snapshot diff near it) |
| `pointer`, `clicks` (Linux) | `tests/e2e/probe_x11.py`, run by `process` as the desktop user, maps a full-screen X window (Xorg, or the compositor's Xwayland) that reports motion and button events: at each of the points, the pointer and a left click land within 2 px of where they were sent |
| `stop`, `restart`, `fs_clean` | `stop` within 10 s (Windows, whose own shutdown takes 3–15 s: 60 s), and a power off forced after the distribution's `shutdown_s` fails it; the restart reaches `running`; the root disk's superblock, read on the host after the stop, is clean (cleanly unmounted, no errors, no journal to replay, no orphans: what `image::growRoot` needs), and the restarted guest's `dmesg` shows no ext4 recovery or errors (ext4's "orphan cleanup on readonly fs" is no finding: it prints it at every read-only mount of a file system with an orphan file, e2fsprogs 1.47's default, as Ubuntu's `ro` root is mounted) |
| `reuse` | a second workspace of the same spec starts from the cached layer (no install) within 120 s |

It prints a table, writes `build/e2e/runs/<time>/report.json` with per-failure evidence (console and
VM log tails, screenshots, cursor images, viewer and probe event logs) and exits non-zero on any
failure. Weak spots: frames in a snapshot are the newest presented one, so a software cursor is
checked after the pointer settles, not mid-motion; Windows has no guest probe (no `pointer` or
`clicks`).
