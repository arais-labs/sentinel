# Klipper startup restoration

This component is under qualification. Build/provisioning integration is now
implemented, but the assembled runtime still needs the standard matrix run.
It changes neither KWin nor clipboard history preferences.

`../../patches/klipper/0001-startup-restore-only-empty.patch` marks saved-history
publication as conditional on an empty selection using KDE's existing
`application/x-kde-onlyReplaceEmpty` protocol. Explicit history selection keeps
its normal replacement behavior. Remove the downstream patch when the pinned
distro source contains the equivalent upstream fix.

## Regression contract

Apply `register-startup-test.patch`, place `startuprestoretest.cpp` in the
source's `klipper/autotests/`, and configure the distro-patched Plasma Workspace
source with its matching Qt/KF dependencies and `BUILD_TESTING=ON`. For the
isolated regression, build only `klipper` and `klipper-startuprestoretest` through
CMake/Ninja. Alpine packaging additionally builds its complete native owner as
described below. Do not build KWin. Retain source, build objects and compiler cache.

Run the test as an ordinary user under a private D-Bus session and Xvfb with
`QT_QPA_PLATFORM=xcb`. It uses temporary XDG paths, real persisted history, and
the production library. `NoEmptyClipboard=false` isolates startup publication
from the separate preserve-empty callback. After record insertion, the test
waits for database jobs and explicitly selects the record before inspecting it.

The unpatched library must fail **at the missing startup MIME marker**, not at
setup. The patched library must pass startup restoration and explicit selection.
This test does not establish Wayland race freedom: additionally exercise a real
ordinary-user Wayland application, verify the loaded library identity, accepted
copy preservation, explicit replacement, and both clipboard directions.

## Packaging boundary

`sources.lock.json` keeps distro-specific source identities separate from Mesa.
Ubuntu pins were hashed from the APT-authenticated source downloads used for the
qualification below; Debian source tar and packaging hashes match its published
DSC. The DSC and detached signature are retained as provenance, not a claim that
this component has independently verified every uploader signature.

`prepare-deb.py DISTRIBUTION --patch PATCH --work CACHE` verifies every pinned
download, uses `dpkg-source` to preserve distro patches, and applies the Sentinel
fix/test without fuzz. It publishes a content-keyed source directory only after
all steps succeed; repeated calls return that retained source. Both Ubuntu and
Debian source preparation have passed with native Linux tools. This does not
install dependencies or qualify a Debian binary on an Ubuntu builder.

`build-deb.sh SOURCE BUILD` owns the previously qualified Ubuntu target command
and accepts the pinned Debian source version. It requires distro-prepared source
with the startup patch and test applied, and matching installed build dependencies.
It delegates configuration to native debhelper (including distro compiler and
hardening flags), then builds only Klipper and its regression with CMake/Ninja
in a persistent build directory. It does not install anything or run tests as
root. The revised script and ordinary-user regression pass on Debian; the warm
run compiles nothing. The unpatched negative control fails at the expected
startup marker. Debian's native package now passes strict symbols validation and
has the same generated dependencies as the distro package. Its native package
was installed in the isolated Debian lab; the loaded Plasma library matches the
package bytes. Real ordinary-user Wayland input and bidirectional clipboard
checks pass, as do guarded restoration and explicit replacement using Debian's
advertised wlr data-control protocol. This is not proof of all queued-serial
races. Ubuntu's revised native-debhelper configuration also passes regression
and strict packaging; its warm run reports no compilation. Build
directories must be keyed by recipe/toolchain inputs so old CMake flags cannot
survive a recipe change.

`debian-arm64-symbols.patch` corrects only three architecture annotations in
Debian 13's source manifest. The APT-authenticated `libklipper6` ARM64 package
`4:6.3.6-2` (SHA256
`d90451d827936c910fbf84ca04dbde7a5b3048c2ec1b0848f46a318896b05781`)
already exports all three `HistoryItem` shared-pointer RTTI/vtable symbols and
lists them in its binary control symbols. Its complete defined dynamic symbol
name list matches both local unpatched and patched native builds. The correction
adds `arm64` alongside `amd64`; it does not relax `dh_makeshlibs -c4`, remove any
symbol, or change a minimum version. Source preparation hashes and applies this
patch only for Debian, with no fuzz.

