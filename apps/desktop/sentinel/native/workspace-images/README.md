# Native workspace images

These OCI roots contain the operating-system service layer, separately from
desktop/application selection. Alpine 3.24 boots OpenRC; Ubuntu 26.04 and Debian
13 boot systemd. Docker and Kubernetes are optional tools, not base-image
packages or boot prerequisites. DBus and native
login/PAM packages are installed at image build, not by a boot polling script.
Machine identity is initialized per workspace by systemd or the native OpenRC
DBus service; the builder's identity is not retained. Containerization owns the
external network interface; Alpine's networking service configures loopback.
The early `sentinel-runtime-mounts` service makes the VM-owned `/run` recursively
shared before login/application services start. This lets native user-runtime
mounts reach confined child namespaces created before login (including Snap).
It does not share any mount namespace with macOS or other workspaces.

Ubuntu and Debian install the distro AppArmor parser. `sentinel-apparmor` loads
unchanged system profiles before native init's ordinary services: stock distro
helpers intentionally skip unrecognized container environments, even though
each Sentinel workspace owns a dedicated VM kernel. Ubuntu's Snap service
drop-ins also restore generated profiles after Snap mounts are ready and prevent
snapd starting when policy loading fails. The parser retains distro filtering,
disabled-profile conventions and caches; Snap owns policy generation/refresh.
Alpine has no dependency on this systemd/AppArmor integration. This design must
not be reused for multiple tenants sharing one kernel. The image marker checked
by the loader is a packaging guard, not a security boundary.

Qualify this lifecycle with a complete stop/start of the same workspace VM,
without reinstalling packages or manually reloading policy. Verify the native
and Snap policy services ran on the new boot, Chromium and snap-confine profiles
are loaded in enforce mode, and the ordinary-user browser starts confined.
Service exit status alone is insufficient: stock helpers can succeed while
skipping their work. Also exercise runtime exec and shutdown after policy loads.

The `sentinel-wayland` and `sentinel-x11` PAM services establish the session
protocol through stock `pam_env` before including the distribution's `greetd`
session stack. Authentication and account policy remain distribution-owned;
the runtime selects the service matching the requested graphical session.

Build locally with an explicitly configured Docker/buildx daemon:

```sh
python3 scripts/packaging/workspace-images/build.py /tmp/sentinel-workspace-images
```

Use `--distribution alpine` to build just one fresh test artifact. The builder
does not start Docker Desktop, provision a VM, push a registry, replace existing
output, or touch workspace disks. A disposable Sentinel build VM can expose its
Docker API to the same CLI through `DOCKER_HOST`. BuildKit's OCI exporter is
required. Build logs are streamed; a failed build publishes no final manifest.

Without a host Docker daemon, use the dedicated persistent Sentinel builder:

```sh
python3 scripts/packaging/workspace-images/build-vm.py build/workspace-images/my-build
```

It snapshots the packaged helper, stages only these recipes and the builder,
and runs Docker/buildx inside its own build VM only; these build tools are not
installed into the resulting workspace images. Each completed distribution publishes
`shared/artifacts/<distribution>/manifest.json` independently. The owned VM is
stopped on completion or failure, not deleted. Its OS disk, installed tools and
BuildKit layers remain under `build/workspace-image-builders/`; an exclusive
lock prevents concurrent mutation. The pinned build image, init image and kernel
bytes determine VM identity, not source edits or output paths. Each run still
uses a fresh staging/output directory so failed output cannot masquerade as a
new artifact. The builder uses available CPU cores, capped at the runtime's 32.

All roots include an ordinary `sentinel` user with sudo root access. Desktop and
automation processes use that account; root remains the provisioning supervisor.

The output contains `manifest.json` and one OCI layout directory per requested
distribution. `images.<distribution>.reference` names an actual SHA-256 index;
`digest` identifies the same index blob. All image, config and layer descriptors
are checked before publication. Import the selected layout with
`ImageStore.load(from:)`; do not pull the local `sentinel.local` reference.

`boot_contract: 1` means the root contains the native-init baseline. Existing
workspace disks are not migrated by this builder. Runtime integration must
validate the disk's boot contract and require explicit recreation when absent.
Private compiler/build images are separate inputs and are not these roots.

Pinned bases and recorded installed package inventories make provenance
inspectable. Package repositories still advance; a source key is **not** a
promise of byte-reproducible package resolution. Only the generated OCI digest
identifies a completed artifact. Native init, cgroup delegation, device/seat
ownership and desktop sessions must still pass actual VM qualification;
successful image construction alone does not establish desktop support.
