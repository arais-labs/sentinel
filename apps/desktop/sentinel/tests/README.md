# Desktop and native Linux tests

Run commands from `apps/desktop/sentinel`. Choose the layer being changed;
unit checks do not qualify a desktop, and a desktop screenshot does not qualify
the driver or browser sandbox.

## Ownership and entry points

| Directory | Contract | Entry point |
| --- | --- | --- |
| `unit/` | Application adapters, packaging, cache keys and installers | `npm test`; `npm run test:graphics-build` |
| `graphics/transport/` | Production C framing, backpressure, bounded storage and fairness | [Transport checks](graphics/transport/README.md) |
| `graphics/guest/` | Native mapped-memory ownership, GL publication and kernel plane advertisement | [Guest checks](graphics/guest/README.md) |
| `graphics/kernel/` | Actual patched kernel mode-advertisement logic | `python3 tests/graphics/kernel/mode-advertisement.py PRISTINE_LINUX_TREE` |
| `graphics/host/` | Actual Metal copy, frame lifetime and encoder behavior | `python3 -m unittest discover -s tests/graphics/host -p 'test_*.py'` |
| `graphics/host/mesa/` | Host driver capabilities, storage, shader side effects and raster output | `python3 tests/graphics/host/mesa/run.py --help` for required pinned build paths |
| `integration/` | Production runtime adapters owning disposable VMs | Invoked by qualification; focused adapters document their own prerequisites |
| `fixtures/` | Programs executed inside those disposable Linux sessions | Not standalone host tests |
| `qualification/` | Distribution × desktop × browser selection, evidence and aggregate result | [Matrix qualification](qualification/README.md) |

Native implementation ownership is documented in
[the graphics contract](../native/graphics/README.md). Keep C/Objective-C
regressions beside their corresponding native layer. Keep Linux-side probes in
`fixtures/` when they require a provisioned desktop session. The JavaScript
integration adapters exercise the production application/runtime interface;
they are not alternative graphics builders or Linux drivers.

## Fast checks before a VM run

```sh
npm run test:graphics-build
python3 -m unittest discover -s tests/qualification -p 'test_*.py'
python3 tests/qualification/run.py --list
```

Prepare the packaged runtime and compiled application adapter as described in
the matrix guide, then select the affected row:

```sh
python3 tests/qualification/run.py --distribution debian --desktop gnome --browser chromium
```

Use `SENTINEL_TEST_RUNTIME` for an isolated candidate bundle. Do not change that
bundle, fixtures, compiled adapter or browser workers during a qualification
run. Leave generated artifacts and reports under `build/`; experimental work
belongs outside the source tree. Reuse the existing persistent native builders
and their component caches instead of clearing build directories to rerun tests.

## Evidence requirements

The default matrix is the supported set declared by the runner, not a list of
historical successes. Its `report.json` distinguishes pass, fail, unsupported and
unrun selections. A focused row passing does not qualify other selections.
Preserve failing screenshots, logs and results when fixing a regression; a later
passing run is separate evidence, not a replacement for the failure record.

Browser qualification requires real presented pixels and input in addition to
CDP lifecycle, native driver/device provenance and confinement. Cold-boot checks
must run on the same installed disk without reprovisioning. Inspect the actual
report before claiming any of these gates passed. Skips are not passes, frame
callbacks are not physical display FPS, and these regressions are not Khronos
conformance tests or a complete sandbox security audit.

The kernel mode check applies the shipped patch to the pinned pristine source
and compiles the actual mode function against a mock DRM list. It covers the
finite display contract, preferred modes, deduplication, EDID and allocation
failure. It does not replace booting the resulting kernel: matrix qualification
must query the live compositor's modes and verify refresh through resize.
