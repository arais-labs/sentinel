# Confined core24 graphics provider

Packaging only: supply the existing verified ARM64 glibc Mesa archive to
`scripts/packaging/graphics/package-gpu-provider.py`. It does not compile Mesa,
install packages, start a VM, connect Snap interfaces or change confinement.
Reuse the persistent Ubuntu graphics builder; do not introduce another builder.

```sh
python3 package-gpu-provider.py \
  --mesa /shared/mesa-linux-arm64-glibc.tar.xz \
  --mesa-sha256 CHECKSUM_FROM_VERIFIED_GRAPHICS_MANIFEST \
  --mesa-source /shared/mesa.tar.xz \
  --mesa-source-sha256 CHECKSUM_FROM_SOURCES_LOCK_MESA_GUEST \
  --assets /shared/gpu-2404 \
  --cache /var/cache/sentinel-build/gpu-2404-provider \
  --output /shared/provider-output
```

Builder tools: Python3.12+, dpkg-deb, curl, readelf/binutils, snap and its
squashfs-tools packer. No APT operation is performed during provider packaging.
`--assets` defaults to this directory when invoked from the repository.

Normal graphics orchestration invokes `build-guest.py DEST gpu-2404 HELPER`
after the glibc Mesa target. It verifies that archive against its current build
key, reuses the same persistent Ubuntu builder and source/dependency caches,
and installs missing packaging tools once in that builder. Provider-only edits
do not invalidate Mesa compilation. A verified host artifact-cache hit does not
boot the VM. The published runtime pair is `gpu-2404.snap` and `gpu-2404.json`;
the latter retains the content identity and original version/provenance.
Prebuilt CI handoff verifies the same pair after the glibc Mesa handoff.

## Inputs and cache

`dependencies.lock.json` preserves the exact Noble ARM64 closure from the
live-tested provider. It was resolved using Ubuntu's signed main/universe,
updates and security APT indexes. Every URL, size and SHA256 is fixed; ordinary
builds never resolve packages or substitute missing historical versions. A
dependency/security refresh is an explicit reviewed lock update, not deletion
of a local cache. Downloaded DEBs are hash-checked and reused.

The artifact identity includes both verified Mesa hashes, this lock, metadata,
wrapper and packager bytes. A valid artifact cache hit verifies its manifest
and snap digest before any dependency download or packaging. Keep this identity
separate from Mesa/host compilation keys: provider edits must not recompile Mesa.
New output is packed privately and atomically published with its manifest as
the final commit marker. A crash between the two publications produces a cache
miss, not an accepted mismatched pair.

The packager never copies the builder's live libraries. It extracts locked
DEBs, replaces distro Mesa vendors with unchanged Sentinel artifacts, preserves
the public gpu-2404 API/support library roots, then checks dependency closure,
library symlinks, AArch64 ELF64/little-endian architecture and GLIBC<=2.39.
It does not bundle libc/the loader. Mesa's complete pinned source archive and
dependency package notices are retained; pruning notices requires a separate
license review. The manifest records artifact/dependency/Mesa provenance. It is
not a Store assertion, and byte-identical reproducibility across packer versions
or differing timestamps is not claimed.

## Runtime boundary

The content-only snap has strict confinement and exports `gpu-2404`. The wrapper
selects these libraries using the standard consumer-provider mechanism; no
sandbox or GL-version overrides. It supplies GL/EGL/GBM, **not** a Vulkan ICD or
video codec driver. Loader libraries alone do not establish those capabilities.

Runtime integration must verify the bundled artifact, install/update the local
provider, connect Chromium's `gpu-2404` plug to `sentinel-gpu-2404:gpu-2404`, and
verify the connection before browser launch. Local `snap install --dangerous`
means an unasserted trusted artifact, not disabled strict confinement. A signed
distribution policy remains required for release. Handle Ubuntu Snap Chromium
whether used as desktop browser or Firefox's automation browser, including
headless workspaces; do not change Chrome-as-selected automation behavior.

Qualification must check actual browser GPU library provenance, visible pixels,
input, UID/seccomp/AppArmor confinement and successful cold reboot. The original
exact-tracker provider passed those live checks; the maintained packaging entry
point still needs its own package-and-run qualification after integration.

References: [gpu-2404 contract](https://discourse.ubuntu.com/t/the-gpu-2404-snap-interface/44251),
[consumer library roots](https://github.com/canonical/gpu-snap/blob/main/lists/mesa-2404.arm64.list).
