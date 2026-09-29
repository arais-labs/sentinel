# Host Mesa qualification

`python3 run.py --help` lists the pinned source, build and packaged library paths.
The Python unittest entry point is unchanged. Python validates the qualification
environment and normalizes bundled epoxy install names; Meson/Ninja own all
compilation, linking and test execution. Tests use the existing private graphics
Meson/Ninja toolchain, with PATH tools as a fallback.

The build directory is persistent (`build/graphics-tests/host-mesa` through the
unittest entry point). Repeated runs reuse objects; source/header/archive changes
invalidate their dependents. Use a separate build directory when changing the C
compiler. GPU tests run serially, with fixed driver capabilities and explicit ICD,
without ambient DYLD library overrides. Buffer range tests retain ASan and UBSan.

The queue-finish regression comes from the patched Mesa source and links the
actual Mesa build's util and C11 archives. Its feature defines are read from that
build's compilation database so queue structure layouts match. No second copy of
the queue implementation is compiled by this project.

After configuration, CPU-only checks can be repeated with:

```sh
meson test -C <build-dir> buffer-ranges queue-finish --print-errorlogs
```

The normal Python entry point always runs every GPU assertion and all requested
buffer-storage repetitions; CPU-only results are not hardware qualification.
