# QEMU Runtime Workspace

Local-first workspace for building and validating a Sentinel runtime base image with QEMU.

This backend is currently targeted at macOS on Apple Silicon with Homebrew QEMU. The scripts use:
- `qemu-system-aarch64`
- Apple HVF acceleration
- Homebrew QEMU firmware files
- `hdiutil` for cloud-init ISO creation

Linux support is not wired yet. The build and bridge scripts would need Linux firmware lookup, KVM acceleration, and ISO creation changes before this can be treated as cross-platform.

Current target:
- Debian 12 arm64 cloud image
- KDE desktop
- noVNC / x11vnc
- Playwright Chromium baked into `/opt/google/chrome`
- base dev tools: `git`, `ripgrep`, `curl`, `wget`, `jq`, `tree`, `htop`, `python3`, `pip`, `nodejs`, `npm`
- shared-runtime ready guest helpers for per-session users and workspace mounts:
  - `/usr/local/bin/sentinel-session-prepare.sh`
  - `/usr/local/bin/sentinel-session-cleanup.sh`

Output:
- `qemu/output/sentinel-runtime-base-arm64.qcow2`
- `qemu/output/sentinel-runtime-base-arm64.id_ed25519`
- `qemu/output/sentinel-runtime-base-arm64.id_ed25519.pub`

These output files are local build artifacts and are intentionally ignored by git. A new machine must build them before selecting the QEMU runtime backend.

Prerequisites:
- Homebrew
- QEMU installed through Homebrew: `brew install qemu`
- GNU coreutils for `sha512sum`: `brew install coreutils`
- macOS command line tools for `ssh`, `ssh-keygen`, `curl`, and `python3`

Main commands:

```bash
./qemu/build-base-image.sh
./qemu/validate-base-image.sh
```

After the image validates, select the QEMU runtime backend from `./sentinel-cli.sh` instance config. The CLI expects the image/key under `qemu/output/` and writes the required `RUNTIME_QEMU_*` values into the selected instance `.env`.

The SSH key written beside the image is local-only and is used for validation and first-boot inspection of the baked image.

Runtime shape:
- one shared local QEMU VM per Sentinel instance
- per-session Unix users inside the VM
- per-session browser profiles
- per-session workspace directories
- host workspaces mounted into the VM through QEMU virtfs

Guest layout:
- runtime account: `sentinel`
- default desktop workspace: `/srv/sentinel/default-workspace`
- per-session roots: `/srv/sentinel/sessions/<session-id>`
- per-session prepare/cleanup scripts manage:
  - isolated Unix user
  - isolated home
  - isolated browser profile
  - isolated workspace path
  - optional bind-mounted external workspace source