With `BUILD_TESTING=ON`, Debian's configure graph additionally requires `xdotool`
even though only the Klipper regression target is compiled. Install it alongside
the distro build dependencies, `wayland-protocols`, and the regression tools (`xvfb`, `xauth`, and
`dbus-x11`); do not disable testing to avoid the dependency.

`package-deb.sh SOURCE BUILD OUTPUT` stages only the already-built library and
uses the source distro's debhelper manifests, KDE symbol substitution, strict
symbol comparison and dependency generation. It creates a `+sentinel1` package
with a downstream changelog in an isolated packaging directory. It requires the
distro build dependencies plus `devscripts`; it does not install the result or
compile other targets. All three arguments must be absolute paths. The script
rejects unreviewed source versions and a library with the wrong SONAME.
Ubuntu's and Debian's resulting dependency lists match their original library
packages, including the exact Qt private ABI. Alpine evidence is described below.

Ubuntu 26.04 source `4:6.6.6-0ubuntu0.1` has passed the isolated baseline/fix
comparison and a real Plasma Wayland application check. Debian's equivalent
checks are described above. Final integrated package qualification is pending.

Use distro-scoped builders, not the shared oldest-ABI glibc graphics builder:
Klipper must match each distro's Qt/KF/Plasma versions. Preserve authenticated
distro patches and package metadata. Install a distro-owned, versioned package;
do not overwrite an installed package's library with an untracked overlay.
Keep component keys independent of Mesa, the kernel and host renderer.

## Retained Debian/Ubuntu package orchestration

`scripts/packaging/graphics/klipper.py` at the app root is the host transport
adapter. It selects the distro's pinned builder image, stages these native
inputs, invokes this recipe, and verifies and records the package/source
manifest. It does not share the oldest-ABI Mesa builder or implement another
compiler recipe. Its host tests are included in `npm run test:graphics-build`;
normal packaging builds all three distro targets and includes only verified
files in the desktop-runtime archive, under `native-packages/DISTRIBUTION`.
Plasma provisioning installs the matching native package after distro dependencies.

The existing `scripts/packaging/graphics/build-guest.py` runner accepts explicit
`klipper-debian`, `klipper-ubuntu`, and `klipper-alpine` targets. They share its retained VM/container
transport and locks, but select the matching distro image, not Mesa's oldest-ABI
image. Artifacts are published below `DEST/klipper-DISTRIBUTION`; the prebuilt
handoff verifies the native manifest and corresponding source before copying.
All three handoffs have passed with actual native-built artifacts. The new
runner's build path still needs a live qualification run. These commands do not
install anything into user workspaces or mark artifacts release-qualified.

`build-package-deb.py` composes the existing native helpers. Run it as root only
inside a dedicated matching arm64 builder, with an existing ordinary test user:

```sh
python3 build-package-deb.py debian /absolute/output \
  --work /var/cache/sentinel-build/klipper \
  --builder-image 'docker.io/library/debian@sha256:REVIEWED_DIGEST' \
  --test-user sentinel --jobs 4
```

Use the matching digest from `native/workspace-images/bases.json`; the example
placeholder is deliberately invalid. `--inputs` and `--patch` support staged
inputs. Output/work paths must be absolute, and the work tree's parent directories
must permit the ordinary user to execute the regression binary. The image digest
is validated and recorded as `declared_builder_image`, a caller assertion rather
than proof of the running image. Actual distro/release/architecture and the full
installed package inventory, compiler version and native build flags are checked
or recorded independently.

Missing tools and the pinned local source's Build-Depends are installed through
APT; this is not an upgrade-all operation and does not add repositories. An exact
source-version distro `libklipper6` DEB is downloaded through authenticated APT
and retained as the dependency oracle. Its dependencies, including the Qt private
ABI, must be satisfied before compilation. After packaging, generated dependency
and complete file/type/symlink inventories must match that original owner; the
library must be a native AArch64 shared ELF. Symbols remain subject to
`dh_makeshlibs -c4`. If a moving repository no longer supplies the pinned package
or compatible dependencies, stop and review the pins or builder repositories.

