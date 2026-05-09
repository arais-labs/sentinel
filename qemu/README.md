# QEMU Runtime Workspace

Local-first workspace for building and validating a Sentinel runtime base image with QEMU.

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

Main commands:

```bash
./qemu/build-base-image.sh
./qemu/validate-base-image.sh
```

The build is local and self-contained. It does not depend on Multipass internals.

The SSH key written beside the image is local-only and is used for validation and first-boot inspection of the baked image.

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
