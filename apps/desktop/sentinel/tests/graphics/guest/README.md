# Guest mapped-buffer checks

`sh tests/graphics/guest/run.sh` runs the ordered-merge and upload-mask unit tests.
The mask checks cover exact runs, partial-byte tails and guard-page bounds. Set
`MESA_SOURCE_DIR` to the pinned Mesa source tree to include the exact inline-write
wire-format tests. `CFLAGS='-fsanitize=address,undefined'` enables sanitizer checks.

Meson (1.1 or newer) and Ninja own compilation and test registration. The wrapper
keeps objects, executables and logs in `build/graphics-guest-tests`; repeat runs
reuse unchanged objects. Set `SENTINEL_GUEST_TEST_BUILD_DIR` for a separate build,
including when changing `CC` (Meson fixes the compiler at initial setup). The
wrapper updates Mesa, DRM, EGL and `CFLAGS`/`LDFLAGS` options on each invocation.
Use `MESON=/path/to/meson` or put private tooling on `PATH`, for example from the
desktop app directory:

```sh
PATH="$PWD/build/graphics-sources/venv/bin:$PATH" sh tests/graphics/guest/run.sh
```

After setup, `meson test -C build/graphics-guest-tests --print-errorlogs` builds
incrementally and runs the configured tests directly. Preserve the runtime
environment (including `LD_LIBRARY_PATH` and optional storage limits) when running
the live EGL gate. Its upload telemetry remains in `upload-only.log` in the build
directory. A requested EGL gate returning 77 is reported as failure, not a Meson
skip; platform-specific tests retain their Linux/ARM64 guards.
Explicitly requesting DRM or EGL on a non-Linux host fails configuration.

On ARM64 the same command runs the actual mapped-buffer tracker/merge tests,
allocation-budget/failure guards, exact capture tails at inaccessible page
boundaries, and concurrent writes to bytes unrelated to consumption. Linux adds
the larger tracker lifecycle/pipe-write test. No userfaultfd permission or
simulated alternative tracker is required. The implementation uses ARM64 machine
loads into private immutable captures and byte-only publication; it does not
promise atomic whole-buffer snapshots or safe same-byte CPU/GPU races. GL-ordered
consumed bytes must remain stable. ASan/UBSan help validate allocation/masks;
uninstrumented assembly is not proved race-free by a sanitizer pass.

Snapshot reservation includes one scratch page plus maximum requested immutable
payload and descriptors; clean scans allocate scratch only. Unmap discards CPU
delta bookkeeping directly into existing baseline, without an uncharged snapshot.
The allocation test forces failures and checks the exact maximum requested heap
payload, not allocator overhead or RSS. Captures remain immutable after accept.

Set `SENTINEL_TEST_EGL` to the packaged absolute `libEGL.so.1` path and select its
library directory with `LD_LIBRARY_PATH` to run the genuine OpenGL API gate.
The test rejects version/extension overrides and software renderers. Missing
buffer-storage support returns 77, not a passing result. Set
`SENTINEL_TEST_STORAGE_LIMITS=1` for Sentinel's 16-mapping admission/exhaustion and
recovery test; this is a product resource-budget check, not a general GL limit.
The harness runs that capacity test in a separate process, after the full API
suite. A fresh screen avoids counting cached shader bindings from earlier tests
against the fixed budget. The direct GL fixture runs only the capacity test when
this variable is set; its sixteen-slot, exhaustion and recovery checks are unchanged.
The API gate also queues 512 coherent-buffer updates without intermediate fence
waits, checking that ordinary submissions reclaim the bounded readback journal
and preserve every update.

The API suite runs again with `SENTINEL_TEST_NATIVE_FENCE=1`. Its fence helper
exports an EGL native sync-file, destroys the EGL sync, and waits with `poll()`
instead of a GL/EGL client wait. Mapped reads must already contain GPU results
when that external FD signals. Missing native-fence support is a failure.

Before the full API suite, the same gate runs the upload-only fixture in a
separate process with `SENTINEL_STORAGE_REPORT=1`. It requires a successful fixture
exit, exact GPU-consumed data and guards, and driver telemetry reporting both
`read_captures=0` and `capture_bytes=0`. Missing telemetry fails rather than silently
skipping the performance contract. The full suite always runs afterward, even if
the caller has set `SENTINEL_TEST_UPLOAD_ONLY`. This report check requires Python 3;
its CPU-only parser tests are `python3 -m unittest discover -s tests/graphics/guest
-p 'test_storage_report.py'`.

`python3 tests/graphics/guest/check-artifacts.py` validates packaged archive
checksums and boots the packaged kernel in disposable headless Alpine and Ubuntu
workspaces. It loads the packaged display/input modules, verifies their device
nodes, exercises each packaged mapped-memory probe and deletes those
workspaces. It does not start a graphical session or a user workspace.

These are focused regression gates, not an OpenGL conformance certification.

## Mesa shader-cache lifecycle

The guest Mesa patch series includes `Cache.LazyQueueLifecycle` in upstream
`src/util/tests/cache_test.cpp`. Configure the patched source with
`-Dbuild-tests=true`, build the native `src/util/util_tests` target with Ninja,
then run `./src/util/util_tests --gtest_filter='Cache.*'` from that build directory.
This checks that cache construction, reads and idle waits do not start workers,
and that concurrent first writes initialize the queue and persist their data.
The rest of Mesa's cache suite covers the supported storage backends.
Run on Linux for the complete suite; the dynamic Fossilize list test is not
available on macOS. Passing these unit tests does not replace live browser
sandbox, GPU, pixel and warm-cache qualification.

The queue-priority patch adds native `queue-priority` and
`queue-priority-scheduler` Meson tests on Linux ARM64/x86-64. Run
`meson test -C /path/to/mesa-build queue-priority queue-priority-scheduler`.
Each forks a bounded child, installs a scheduling seccomp filter before creating
the real Mesa queue, and verifies that the first job runs at nice 19/SCHED_BATCH
while its creator stays unchanged. The scheduler-only case isolates the original
cross-thread scheduling crash; the stricter case also requires pid-zero nice
updates. Run as an ordinary user with normal initial scheduling priority.
These tests exercise actual Mesa objects, not a copied queue implementation.

On a running disposable graphics workspace, set `SENTINEL_TEST_DRM_PLANES=1`
to assert that the actual virtio GPU exposes a primary and cursor plane for each
scanout, without unsupported extra plane types. This requires libdrm development headers and
pkg-config. It checks the kernel capability contract without changing display
state; visible cursor movement still needs the desktop pixel/input integration
test on both X11 and Wayland.

## Driver-internal consumers

`python3 tests/graphics/guest/run-gallium.py /path/to/mesa-build` compiles and links
the compute, direct/indirect draw and query-result-buffer fixtures against that
normal Meson build's archives. It never replaces driver objects or produces an
alternate graphics library. Use `--compile-only` to check fixtures without linking;
add `--device /dev/dri/card0` to execute the GPU tests explicitly.

The draw fixture binds the vertex buffer once and updates the original coherent
pointer across repeated draws, including zero/nonzero indirect draw counts. The
compute fixture checks SSBO output publication. The query fixture uses the
production singleton query-buffer bind and checks specified 64-to-32-bit
saturation, original mapped pointers, and untouched guard bytes.