Sources/downloads survive under `WORK/DISTRIBUTION`; compiler objects live under
`compilations/COMPILE_KEY/build`, while package evidence and logs live under
`components/CONTENT_KEY`. Compilation identity covers the prepared source,
explicit build helper, declared builder image, baseline package hash, installed
package inventory, native flags/environment and compiler/tool executable hashes.
The complete package identity additionally covers packaging and orchestration
recipes. Packaging-only edits retain compiler objects but recreate package
staging and rerun strict verification. Source/build/toolchain changes get a fresh
compiler directory. Both identities are recorded in provenance. A
distro-specific file lock serializes source preparation/builds. The existing
builder-wide `/var/cache/sentinel-build/ccache` is reused by default (`--ccache`
overrides it), with ccache's compiler check set to content. The patched test
runs under the requested non-root account with a private temporary HOME/runtime,
private D-Bus/Xvfb and `QT_QPA_PLATFORM=xcb`, preserving its normal profile.

The output contract is:

```text
OUTPUT/packages/libklipper6/<native-package>.deb
OUTPUT/packages/libklipper6/manifest.json
OUTPUT/share/sources/klipper-<distribution>/corresponding-source.tar.xz
```

The manifest records distro/release/architecture, owning package/version, DEB
checksum, component key, declared image, generated dependencies, source archive
path/checksum and patched-regression result. It always records `qualified:false`.
The corresponding-source archive contains every pinned original source/packaging
download, downstream patches, regression source, build/packaging scripts and pins,
the generated downstream changelog, dependency/toolchain provenance, and logs.
Only verified outputs are copied and the manifest is published last. No resulting
package is installed by this native build script; installation belongs to guest provisioning.

Host unit checks cover builder rejection, dependency/private-ABI comparison and
cache invalidation: `python3 test_build_package_deb.py`. Debian's orchestration
has passed a native lab run and a warm run with no compilation. The resulting
package was explicitly reinstalled in that lab, its loaded library identified
in Plasma, and ordinary-user Wayland pixels, input and bidirectional clipboard
checks passed. Ubuntu's new orchestration, host artifact verification, and real
guest DEB installer also pass; its warm rerun reports no compilation and all
three regression checks pass. Final production integration remains unqualified.
The patched X11 regression does not replace the unpatched negative
control or real ordinary-user Plasma Wayland release qualification.

DEB packaging uses the pinned distro changelog timestamp for both the downstream
entry and `SOURCE_DATE_EPOCH`, not the build's wall clock. Two package operations
in distinct temporary directories produced byte-identical Debian DEBs without
compiling. Corresponding-source bundles also contain execution logs, so their
checksums are deliberately not a reproducible-build assertion.

## Alpine native owner

`sources.lock.json` in this directory pins Alpine 3.24/aarch64 Plasma Workspace
6.6.6-r0, its exact aports recipe commit, distro patch, upstream source and signed
baseline owner APK. These pins deliberately do not live in the shared graphics
source lock. Download hashes have been checked against fetched bytes; the source
SHA512 also matches the pinned aports recipe. `prepare-alpine.py` checks SHA256
and authenticates the baseline APK with the builder's normal Alpine keys. Native
abuild checks the original recipe's source/patch SHA512 values as well.

Alpine owns Klipper in `plasma-workspace-libs`, together with eight other
libraries. A Klipper-only replacement bearing that package name is incomplete.
`alpine.APKBUILD.inc` preserves the distro recipe and builds these nine owned
targets: `batterycontrol`, `kfontinst`, `kfontinstui`, `klipper`, `klookandfeel`,
`kmpris`, `kworkspace`, `notificationmanager`, and `taskmanager`, plus
`klipper-startuprestoretest`. Their normal CMake dependency closure may build
supporting workspace targets. No full-workspace or KWin build is requested.
Only the exact nine real libraries and their SONAME links are staged; the native
`libs` split generates the complete owner APK. Native abuild also generates an
empty parent package as an intermediate; it is never bundled or installed.

