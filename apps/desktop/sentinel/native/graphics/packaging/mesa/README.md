# Native Alpine Mesa runtime

`build-guest.sh` compiles the existing Mesa26.1.6 virtual-GPU driver with `/usr`
prefix, non-GLVND public ABI and real GLES1 support. `build.py` packages that
compiler staging; it does not compile applications or add source patches.

The seven signed APKs retain native ownership: mesa, mesa-egl, mesa-gl, mesa-gles,
mesa-gbm, mesa-dri-gallium and mesa-dev. Their exact-version dependencies prevent a mixed
Mesa family. There are no fake provides/replaces and no force-overwrite. This
virgl-only build provides real development headers/pkg-config files and linker
symlinks, but not other hardware drivers, VA/OpenCL or
Vulkan drivers; incompatible installed sibling packages require an explicit
solution, not silent removal. Public GL/EGL/GLES1/GLES2/GBM use `/usr/lib` normally,
without global libc search overrides. Private Sentinel transport helpers remain
under `/opt/sentinel/graphics`.

Output: `packages/mesa/manifest.json`, seven APKs and `keys/sentinel-mesa.rsa.pub`.
The manifest contains checked package identities/dependencies, digests and public
key digest; `apk verify` runs before it is published. Source/recipe/driver hashes,
existing patches and original upstream license are recorded under
`share/sources/mesa-26.1.6`. Build inputs use the existing verified source lock.
Desktop qualification is tracked separately against artifact digests, not encoded
as a permanently false runtime-manifest field.

The installer must validate the manifest, verify signatures and install the
entire family in one normal APK transaction. No musl loader file is required.
glibc Mesa and the Ubuntu Snap provider are unchanged by this path.
