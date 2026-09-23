# Native workspace qualification

Run from `apps/desktop/sentinel` on a supported Apple silicon Mac after preparing
the production runtime (`npm run desktop:prepare`) and compiling the TypeScript
test adapter (`npx tsc -p tsconfig.json --outDir .test-dist`). XFCE also requires
the backend virtualenv for production appearance defaults.

```sh
python3 tests/qualification/run.py --list
python3 tests/qualification/run.py --distribution ubuntu --desktop xfce --browser chromium
python3 tests/qualification/run.py
```

The default matrix covers 32 supported distribution/desktop/browser selections:
Ubuntu and Debian with Chromium, Firefox or Chrome, and Alpine with Chromium or
Firefox, each with XFCE, LXQt, GNOME and KDE Plasma. Repeat filters to select
several values; `--desktop kde` aliases `plasma`. Explicit Alpine/Chrome selections
are reported as unsupported and prevent overall success; they never launch a VM.
Each selected row invokes the existing `host-desktop.mjs` integration
adapter in its own disposable VM. Execution is serial by default. Explicit
`--jobs 2` or `--jobs 3` permits bounded concurrency; each row requests four CPUs,
4 GiB RAM and a 16 GiB disk. `--timeout` sets the per-row deadline in seconds
(default 1800). Package installation requires network access.

For Ubuntu download reuse, optionally set `SENTINEL_TEST_SNAP_CACHE` to a
directory containing `.snap` downloads and `manifest.json` with
`{"schema_version":1,"blobs":[{"filename":"example.snap","size":123,"sha3_384":"<96 lowercase hex characters>"}]}`.
Use the size and SHA3-384 from the Snap Store's metadata. The fixture copies and
verifies every blob before placing it on the guest filesystem in snapd's cache;
invalid bytes fail the row. The normal production `snap install` still resolves
revisions and verifies signed assertions. This is download reuse, not an offline
installation or a signature bypass. Cache evidence is recorded in the row log.

The runner creates a unique directory under `build/qualification/` (or
`--output-root`) containing `report.json` and per-distribution/desktop console
logs, result JSON, and a desktop screenshot when that gate is reached. Evidence
is separated by distribution, desktop, and selected browser. Reports
are updated after each row and distinguish pass, fail, and unrun. A failure or
unrun selected row prevents success. Interruptions stop running adapters, leave
queued rows unrun, preserve evidence, and return 130. The adapter receives up to
60 seconds to clean up after termination; forced termination is recorded and
requires checking runtime cleanup.
Each row records a SHA-256 identity of its adapter, guest fixtures, compiled
workspace adapter modules, and browser workers. Editing these inputs during a
run fails qualification instead of silently mixing test revisions.
Each row also records SHA-256 hashes of the actual runtime helper, kernel,
manifests, graphics files and selected distribution's OCI image layout. These
are checked against a pre-run baseline before launch and again after completion;
changed or missing artifacts fail qualification. Unchanged files share a hash
cache across rows, avoiding repeated reads of large image layers. The init image
is identified by its pinned reference. This is reproducibility evidence, not
tamper resistance: online package downloads, host libraries and changes restored
between observations are outside this identity check.

Coverage includes desktop checks and the production selected automation worker's
headless/native-session launch, CDP, regular-user ownership, reuse, stale-process
refusal and confirmed shutdown. It installs browsers through production setup
steps. Chrome selection must launch Chrome; Chromium and Firefox selections must
launch Chromium for automation. Native automation browsers must pass presented WebGL pixel phases,
real click/keyboard input, live packaged Mesa hashes and virtio-gpu device
ownership, plus GPU-process seccomp and (for Snap) enforcing AppArmor policy.
Their screenshots, logs and results are retained under `browser-gpu/<app>/`, including
on failure. These targeted confinement checks are not a sandbox security audit.
Firefox selection additionally runs the native Firefox pixel/input/confinement
fixture. These are mandatory gates, not a claim that all rows have passed;
inspect the actual run report for qualification results.
The selected desktop browser separately requires a visible application entry,
an icon resolved by the desktop theme, the Sentinel launcher, and effective
HTTP/HTTPS/HTML default associations under the ordinary session user. This
metadata gate does not substitute for the browser execution and pixel checks.
Every row also stops and boots the same VM disk again, verifies a new kernel
boot ID, then repeats the headless browser lifecycle without reinstalling
packages. Snap Chromium must have its actual AppArmor enforce profile and
seccomp filtering on both boots; a successful service status is not a substitute.
The adapter's guest failure diagnostics are captured in `console.log`; the
exact failed application sample is retained as `oracle-failure.png` in the row's
evidence directory. Standalone adapter runs retain their default build path.
The runner does not build artifacts or modify existing workspaces.

The clipboard oracle performs one real application Copy gesture and one
production read. For X11 applications on Wayland, an ordinary-user `wl-paste
--watch` observer acknowledges destination selection propagation; an X-server
sync alone cannot acknowledge the compositor's bridge. A unique setup marker is
observed by both Wayland and GTK before Copy, and the copied payload is unique
per run. Competing selections fail rather than triggering another Copy. Bounded
event waits replace sleeps or read polling, and selection/cleanup diagnostics
are retained on failure. The reverse transfer uses GTK's owner-change event.
Application frame-clock timing is callback timing, not physical display FPS.
The adapter requires the contract's target refresh at 1280×800, after resizing
to 1920×1200, and after returning to 1280×800. The same GPU proxy PID/start-time
must survive those session changes. Every login also queries the compositor's
real mode list and requires all seven contract resolutions at the target refresh.
For Plasma, every login waits for Klipper's actual session D-Bus ownership
announcement (bounded, with no activation or fixed delay), then verifies the bundled native package
checksum, compares its library bytes with the installed file, and checks that
the ordinary-user `plasmashell` maps that exact device/inode. The result records
the package version, both hashes and actual process IDs. Merely having a patched
package installed is insufficient if Plasma loaded a different library.
This verifies advertised modes and selected timing, not 120 physically presented
frames per second; measured presentation performance remains a separate gate.
Older reports produced by the initial-size-only adapter do not qualify this
stronger resize contract; compare each report's source identity.

Set `SENTINEL_TEST_RUNTIME` to an isolated packaged runtime directory to qualify
candidate binaries and image catalogs without replacing the dev runtime. The
directory must contain the normal kernel, manifest, helper, graphics and
workspace-images assets; the default is the production build output above.

Fast runner checks do not start VMs:

```sh
python3 -m unittest discover -s tests/qualification -p 'test_*.py'
```