`build-alpine.sh ABSOLUTE_OUTPUT` expects a dedicated Alpine 3.24/aarch64 builder
and a stable, distro-specific `SENTINEL_BUILD_WORK` root. Do not change this root
for packaging-only edits: native identities select its subdirectories. It installs build
dependencies into that builder, retaining source, build objects, package cache
and ccache. Optional `SENTINEL_KLIPPER_INPUTS` identifies this packaging directory;
`SENTINEL_KLIPPER_PATCH` identifies the common startup patch when inputs are staged
elsewhere. `build-package-alpine.py` holds a filesystem lock over preparation,
compilation and packaging. Its source identity covers upstream/distro source
pins, preparation code, applied patches and the regression source. Its compiler
identity additionally covers `compile-alpine.sh`, the source epoch, installed
dependencies, compiler/tool binary hashes, configuration and build environment.
Compiler source and objects stay in `compilations/<compile_key>` at stable paths.
The package identity separately covers the complete recipe, normalization and
verification code, revision, baseline pin and public signing key. All identities
and input hashes are retained in corresponding-source `provenance.json`; manifest
keys identify those records and the package remains `qualified: false`.

Each packaging attempt gets a fresh recipe/staging tree whose `src` links to the
locked, matching compiler source tree. It runs only native packaging against
that tree, never unpacking or reapplying patches. Changed normalization/package
code can therefore reuse objects, while source/compiler changes select a fresh
tree. Failed compilation or packaging can resume; interrupted preparation or
an invalid marker fails closed and requires inspecting the specific incomplete
tree before removing it. Legacy caches are left untouched, not relabeled.
All pinned downloads, including the original APKBUILD and distro patch, share a
checksum-verified cache. Package artifacts and provenance are retained under
`packages/<package_key>`; compiler outputs are never normalized in place.
`SENTINEL_BUILD_JOBS` overrides the detected CPU count. A prepared work tree
records its exact compiler identity; invalid markers are rejected before
rewriting prepared sources. Warm runs avoid unconditional APK index updates,
bootstrap installation and build-dependency installation. `python3 test_build_alpine.py`
checks job counts, identity boundaries, the retained-work guard and a mocked
package-only rerun without a VM. Native execution of the split-cache layout has
also passed: changing only normalization preserved the compiler identity and
ran only Ninja's glob check, with no compilation, installation or download.
Restoring the recipe and rerunning passed too.

The candidate uses abuild dependency/provides generation and build-local signing
keys, with command-scoped trust. `verify-alpine.py` checks the full file inventory,
aarch64 shared ELF identity and SONAMEs, regular-file/symlink types and exact
symlink destinations, package origin, license, provides and dependency closure
against the signed baseline. Unexpected differences fail for review. Only the
libraries APK and public key are bundled, alongside the complete corresponding
source archive, original and modified recipes, distro/downstream patches,
regression source, scripts, pins and builder dependency inventory. No original
distro sibling binaries are repackaged.

`python3 test_verify_alpine.py` exercises the library contract using small
APK-style concatenated tar/gzip fixtures and mocked readelf output. It covers
the complete owner, missing/duplicate entries, incorrect file/link types,
dangling or redirected links, foreign ELF architecture, and missing/wrong/duplicate
SONAMEs. These host tests do not establish a successful native package build.

Native qualification so far: the nine-target build, split/sign/dependency and
ELF checks pass. The earlier packaged artifact was installed in an isolated
Alpine VM and its library hash confirmed in ordinary-user plasmashell mappings.
The patched regression passes; the signed baseline fails specifically on the
missing marker. Six Wayland/Xwayland pixel, input and bidirectional clipboard
checks pass across three fresh sessions with resolution changes. The revised
split-cache artifact also passes all six installed-session checks, with its
APK library hash matched to the ordinary-user plasmashell mapping. The script
now requires the regression to pass before packaging, using an ordinary builder
account with private HOME/runtime and D-Bus/Xvfb. It retains the log and its hash
in corresponding source/provenance. This gate passes on the native Alpine lab;
the manifest remains `qualified: false`. Final integration must
exercise startup preservation and explicit history replacement in the standard
matrix. These checks do not prove every queued Wayland serial-ordering race.
The build and provisioning hooks are implemented; final assembled-runtime matrix
qualification remains required before claiming this component release-ready.
